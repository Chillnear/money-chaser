from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("backtest_module_for_gf", SCRIPTS_DIR / "backtest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass ที่ใช้ `from __future__ import annotations` ต้อง resolve ผ่าน sys.modules ได้
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rb(bt):
    spec = importlib.util.spec_from_file_location("rule_backtest_for_gf", SCRIPTS_DIR / "rule_backtest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gf(bt, rb):
    spec = importlib.util.spec_from_file_location("backtest_grid_farming_under_test", SCRIPTS_DIR / "backtest_grid_farming.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_calendar_candles(start_date: str, n_days: int, start_price: float = 100.0, daily_pcts: list[float] | None = None) -> list[dict]:
    base = dt.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    candles = []
    price = start_price
    for i in range(n_days):
        pct = daily_pcts[i % len(daily_pcts)] if daily_pcts else 0.0
        open_price = price
        close_price = price * (1 + pct / 100)
        day_start_ms = int((base + dt.timedelta(days=i)).timestamp() * 1000)
        day_end_ms = int((base + dt.timedelta(days=i + 1)).timestamp() * 1000)
        candles.append({
            "t": day_start_ms, "T": day_end_ms, "o": open_price,
            "h": max(open_price, close_price) * 1.03, "l": min(open_price, close_price) * 0.97,
            "c": close_price, "v": 1_000_000.0,
        })
        price = close_price
    return candles


def _settings():
    from src.settings import AppConfig, RiskConfig, Secrets, Settings
    return Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )


def test_grid_backtest_opens_and_tracks_equity_on_volatile_market(bt, gf):
    settings = _settings()
    candles = _make_calendar_candles("2025-01-01", n_days=50, daily_pcts=[8.0, -8.0])  # สลับขึ้นลงแรงทุกวัน
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": []})

    result = gf.run_grid_backtest(settings, hist_client, "BTC", "2025-01-15", "2025-02-10", starting_equity_usd=28.0)

    assert result["grids_opened"] > 0  # volatility สูงพอควรเปิด grid อย่างน้อยครั้งนึง
    assert len(result["equity_curve"]) > 0
    assert result["final_equity_usd"] > 0


def test_grid_backtest_stays_flat_on_calm_market(bt, gf):
    settings = _settings()
    candles = _make_calendar_candles("2025-01-01", n_days=50, daily_pcts=[0.05, -0.03])  # นิ่งมาก
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": []})

    result = gf.run_grid_backtest(settings, hist_client, "BTC", "2025-01-15", "2025-02-10", starting_equity_usd=28.0)

    assert result["grids_opened"] == 0
    assert result["final_equity_usd"] == 28.0


def test_funding_farmer_backtest_opens_and_collects_on_high_funding(bt, gf):
    settings = _settings()
    candles = _make_calendar_candles("2025-01-01", n_days=50, daily_pcts=[0.0])
    funding_history = [
        {"time": bt.date_to_ms("2025-01-01") + i * 3_600_000, "fundingRate": "0.001"}  # 0.1%/8h -> ~109%/ปี
        for i in range(50 * 24)
    ]
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": funding_history})

    # หมายเหตุ: ใช้ 100 USD แทน 28 USD ที่ระบบจริงใช้ตอนนี้ เพราะพบว่า FundingFarmingAgent ปัจจุบัน
    # sizing แบบ "ใช้ 50% ของทุน" ชนขั้นต่ำ $20 ไม่ผ่านที่ 28 USD เลย (28*0.5=14 < 20) — เป็นข้อจำกัดจริง
    # ของ agent ตัวนี้ที่ต้องรู้ไว้: ด้วยทุนปัจจุบันของระบบจริง (28 USD) กลยุทธ์นี้เปิดไม้ไม่ได้เลยแม้ funding จะดีแค่ไหน
    result = gf.run_funding_farmer_backtest(settings, hist_client, "BTC", "2025-01-15", "2025-02-10", starting_equity_usd=100.0)

    assert result["positions_opened"] > 0
    assert result["final_equity_usd"] > 100.0  # funding เป็นบวกต่อเนื่อง ควรเก็บกำไรได้


def test_funding_farmer_backtest_stays_flat_on_low_funding(bt, gf):
    settings = _settings()
    candles = _make_calendar_candles("2025-01-01", n_days=50, daily_pcts=[0.0])
    funding_history = [
        {"time": bt.date_to_ms("2025-01-01") + i * 3_600_000, "fundingRate": "0.00001"}  # ต่ำกว่าเกณฑ์ 10%/ปี มาก
        for i in range(50 * 24)
    ]
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": funding_history})

    result = gf.run_funding_farmer_backtest(settings, hist_client, "BTC", "2025-01-15", "2025-02-10", starting_equity_usd=28.0)

    assert result["positions_opened"] == 0
    assert result["final_equity_usd"] == 28.0

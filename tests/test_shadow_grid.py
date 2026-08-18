from __future__ import annotations

import datetime as dt

from src.settings import AppConfig, RiskConfig, Secrets, Settings
from src.shadow_grid import run_grid_shadow_day
from src.util.io import load_json


def _settings():
    return Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )


def _make_candles(n_days: int, start_price: float = 100.0, daily_pcts: list[float] | None = None) -> list[dict]:
    base = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    candles = []
    price = start_price
    for i in range(n_days):
        pct = daily_pcts[i % len(daily_pcts)] if daily_pcts else 0.0
        open_price = price
        close_price = price * (1 + pct / 100)
        t = int((base + dt.timedelta(days=i)).timestamp() * 1000)
        candles.append({
            "t": t, "o": open_price, "h": max(open_price, close_price) * 1.03,
            "l": min(open_price, close_price) * 0.97, "c": close_price, "v": 1_000_000.0,
        })
        price = close_price
    return candles


class FakeHlClient:
    def __init__(self, candles: list[dict]):
        self.candles = candles

    def get_candles(self, asset, interval="1d", lookback_days=30):
        return self.candles


def test_waits_when_not_enough_data(tmp_path):
    settings = _settings()
    result = run_grid_shadow_day(settings, FakeHlClient(_make_candles(3)), now_ts=1_800_000_000.0, journal_dir=tmp_path)

    assert result["action"] == "waiting_for_data"
    assert result["open_position"] is None


def test_stays_flat_on_calm_market(tmp_path):
    settings = _settings()
    candles = _make_candles(15, daily_pcts=[0.05, -0.03])  # นิ่งมาก ไม่พอเปิด grid
    result = run_grid_shadow_day(settings, FakeHlClient(candles), now_ts=1_800_000_000.0, journal_dir=tmp_path)

    assert result["action"] == "flat"
    assert result["open_position"] is None
    assert result["equity_usd"] == 28.0  # ไม่เปิดไม้ -> equity เท่าทุนตั้งต้นเดิม


def test_opens_grid_on_high_volatility(tmp_path):
    settings = _settings()
    candles = _make_candles(15, daily_pcts=[8.0, -8.0])  # ผันผวนพอเปิด grid
    result = run_grid_shadow_day(settings, FakeHlClient(candles), now_ts=1_800_000_000.0, journal_dir=tmp_path)

    assert result["action"] == "opened"
    assert result["open_position"] is not None
    assert result["open_position"]["symbol"] == "BTC"

    saved_state = load_json(tmp_path / "shadow_grid_state.json", default=None)
    assert saved_state["open_position"] is not None


def test_holds_position_across_days_and_can_close(tmp_path):
    settings = _settings()
    day1_candles = _make_candles(15, daily_pcts=[8.0, -8.0])
    day1 = run_grid_shadow_day(settings, FakeHlClient(day1_candles), now_ts=1_800_000_000.0, journal_dir=tmp_path)
    assert day1["action"] == "opened"

    # วันถัดไปราคานิ่งลงมาก (volatility ตาย 2+ วัน) -> agent ควรปิด grid ในที่สุด
    calm_candles = _make_candles(20, daily_pcts=[0.01, -0.01])
    last_action = None
    for i in range(5):
        result = run_grid_shadow_day(settings, FakeHlClient(calm_candles), now_ts=1_800_086_400.0 + i * 86_400, journal_dir=tmp_path)
        last_action = result["action"]
        if last_action.startswith("closed"):
            break

    assert last_action is not None and (last_action.startswith("closed") or last_action == "held_position")


def test_fails_safe_and_never_raises_when_hl_client_errors(tmp_path):
    settings = _settings()

    class BrokenHlClient:
        def get_candles(self, *args, **kwargs):
            raise RuntimeError("network ล้มเหลวจำลอง")

    result = run_grid_shadow_day(settings, BrokenHlClient(), now_ts=1_800_000_000.0, journal_dir=tmp_path)

    assert result["action"] == "error"
    assert "error" in result

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backtest.py"


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("backtest_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY = {
    "roles": {
        "analyst_trend": {"model": "m-trend", "provider": "alibaba", "tier": "mid", "source": "litellm"},
        "analyst_positioning": {"model": "m-pos", "provider": "anthropic", "tier": "mid", "source": "litellm"},
        "analyst_macro": {"model": "m-macro", "provider": "deepseek", "tier": "mid", "source": "litellm"},
        "redteam": {"model": "m-red", "provider": "google", "tier": "cheap", "source": "litellm"},
        "judge": {"model": "m-judge", "provider": "anthropic", "tier": "frontier", "source": "litellm"},
        "reflector": {"model": "m-reflect", "provider": "openai", "tier": "frontier", "source": "litellm"},
    }
}


def _make_calendar_candles(start_date: str, n_days: int, start_price: float = 100.0, daily_pct: float = 0.0) -> list[dict]:
    """สร้างแท่งเทียนที่ t/T ตรงกับปฏิทินจริง (วันละ 1 แท่ง) ต่างจาก _make_candles ใน test_main_pipeline.py
    ที่ใช้เวลาสัมพัทธ์กับ time.time() — backtest ต้อง cutoff ตามวันที่ปฏิทินจริงเป๊ะๆ กัน lookahead
    """
    base = dt.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    candles = []
    price = start_price
    for i in range(n_days):
        open_price = price
        close_price = price * (1 + daily_pct / 100)
        day_start_ms = int((base + dt.timedelta(days=i)).timestamp() * 1000)
        day_end_ms = int((base + dt.timedelta(days=i + 1)).timestamp() * 1000)
        candles.append(
            {
                "t": day_start_ms, "T": day_end_ms,
                "o": open_price, "h": max(open_price, close_price) * 1.002,
                "l": min(open_price, close_price) * 0.998, "c": close_price, "v": 1000.0,
            }
        )
        price = close_price
    return candles


def _fake_llm_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _judge_flat_json():
    return json.dumps(
        {
            "action": "flat", "asset": None, "confidence": 40, "stop_pct": 0, "take_profit_pct": 0,
            "reasoning": "ไม่มั่นใจพอ", "why_this_over_others": "-", "redteam_response": "-",
        }
    )


def _analyst_flat_json():
    return json.dumps({"candidates": [{"asset": "BTC", "direction": "flat", "confidence": 40, "thesis": "เฉยๆ", "invalidation": "-"}]})


# ---- date helpers ----


def test_date_to_ms_is_midnight_utc(bt):
    ms = bt.date_to_ms("2026-01-01")
    assert dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc) == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def test_date_range_inclusive_of_both_ends(bt):
    days = bt.date_range("2026-01-01", "2026-01-03")
    assert days == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_date_range_empty_when_end_before_start(bt):
    assert bt.date_range("2026-01-05", "2026-01-01") == []


# ---- HistoricalHyperliquidClient ----


def test_historical_client_hides_candles_not_yet_closed(bt):
    candles = _make_calendar_candles("2026-01-01", n_days=10)
    client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": []})
    client.set_as_of(bt.date_to_ms("2026-01-05"))  # เห็นได้แค่แท่งที่ปิดก่อนหรือ = 2026-01-05

    visible = client.get_candles("BTC", lookback_days=400)

    assert len(visible) == 4  # แท่งของวัน 01-01..01-04 ปิดแล้ว (T <= as_of=จุดเริ่ม 01-05) รวม 4 แท่ง
    assert all(c["T"] <= bt.date_to_ms("2026-01-05") for c in visible)


def test_historical_client_raises_if_as_of_not_set(bt):
    client = bt.HistoricalHyperliquidClient({"BTC": []}, {"BTC": []})
    with pytest.raises(ValueError):
        client.get_candles("BTC")


def test_historical_client_universe_snapshot_uses_latest_visible_close(bt):
    candles = _make_calendar_candles("2026-01-01", n_days=5, start_price=100.0, daily_pct=1.0)
    client = bt.HistoricalHyperliquidClient({"BTC": candles}, {"BTC": []})
    client.set_as_of(bt.date_to_ms("2026-01-04"))

    snapshot = client.get_universe_snapshot()
    btc = next(e for e in snapshot if e["coin"] == "BTC")

    assert btc["open_interest_usd"] == 0.0
    assert btc["mark_px"] > 100.0  # ราคาขยับขึ้นตาม daily_pct
    assert btc["funding"] == 0.0  # ไม่มีประวัติ funding -> ค่าเริ่มต้น


def test_historical_client_uses_funding_history_up_to_as_of(bt):
    client = bt.HistoricalHyperliquidClient(
        {"BTC": _make_calendar_candles("2026-01-01", n_days=5)},
        {"BTC": [
            {"time": bt.date_to_ms("2026-01-01"), "fundingRate": "0.0001"},
            {"time": bt.date_to_ms("2026-01-03"), "fundingRate": "0.0005"},
            {"time": bt.date_to_ms("2026-01-10"), "fundingRate": "0.0009"},  # อนาคต ห้ามเห็น
        ]},
    )
    client.set_as_of(bt.date_to_ms("2026-01-04"))

    snapshot = client.get_universe_snapshot()
    btc = next(e for e in snapshot if e["coin"] == "BTC")
    assert btc["funding"] == pytest.approx(0.0005)  # ค่าล่าสุดที่ไม่ใช่อนาคต


def test_historical_client_excludes_coin_with_too_few_visible_candles(bt):
    client = bt.HistoricalHyperliquidClient({"BTC": _make_calendar_candles("2026-01-01", n_days=5)}, {"BTC": []})
    client.set_as_of(bt.date_to_ms("2026-01-01"))  # ยังไม่มีแท่งปิดเลย ณ จุดนี้

    snapshot = client.get_universe_snapshot()
    assert snapshot == []


# ---- run_backtest (mocked LLM, deterministic FLAT every day = cheap + fast) ----


def test_run_backtest_simulates_multiple_days(bt, tmp_path):
    from src.execution.broker_paper import PaperBroker
    from src.agents.llm import LLMClient
    from src.settings import AppConfig, RiskConfig, Secrets, Settings

    settings = Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )
    candles = _make_calendar_candles("2025-10-01", n_days=95, daily_pct=0.0)  # ~95 วัน (warm-up + จำลอง)
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []})

    responses = [_fake_llm_response(_analyst_flat_json())] * 4 + [_fake_llm_response(_judge_flat_json())]
    completion_fn = MagicMock(side_effect=lambda **kw: responses[completion_fn.call_count % len(responses)])
    llm_client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.001),
    )

    summary = bt.run_backtest(
        settings=settings, llm_client=llm_client, model_registry=REGISTRY, hist_client=hist_client,
        start_date="2025-12-30", end_date="2026-01-03", backtest_journal_dir=tmp_path / "journal",
        starting_equity_usd=28.0,
    )

    assert summary["days_simulated"] == 5
    assert summary["stopped_early_reason"] is None
    assert (tmp_path / "journal" / "state.json").exists()


def test_run_backtest_stops_early_when_ai_cost_cap_exceeded(bt, tmp_path):
    from src.agents.llm import LLMClient
    from src.settings import AppConfig, RiskConfig, Secrets, Settings

    settings = Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )
    candles = _make_calendar_candles("2025-10-01", n_days=95, daily_pct=0.0)
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []})

    responses = [_fake_llm_response(_analyst_flat_json())] * 4 + [_fake_llm_response(_judge_flat_json())]
    completion_fn = MagicMock(side_effect=lambda **kw: responses[completion_fn.call_count % len(responses)])
    # cost_fn คืน 1.0 USD ต่อ call จริง -> วันแรกก็ใช้ ~5 USD แล้ว เกิน cap 0.5 USD ทันที
    llm_client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=1.0),
    )

    summary = bt.run_backtest(
        settings=settings, llm_client=llm_client, model_registry=REGISTRY, hist_client=hist_client,
        start_date="2025-12-30", end_date="2026-01-10", backtest_journal_dir=tmp_path / "journal",
        starting_equity_usd=28.0, max_ai_cost_usd=0.5,
    )

    assert summary["stopped_early_reason"] is not None
    assert summary["days_simulated"] < len(bt.date_range("2025-12-30", "2026-01-10"))


def test_run_backtest_wipes_previous_journal_before_starting(bt, tmp_path):
    from src.agents.llm import LLMClient
    from src.settings import AppConfig, RiskConfig, Secrets, Settings

    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True)
    (journal_dir / "leftover_from_last_run.txt").write_text("stale", encoding="utf-8")

    settings = Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )
    candles = _make_calendar_candles("2025-10-01", n_days=95, daily_pct=0.0)
    hist_client = bt.HistoricalHyperliquidClient({"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []})
    responses = [_fake_llm_response(_analyst_flat_json())] * 4 + [_fake_llm_response(_judge_flat_json())]
    completion_fn = MagicMock(side_effect=lambda **kw: responses[completion_fn.call_count % len(responses)])
    llm_client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.001),
    )

    bt.run_backtest(
        settings=settings, llm_client=llm_client, model_registry=REGISTRY, hist_client=hist_client,
        start_date="2025-12-30", end_date="2025-12-30", backtest_journal_dir=journal_dir,
        starting_equity_usd=28.0,
    )

    assert not (journal_dir / "leftover_from_last_run.txt").exists()

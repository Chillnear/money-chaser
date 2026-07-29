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
    spec = importlib.util.spec_from_file_location("backtest_module_for_rule", SCRIPTS_DIR / "backtest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass ที่ใช้ `from __future__ import annotations` ต้อง resolve ผ่าน sys.modules ได้
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rb(bt):
    spec = importlib.util.spec_from_file_location("rule_backtest_under_test", SCRIPTS_DIR / "rule_backtest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_calendar_candles(start_date: str, n_days: int, start_price: float = 100.0, daily_pct: float = 0.0) -> list[dict]:
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
                "o": open_price, "h": max(open_price, close_price) * 1.005,
                "l": min(open_price, close_price) * 0.995, "c": close_price, "v": 1000.0,
            }
        )
        price = close_price
    return candles


def _settings():
    from src.settings import AppConfig, RiskConfig, Secrets, Settings

    settings = Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )
    mode_defaults = settings.risk.mode_defaults.model_copy(update={"min_24h_volume_usd": 0.0, "min_open_interest_usd": 0.0})
    risk = settings.risk.model_copy(update={"mode_defaults": mode_defaults})
    return settings.model_copy(update={"risk": risk})


# ---- decide_trend_following ----


def test_decide_trend_following_goes_long_on_trend_up(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    regime_by_coin = {"BTC": {"trend": "trend_up", "vol": "vol_mid", "tag": "trend_up_vol_mid"}}
    decision = rb.decide_trend_following(shortlist, regime_by_coin, {}, [])
    assert decision.action == "long"
    assert decision.asset == "BTC"


def test_decide_trend_following_flat_on_chop(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    regime_by_coin = {"BTC": {"trend": "chop", "vol": "vol_mid", "tag": "chop_vol_mid"}}
    decision = rb.decide_trend_following(shortlist, regime_by_coin, {}, [])
    assert decision.action == "flat"


# ---- decide_mean_reversion ----


def test_decide_mean_reversion_shorts_when_overbought_at_top(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    price_features_by_coin = {"BTC": {"donchian_position": 0.95, "rsi": 70.0}}
    decision = rb.decide_mean_reversion(shortlist, {}, price_features_by_coin, [])
    assert decision.action == "short"
    assert decision.asset == "BTC"


def test_decide_mean_reversion_longs_when_oversold_at_bottom(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    price_features_by_coin = {"BTC": {"donchian_position": 0.05, "rsi": 30.0}}
    decision = rb.decide_mean_reversion(shortlist, {}, price_features_by_coin, [])
    assert decision.action == "long"


def test_decide_mean_reversion_flat_when_not_extreme(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    price_features_by_coin = {"BTC": {"donchian_position": 0.5, "rsi": 50.0}}
    decision = rb.decide_mean_reversion(shortlist, {}, price_features_by_coin, [])
    assert decision.action == "flat"


def test_decide_mean_reversion_flat_when_data_missing(rb):
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    decision = rb.decide_mean_reversion(shortlist, {}, {"BTC": {}}, [])
    assert decision.action == "flat"


# ---- decide_funding_carry ----


def test_decide_funding_carry_shorts_on_extreme_positive_funding(rb):
    shortlist = [{"coin": "BTC", "funding_score": 0.95}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.001}]
    decision = rb.decide_funding_carry(shortlist, {}, {}, universe_snapshot)
    assert decision.action == "short"
    assert decision.asset == "BTC"


def test_decide_funding_carry_longs_on_extreme_negative_funding(rb):
    shortlist = [{"coin": "BTC", "funding_score": 0.02}]
    universe_snapshot = [{"coin": "BTC", "funding": -0.001}]
    decision = rb.decide_funding_carry(shortlist, {}, {}, universe_snapshot)
    assert decision.action == "long"


def test_decide_funding_carry_flat_when_not_extreme(rb):
    shortlist = [{"coin": "BTC", "funding_score": 0.55}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.0001}]
    decision = rb.decide_funding_carry(shortlist, {}, {}, universe_snapshot)
    assert decision.action == "flat"


# ---- run_rule_backtest integration ----


def test_run_rule_backtest_produces_consistent_summary_in_uptrend(rb, bt):
    settings = _settings()
    candles = _make_calendar_candles("2024-01-01", n_days=500, start_price=100.0, daily_pct=0.4)
    hist_client = bt.HistoricalHyperliquidClient(
        {"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []}
    )

    summary = rb.run_rule_backtest(
        settings=settings, hist_client=hist_client, coins=["BTC", "PAXG"],
        start_date="2025-04-01", end_date="2025-05-10",
        starting_equity_usd=28.0, decide_fn=rb.decide_trend_following,
    )

    assert set(["trades", "win_rate_pct", "starting_equity_usd", "final_equity_usd", "total_pnl_usd", "trade_list"]) <= set(summary.keys())
    assert 0.0 <= summary["win_rate_pct"] <= 100.0
    assert round(summary["starting_equity_usd"] + summary["total_pnl_usd"], 2) == summary["final_equity_usd"]
    for trade in summary["trade_list"]:
        assert trade["asset"] in ("BTC", "PAXG")
        assert trade["side"] in ("long", "short")


def test_run_rule_backtest_flat_strategy_never_trades(rb, bt):
    settings = _settings()
    candles = _make_calendar_candles("2024-01-01", n_days=500, start_price=100.0, daily_pct=0.0)
    hist_client = bt.HistoricalHyperliquidClient(
        {"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []}
    )

    def _always_flat(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot):
        return rb.Decision("flat", None, "test always flat")

    summary = rb.run_rule_backtest(
        settings=settings, hist_client=hist_client, coins=["BTC", "PAXG"],
        start_date="2025-04-01", end_date="2025-04-10",
        starting_equity_usd=28.0, decide_fn=_always_flat,
    )

    assert summary["trades"] == 0
    assert summary["final_equity_usd"] == 28.0

from __future__ import annotations

import pytest

from src.baseline import BASELINE_CONFIDENCE, BaselineDecision, decide


def test_decide_flat_when_shortlist_empty():
    result = decide(shortlist=[], regime_by_coin={}, default_stop_pct=3.0, default_take_profit_pct=6.0)
    assert result.action == "flat"
    assert result.asset is None
    assert result.confidence == 0.0


def test_decide_long_when_top_composite_is_trend_up():
    shortlist = [{"coin": "BTC", "composite": 0.8}, {"coin": "PAXG", "composite": 0.3}]
    regime_by_coin = {"BTC": {"trend": "trend_up", "vol": "vol_mid", "tag": "trend_up_vol_mid"}}

    result = decide(shortlist, regime_by_coin, default_stop_pct=3.0, default_take_profit_pct=6.0)

    assert result.action == "long"
    assert result.asset == "BTC"
    assert result.confidence == BASELINE_CONFIDENCE
    assert result.stop_pct == 3.0
    assert result.take_profit_pct == 6.0


def test_decide_short_when_top_composite_is_trend_down():
    shortlist = [{"coin": "ETH", "composite": 0.9}]
    regime_by_coin = {"ETH": {"trend": "trend_down", "vol": "vol_low", "tag": "trend_down_vol_low"}}

    result = decide(shortlist, regime_by_coin, default_stop_pct=2.5, default_take_profit_pct=5.0)

    assert result.action == "short"
    assert result.asset == "ETH"


def test_decide_flat_when_top_composite_is_chop():
    shortlist = [{"coin": "SOL", "composite": 0.7}]
    regime_by_coin = {"SOL": {"trend": "chop", "vol": "vol_mid", "tag": "chop_vol_mid"}}

    result = decide(shortlist, regime_by_coin, default_stop_pct=3.0, default_take_profit_pct=6.0)

    assert result.action == "flat"
    assert result.asset is None
    assert result.stop_pct == 0.0


def test_decide_picks_highest_composite_not_first_in_list():
    shortlist = [
        {"coin": "LOW", "composite": 0.1},
        {"coin": "HIGH", "composite": 0.95},
        {"coin": "MID", "composite": 0.5},
    ]
    regime_by_coin = {
        "LOW": {"trend": "trend_up"},
        "HIGH": {"trend": "trend_up"},
        "MID": {"trend": "trend_up"},
    }

    result = decide(shortlist, regime_by_coin, default_stop_pct=3.0, default_take_profit_pct=6.0)

    assert result.asset == "HIGH"


def test_decide_missing_regime_for_top_coin_defaults_to_chop_and_flat():
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    result = decide(shortlist, regime_by_coin={}, default_stop_pct=3.0, default_take_profit_pct=6.0)

    assert result.action == "flat"


def test_decide_returns_baseline_decision_dataclass():
    shortlist = [{"coin": "BTC", "composite": 0.8}]
    regime_by_coin = {"BTC": {"trend": "trend_up"}}
    result = decide(shortlist, regime_by_coin, default_stop_pct=3.0, default_take_profit_pct=6.0)
    assert isinstance(result, BaselineDecision)

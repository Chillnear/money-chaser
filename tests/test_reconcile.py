from __future__ import annotations

from src.execution.reconcile import reconcile_all, reconcile_equity, reconcile_position

BTC_LONG = {"asset": "BTC", "side": "long", "notional_usd": 100.0, "entry_price": 65000.0}


def test_reconcile_position_both_none_matches():
    assert reconcile_position(None, None).matched is True


def test_reconcile_position_mismatch_one_side_has_position():
    result = reconcile_position(BTC_LONG, None)
    assert result.matched is False
    result2 = reconcile_position(None, BTC_LONG)
    assert result2.matched is False


def test_reconcile_position_mismatch_different_asset():
    other = dict(BTC_LONG, asset="ETH")
    result = reconcile_position(BTC_LONG, other)
    assert result.matched is False
    assert "asset" in result.reason


def test_reconcile_position_mismatch_different_side():
    other = dict(BTC_LONG, side="short")
    result = reconcile_position(BTC_LONG, other)
    assert result.matched is False
    assert "side" in result.reason


def test_reconcile_position_matches_within_notional_tolerance():
    broker_side = dict(BTC_LONG, notional_usd=102.0)  # ต่างกัน 2%, tolerance default 5%
    result = reconcile_position(BTC_LONG, broker_side)
    assert result.matched is True


def test_reconcile_position_fails_outside_notional_tolerance():
    broker_side = dict(BTC_LONG, notional_usd=120.0)  # ต่างกัน 20%
    result = reconcile_position(BTC_LONG, broker_side, notional_tolerance_pct=5.0)
    assert result.matched is False


def test_reconcile_equity_within_tolerance():
    result = reconcile_equity(journal_equity=100.0, broker_equity=100.5, tolerance_pct=1.0)
    assert result.matched is True


def test_reconcile_equity_outside_tolerance():
    result = reconcile_equity(journal_equity=100.0, broker_equity=110.0, tolerance_pct=1.0)
    assert result.matched is False


def test_reconcile_equity_invalid_journal_value():
    result = reconcile_equity(journal_equity=0.0, broker_equity=50.0)
    assert result.matched is False


def test_reconcile_all_passes_when_both_match():
    result = reconcile_all(
        journal_position=BTC_LONG,
        broker_position=dict(BTC_LONG),
        journal_equity=100.0,
        broker_equity=100.2,
    )
    assert result.matched is True


def test_reconcile_all_fails_on_position_mismatch_before_checking_equity():
    result = reconcile_all(
        journal_position=BTC_LONG,
        broker_position=None,
        journal_equity=100.0,
        broker_equity=100.0,
    )
    assert result.matched is False
    assert "position" in result.reason.lower() or "broker" in result.reason.lower()


def test_reconcile_all_fails_on_equity_mismatch_when_position_ok():
    result = reconcile_all(
        journal_position=None,
        broker_position=None,
        journal_equity=100.0,
        broker_equity=150.0,
    )
    assert result.matched is False

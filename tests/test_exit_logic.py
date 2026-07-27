from __future__ import annotations

from src.risk.exit_rules import classify_exit, should_force_time_exit

DAY = 86400.0


def test_should_force_time_exit_true_when_exceeded():
    opened = 0.0
    now = 6 * DAY
    assert should_force_time_exit(opened, now, max_holding_days=5) is True


def test_should_force_time_exit_false_when_within_window():
    opened = 0.0
    now = 3 * DAY
    assert should_force_time_exit(opened, now, max_holding_days=5) is False


def test_should_force_time_exit_false_on_bad_timestamps():
    assert should_force_time_exit(opened_at_ts=1000.0, now_ts=500.0, max_holding_days=5) is False


def test_classify_exit_sl_takes_priority():
    result = classify_exit(sl_hit=True, tp_hit=True, opened_at_ts=0.0, now_ts=DAY, max_holding_days=5, invalidation_triggered=True)
    assert result.should_exit is True
    assert result.reason == "stop_loss_hit"


def test_classify_exit_tp_when_no_sl():
    result = classify_exit(sl_hit=False, tp_hit=True, opened_at_ts=0.0, now_ts=DAY, max_holding_days=5)
    assert result.reason == "take_profit_hit"


def test_classify_exit_time_based_when_no_sl_tp():
    result = classify_exit(sl_hit=False, tp_hit=False, opened_at_ts=0.0, now_ts=6 * DAY, max_holding_days=5)
    assert result.should_exit is True
    assert result.reason == "max_holding_days_exceeded"


def test_classify_exit_invalidation_when_nothing_else_triggered():
    result = classify_exit(
        sl_hit=False, tp_hit=False, opened_at_ts=0.0, now_ts=2 * DAY, max_holding_days=5, invalidation_triggered=True
    )
    assert result.should_exit is True
    assert result.reason == "thesis_invalidated"


def test_classify_exit_no_exit_when_nothing_triggered():
    result = classify_exit(sl_hit=False, tp_hit=False, opened_at_ts=0.0, now_ts=2 * DAY, max_holding_days=5)
    assert result.should_exit is False
    assert result.reason is None

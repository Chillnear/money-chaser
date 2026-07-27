from __future__ import annotations

from src.risk.breaker import (
    CONSECUTIVE_LOSS_PAUSE_HOURS,
    BreakerState,
    apply_daily_breaker,
    apply_trade_result,
    apply_weekly_breaker,
    clear_pause,
    compute_drawdown_pct,
    is_killed,
    is_paused,
    should_pause_daily,
    should_pause_weekly,
    should_trigger_kill,
    size_multiplier,
    write_kill_file,
)


def test_consecutive_loss_streak_also_pauses_trading_for_one_day():
    # แนวคิดจาก playbook: แพ้ติดกันครบเกณฑ์ ต้องหยุดเทรดจริง ไม่ใช่แค่ลดขนาดไม้แล้วไล่แก้ตัวต่อทันที
    now_ts = 1_000_000.0
    state = BreakerState(consecutive_losses=2)

    state = apply_trade_result(state, pnl_usd=-1.0, consecutive_losses_halve_size=3, now_ts=now_ts)

    assert state.halving_remaining == 3  # ยังลดขนาดไม้เหมือนเดิม
    assert state.paused_until_ts == now_ts + CONSECUTIVE_LOSS_PAUSE_HOURS * 3600
    assert is_paused(state, now_ts) is True
    assert "ขาดทุนติดกัน 3 ไม้" in state.pause_reason


def test_consecutive_loss_pause_expires_after_one_day():
    now_ts = 1_000_000.0
    state = apply_trade_result(
        BreakerState(consecutive_losses=2), pnl_usd=-1.0, consecutive_losses_halve_size=3, now_ts=now_ts
    )

    # ผ่านไป 1 วันเต็มแล้ว -> กลับมาเทรดได้เอง ไม่ต้องรอ ack (ต่างจาก weekly breaker)
    assert is_paused(state, now_ts + CONSECUTIVE_LOSS_PAUSE_HOURS * 3600 + 1) is False
    assert state.weekly_pause_needs_ack is False


def test_winning_trade_does_not_pause():
    now_ts = 1_000_000.0
    state = apply_trade_result(BreakerState(consecutive_losses=2), pnl_usd=5.0, consecutive_losses_halve_size=3, now_ts=now_ts)
    assert state.paused_until_ts is None
    assert is_paused(state, now_ts) is False


def test_single_loss_below_threshold_does_not_pause():
    now_ts = 1_000_000.0
    state = apply_trade_result(BreakerState(), pnl_usd=-1.0, consecutive_losses_halve_size=3, now_ts=now_ts)
    assert state.consecutive_losses == 1
    assert state.paused_until_ts is None


def test_compute_drawdown_pct():
    assert compute_drawdown_pct(100.0, 80.0) == 20.0
    assert compute_drawdown_pct(100.0, 100.0) == 0.0
    assert compute_drawdown_pct(100.0, 110.0) == 0.0  # equity สูงกว่า peak ไม่ควรเป็นลบ


def test_should_trigger_kill():
    assert should_trigger_kill(peak_equity=100.0, current_equity=74.0, max_drawdown_pct=25.0) is True
    assert should_trigger_kill(peak_equity=100.0, current_equity=76.0, max_drawdown_pct=25.0) is False


def test_should_pause_daily_and_weekly():
    assert should_pause_daily(-7.0, 6.0) is True
    assert should_pause_daily(-3.0, 6.0) is False
    assert should_pause_weekly(-13.0, 12.0) is True
    assert should_pause_weekly(-5.0, 12.0) is False


def test_apply_trade_result_triggers_halving_after_streak():
    state = BreakerState()
    state = apply_trade_result(state, pnl_usd=-1.0, consecutive_losses_halve_size=3)
    assert state.consecutive_losses == 1 and state.halving_remaining == 0

    state = apply_trade_result(state, pnl_usd=-1.0, consecutive_losses_halve_size=3)
    assert state.consecutive_losses == 2 and state.halving_remaining == 0

    state = apply_trade_result(state, pnl_usd=-1.0, consecutive_losses_halve_size=3)
    # แพ้ครบ 3 ไม้ -> เข้าโหมด halving 3 ไม้ถัดไป, reset streak counter
    assert state.consecutive_losses == 0
    assert state.halving_remaining == 3
    assert size_multiplier(state) == 0.5


def test_apply_trade_result_win_resets_streak():
    state = BreakerState(consecutive_losses=2)
    state = apply_trade_result(state, pnl_usd=5.0, consecutive_losses_halve_size=3)
    assert state.consecutive_losses == 0


def test_halving_window_counts_down_regardless_of_outcome():
    state = BreakerState(halving_remaining=3)
    state = apply_trade_result(state, pnl_usd=5.0, consecutive_losses_halve_size=3)  # ชนะระหว่าง halving
    assert state.halving_remaining == 2
    assert size_multiplier(state) == 0.5

    state = apply_trade_result(state, pnl_usd=-1.0, consecutive_losses_halve_size=3)
    assert state.halving_remaining == 1

    state = apply_trade_result(state, pnl_usd=5.0, consecutive_losses_halve_size=3)
    assert state.halving_remaining == 0
    assert size_multiplier(state) == 1.0


def test_size_multiplier_default_is_one():
    assert size_multiplier(BreakerState()) == 1.0


def test_apply_daily_breaker_sets_48h_pause():
    state = BreakerState()
    now = 1_000_000.0
    new_state = apply_daily_breaker(state, daily_pnl_pct=-7.0, daily_loss_pct=6.0, now_ts=now)
    assert new_state.paused_until_ts == now + 48 * 3600
    assert new_state.weekly_pause_needs_ack is False
    assert is_paused(new_state, now_ts=now + 3600) is True
    assert is_paused(new_state, now_ts=now + 49 * 3600) is False  # พ้น 48 ชม.แล้ว


def test_apply_daily_breaker_no_trigger_when_within_limit():
    state = BreakerState()
    new_state = apply_daily_breaker(state, daily_pnl_pct=-2.0, daily_loss_pct=6.0, now_ts=1000.0)
    assert new_state == state


def test_apply_weekly_breaker_requires_manual_ack():
    state = BreakerState()
    now = 1_000_000.0
    new_state = apply_weekly_breaker(state, weekly_pnl_pct=-15.0, weekly_loss_pct=12.0, now_ts=now)
    assert new_state.weekly_pause_needs_ack is True
    # ต่างจาก daily breaker: ต้อง ack ไม่ auto-unpause แม้เวลาผ่านไปนานมาก
    assert is_paused(new_state, now_ts=now + 365 * 24 * 3600) is True


def test_clear_pause_after_human_ack():
    state = BreakerState(paused_until_ts=1000.0, pause_reason="test", weekly_pause_needs_ack=True)
    cleared = clear_pause(state)
    assert is_paused(cleared, now_ts=999999.0) is False


def test_kill_file_write_and_check(tmp_path):
    kill_path = tmp_path / "state" / "KILL"
    assert is_killed(kill_path) is False
    write_kill_file(kill_path, reason="max drawdown 26% เกิน 25%")
    assert is_killed(kill_path) is True
    assert "max drawdown" in kill_path.read_text(encoding="utf-8")

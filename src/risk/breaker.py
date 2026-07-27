"""
Circuit breaker ตาม BUILD-SPEC.md ข้อ 5 (breakers) — ทุกอย่างเป็นโค้ดกำหนดตายตัว
- daily_loss_pct เกิน -> ปิด position, พัก 48 ชม.
- weekly_loss_pct เกิน -> พัก 7 วัน, ต้องมีมนุษย์ ack ก่อนกลับมาเทรด
- max_drawdown_pct จาก peak equity -> เขียนไฟล์ KILL, มนุษย์เท่านั้นปลดได้ (ลบไฟล์เอง)
- แพ้ติดกัน consecutive_losses_halve_size ไม้ -> ลดขนาดครึ่งหนึ่ง 3 ไม้ถัดไป
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

HALVING_WINDOW_TRADES = 3
CONSECUTIVE_LOSS_PAUSE_HOURS = 24  # แพ้ติดกันครบเกณฑ์ -> หยุดเทรด 1 วันเต็ม (ไม่ใช่แค่ลดขนาดไม้)


@dataclass(frozen=True)
class BreakerState:
    consecutive_losses: int = 0
    halving_remaining: int = 0
    paused_until_ts: float | None = None
    pause_reason: str | None = None
    weekly_pause_needs_ack: bool = False


def compute_drawdown_pct(peak_equity: float, current_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity * 100)


def compute_period_pnl_pct(start_equity: float, current_equity: float) -> float:
    """PnL % ของช่วงเวลา (วัน/สัปดาห์) เทียบ equity ตอนเริ่มช่วง — ค่าลบ = ขาดทุน"""
    if start_equity <= 0:
        return 0.0
    return (current_equity - start_equity) / start_equity * 100


def should_trigger_kill(peak_equity: float, current_equity: float, max_drawdown_pct: float) -> bool:
    return compute_drawdown_pct(peak_equity, current_equity) >= max_drawdown_pct


def should_pause_daily(daily_pnl_pct: float, daily_loss_pct: float) -> bool:
    return daily_pnl_pct <= -abs(daily_loss_pct)


def should_pause_weekly(weekly_pnl_pct: float, weekly_loss_pct: float) -> bool:
    return weekly_pnl_pct <= -abs(weekly_loss_pct)


def apply_trade_result(
    state: BreakerState,
    pnl_usd: float,
    consecutive_losses_halve_size: int,
    now_ts: float | None = None,
) -> BreakerState:
    """เรียกทุกครั้งที่ปิดไม้ (ไม่ใช่วันที่ FLAT) เพื่ออัปเดตสถานะ streak/halving

    เมื่อแพ้ติดกันครบเกณฑ์: ทำ 2 อย่างพร้อมกัน (แนวคิดจาก Earthh Evans playbook No-Trade Rule #4
    "ขาดทุน 3 ครั้งติด = หยุด 2-3 วัน" — เราเลือก 1 วันตามที่ผู้ใช้กำหนด)
      1. ลดขนาดไม้ครึ่งหนึ่งใน 3 ไม้ถัดไป (ของเดิม)
      2. หยุดเทรด 1 วันเต็มทันที (เพิ่มใหม่) — ให้ตลาดกับระบบได้ตั้งหลักก่อน ไม่ไล่แก้ตัวทันที
    """
    is_loss = pnl_usd < 0
    now_ts = now_ts if now_ts is not None else time.time()

    if state.halving_remaining > 0:
        new_halving = state.halving_remaining - 1
        new_consecutive = state.consecutive_losses + 1 if is_loss else 0
        return replace(state, consecutive_losses=new_consecutive, halving_remaining=new_halving)

    new_consecutive = state.consecutive_losses + 1 if is_loss else 0
    if new_consecutive >= consecutive_losses_halve_size:
        return replace(
            state,
            consecutive_losses=0,
            halving_remaining=HALVING_WINDOW_TRADES,
            paused_until_ts=now_ts + CONSECUTIVE_LOSS_PAUSE_HOURS * 3600,
            pause_reason=(
                f"ขาดทุนติดกัน {consecutive_losses_halve_size} ไม้ — หยุดเทรด "
                f"{CONSECUTIVE_LOSS_PAUSE_HOURS} ชม. และลดขนาดไม้ครึ่งหนึ่งอีก {HALVING_WINDOW_TRADES} ไม้ถัดไป"
            ),
        )
    return replace(state, consecutive_losses=new_consecutive)


def size_multiplier(state: BreakerState) -> float:
    """ตัวคูณขนาดไม้ถัดไป — 0.5 ถ้าอยู่ในช่วง halving, ไม่งั้น 1.0"""
    return 0.5 if state.halving_remaining > 0 else 1.0


def apply_daily_breaker(state: BreakerState, daily_pnl_pct: float, daily_loss_pct: float, now_ts: float) -> BreakerState:
    if should_pause_daily(daily_pnl_pct, daily_loss_pct):
        return replace(
            state,
            paused_until_ts=now_ts + 48 * 3600,
            pause_reason=f"daily loss {daily_pnl_pct:.2f}% เกินเกณฑ์ {daily_loss_pct}% — พัก 48 ชม.",
        )
    return state


def apply_weekly_breaker(state: BreakerState, weekly_pnl_pct: float, weekly_loss_pct: float, now_ts: float) -> BreakerState:
    if should_pause_weekly(weekly_pnl_pct, weekly_loss_pct):
        return replace(
            state,
            paused_until_ts=now_ts + 7 * 24 * 3600,
            pause_reason=f"weekly loss {weekly_pnl_pct:.2f}% เกินเกณฑ์ {weekly_loss_pct}% — พัก 7 วัน ต้องมนุษย์ ack",
            weekly_pause_needs_ack=True,
        )
    return state


def is_paused(state: BreakerState, now_ts: float) -> bool:
    if state.paused_until_ts is None:
        return False
    if state.weekly_pause_needs_ack:
        return True  # ต้องรอมนุษย์ ack เสมอ ไม่ auto-unpause ตามเวลา
    return now_ts < state.paused_until_ts


def clear_pause(state: BreakerState) -> BreakerState:
    """มนุษย์ ack แล้ว ปลดสถานะพัก (ใช้ตอนพัก weekly ที่ต้องการ ack)"""
    return replace(state, paused_until_ts=None, pause_reason=None, weekly_pause_needs_ack=False)


# --- KILL file: หยุดทันทีทุกกรณี, ปลดได้ด้วยมือเท่านั้น (non-negotiable, BUILD-SPEC.md ข้อ 8) ---


def write_kill_file(kill_path: Path, reason: str) -> None:
    kill_path.parent.mkdir(parents=True, exist_ok=True)
    kill_path.write_text(f"KILL triggered at {time.time()}\nreason: {reason}\n", encoding="utf-8")


def is_killed(kill_path: Path) -> bool:
    return kill_path.exists()

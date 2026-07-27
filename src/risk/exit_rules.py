"""
ตรรกะปิดไม้ตาม BUILD-SPEC.md ข้อ 2b — สรุป 4 กรณีที่ปิดไม้ได้:
  1. SL hit (ตรวจจากการ reconcile กับ exchange — ออเดอร์ trigger จริง ไม่ใช่โค้ดในนี้)
  2. TP hit (เหมือนกัน)
  3. Time exit — ถือนานเกิน max_holding_days (คำนวณในไฟล์นี้ เป็นโค้ดล้วน)
  4. Thesis invalidation — ต้องให้ agent (judge) ประเมินใหม่ตอน manage_existing (P3) เพราะเป็น
     เงื่อนไขภาษาธรรมชาติที่ analyst เขียนไว้ตอนเปิดไม้ ไม่ใช่ตัวเลขที่โค้ดแปลตรงๆได้
     ไฟล์นี้รับผลการประเมินนั้นมาเป็น boolean เท่านั้น (ไม่ตัดสินใจเอง)

ลำดับความสำคัญเมื่อปิดพร้อมกันหลายเหตุผล: SL > TP > time_exit > invalidation
(SL/TP มาจาก exchange จริงเสมอ ดังนั้นมาก่อน time/invalidation ที่เป็น judgement เพิ่มเติม)
"""
from __future__ import annotations

from dataclasses import dataclass


def should_force_time_exit(opened_at_ts: float, now_ts: float, max_holding_days: int) -> bool:
    if now_ts < opened_at_ts:
        return False  # ข้อมูลเวลาผิดปกติ -> อย่าปิดไม้มั่ว ให้ reconcile จับ error แยก
    holding_days = (now_ts - opened_at_ts) / 86400
    return holding_days >= max_holding_days


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str | None = None


def classify_exit(
    sl_hit: bool,
    tp_hit: bool,
    opened_at_ts: float,
    now_ts: float,
    max_holding_days: int,
    invalidation_triggered: bool = False,
) -> ExitDecision:
    """รวมทุกสัญญาณปิดไม้เป็นคำตอบเดียว เรียงลำดับความสำคัญตามที่อธิบายไว้ด้านบนไฟล์"""
    if sl_hit:
        return ExitDecision(should_exit=True, reason="stop_loss_hit")
    if tp_hit:
        return ExitDecision(should_exit=True, reason="take_profit_hit")
    if should_force_time_exit(opened_at_ts, now_ts, max_holding_days):
        return ExitDecision(should_exit=True, reason="max_holding_days_exceeded")
    if invalidation_triggered:
        return ExitDecision(should_exit=True, reason="thesis_invalidated")
    return ExitDecision(should_exit=False, reason=None)

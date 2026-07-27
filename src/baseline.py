"""
Baseline strategy — กลยุทธ์ deterministic ที่ไม่มี LLM เลย (โครงสร้าง repo ตาม BUILD-SPEC.md §1)

ใช้ 2 ที่:
  1. **Fallback เมื่อ cost governor ตัด LLM ออกทั้งหมด** (DEGRADE_LLM_OFF ใน src/agents/llm.py) — ระบบยัง
     เทรดต่อได้โดยไม่หยุดตายแม้งบ LLM หมดเดือน (ตาม BUILD-SPEC.md §4.2 ข้อ degradation ladder ขั้นสุดท้าย)
  2. **Shadow benchmark** คำนวณคู่กันทุกวัน (BUILD-SPEC.md §2 ขั้น 12 baseline_shadow) เพื่อเทียบว่า
     ทีม agent ให้ผลดีกว่าการเดาตามกฎง่ายๆหรือไม่ — เก็บ equity แยกจากบัญชีจริง/paper หลัก

กฎตั้งใจให้ "ง่ายแต่มีเหตุผล" ไม่ใช่สุ่มเดา เพื่อเป็น benchmark ที่มีความหมายจริง:
  - เลือกผู้เข้าชิงที่ composite score สูงสุดจาก shortlist ของวันนั้นเท่านั้น (ใช้ผลจาก screening.py
    ตัวเดียวกับที่ agent เห็น ไม่ได้มองข้ามไปดูตลาดอื่นที่ agent ไม่เห็น เพื่อเทียบกันแบบยุติธรรม)
  - ทิศทางตาม regime ล้วนๆ (จาก regime.py): trend_up -> long, trend_down -> short, chop -> flat
  - confidence คงที่ (deterministic ไม่มีความไม่แน่นอนจากการตีความของ agent) แต่ยังต้องผ่าน
    min_judge_confidence gate เหมือน judge ปกติ — ความเสี่ยงจริงถูกคุมด้วย BASELINE_RISK_MULTIPLIER
    (ลดขนาดไม้ลงเพราะกลยุทธ์นี้ไม่ผ่านการถกเถียงของ agent หลายมุมมองแบบ analyst/redteam/judge)
"""
from __future__ import annotations

from dataclasses import dataclass

BASELINE_CONFIDENCE = 65.0
BASELINE_RISK_MULTIPLIER = 0.5  # ลดขนาดไม้ลงครึ่งหนึ่งเทียบ risk_per_trade_pct ปกติ (ไม่ผ่าน agent debate)


@dataclass
class BaselineDecision:
    action: str  # "long" | "short" | "flat"
    asset: str | None
    confidence: float
    stop_pct: float
    take_profit_pct: float
    reasoning: str


def decide(
    shortlist: list[dict],
    regime_by_coin: dict[str, dict],
    default_stop_pct: float,
    default_take_profit_pct: float,
) -> BaselineDecision:
    """เลือกผู้เข้าชิง composite score สูงสุดจาก shortlist แล้วตัดสินทิศทางตาม regime เท่านั้น

    shortlist: list ของ dict {coin, composite, ...} จาก screening.build_shortlist()["shortlist"]
    regime_by_coin: {coin: {"trend": ..., "vol": ..., "tag": ...}} จาก regime.classify_regime()
    default_stop_pct/default_take_profit_pct: ใช้ค่าเดียวกับ risk.yaml (stops.stop_floor_pct เป็นต้น)
    เพราะ baseline ไม่มี ATR-based sizing เป็นของตัวเอง — ให้ main.py คำนวณ ATR stop จริงต่อผ่าน
    risk/sizing.py เหมือน judge ปกติ (ค่าที่ใส่มาตรงนี้เป็นแค่ค่าเริ่มต้นเผื่อ ATR ไม่มี)
    """
    if not shortlist:
        return BaselineDecision(
            action="flat",
            asset=None,
            confidence=0.0,
            stop_pct=0.0,
            take_profit_pct=0.0,
            reasoning="ไม่มีผู้เข้าชิงใน shortlist วันนี้ — baseline ตอบ FLAT",
        )

    top = max(shortlist, key=lambda item: item.get("composite", 0.0))
    coin = top["coin"]
    regime = regime_by_coin.get(coin, {})
    trend = regime.get("trend", "chop")

    if trend == "trend_up":
        action = "long"
    elif trend == "trend_down":
        action = "short"
    else:
        action = "flat"

    if action == "flat":
        return BaselineDecision(
            action="flat",
            asset=None,
            confidence=BASELINE_CONFIDENCE,
            stop_pct=0.0,
            take_profit_pct=0.0,
            reasoning=f"{coin} (composite สูงสุด) อยู่ใน regime chop — ไม่มีเทรนด์ชัดพอให้ baseline เทรด",
        )

    return BaselineDecision(
        action=action,
        asset=coin,
        confidence=BASELINE_CONFIDENCE,
        stop_pct=default_stop_pct,
        take_profit_pct=default_take_profit_pct,
        reasoning=f"{coin} (composite สูงสุด) อยู่ใน regime {trend} — baseline ตามเทรนด์ตรงๆ ไม่มี LLM ตีความเพิ่ม",
    )

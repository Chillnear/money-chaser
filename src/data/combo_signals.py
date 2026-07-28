"""
"Combination Read" — จับคู่สัญญาณ price + OI + funding + volume เป็น pattern เดียวที่ตีความง่ายขึ้น
ตามไอเดียจาก playbook ของ Earthh Evans ที่ผู้ใช้เลือกรับมาใช้ (P5.3) แนวคิดหลัก: สัญญาณเดี่ยวๆ (แค่ RSI สูง
หรือแค่ funding แพง) ตีความผิดพลาดได้ง่าย แต่ถ้าหลายสัญญาณชี้ไปทางเดียวกันพร้อมกัน ความมั่นใจของสัญญาณนั้น
สูงขึ้นมาก — เป็นสิ่งที่ redteam เดิมทำอยู่แล้วแบบ ad hoc ในพรอมต์ ไฟล์นี้ทำให้เป็น "ป้ายกำกับ" ที่คำนวณ
แน่นอนด้วยโค้ดแทน (deterministic) แล้วส่งให้ทุก agent เห็นในตาราง feature เดียวกัน

liquidation cascade proxy: ข้อมูล liquidation จริงเป็นของเสียเงิน (CoinGlass $29/เดือนขึ้นไป — ไม่มี free
tier ตามที่ตรวจสอบไว้) เราประมาณจาก "OI ยุบตัวแรง + วอลุ่มพุ่ง + ราคาขยับแรงพร้อมกัน" แทน (ข้อมูลฟรีทั้งหมด
จาก Hyperliquid) — เป็น proxy พฤติกรรม ไม่ใช่ตัวเลข liquidation จริง 100%

ทุกฟังก์ชันในไฟล์นี้เป็น pure function กำหนดตายตัว ไม่มี LLM ตัดสินใจ (non-negotiable ข้อ 2) ผลลัพธ์เป็นแค่
"ป้ายกำกับ" ที่ยัดเข้าไปในตาราง feature ให้ agent ไปตีความต่อ ไม่ใช่คำสั่งเทรดหรือ veto โดยตรง
"""
from __future__ import annotations

from dataclasses import dataclass

# เกณฑ์ทั้งหมดปรับได้ตามผลจริงในอนาคต — ตั้งค่าเริ่มต้นแบบระมัดระวัง (ต้องสุดโต่งพร้อมกันหลายตัวถึง trigger
# กันไม่ให้ pattern ขึ้นบ่อยเกินจนไม่มีความหมาย)
LARGE_PRICE_MOVE_PCT = 5.0
MODERATE_PRICE_MOVE_PCT = 2.0
OI_COLLAPSE_PCT = -8.0
OI_SURGE_PCT = 8.0
VOLUME_SPIKE_RATIO_THRESHOLD = 2.0
EXTREME_FUNDING_ANNUAL_PCT = 15.0

NONE_PATTERN = "none"
LIQUIDATION_CASCADE_PROXY = "liquidation_cascade_proxy"
LONG_SQUEEZE_RISK = "long_squeeze_risk"
SHORT_SQUEEZE_RISK = "short_squeeze_risk"
HEALTHY_TREND_CONTINUATION = "healthy_trend_continuation"

PATTERN_LABELS_TH = {
    NONE_PATTERN: "none — ไม่มีสัญญาณ combination ชัดเจน",
    LIQUIDATION_CASCADE_PROXY: (
        "liquidation_cascade_proxy — OI ยุบ+วอลุ่มพุ่ง+ราคาขยับแรงพร้อมกัน = สัญญาณ position ถูกบีบปิด "
        "จำนวนมาก (ประมาณจากข้อมูลฟรี ไม่ใช่ liquidation จริง) ผันผวนสูง ควรเลี่ยงเข้าใหม่"
    ),
    LONG_SQUEEZE_RISK: (
        "long_squeeze_risk — ราคาขึ้น+OI พุ่ง+funding แพงฝั่ง long พร้อมกัน = long ใหม่แห่เข้าจำนวนมาก "
        "อาจแน่นเกินไป เสี่ยงกลับตัวแรง แม้เทรนด์จะดูแข็งแรง"
    ),
    SHORT_SQUEEZE_RISK: (
        "short_squeeze_risk — ราคาลง+OI พุ่ง+funding ติดลบแรงฝั่ง short พร้อมกัน = short ใหม่แห่เข้าจำนวนมาก "
        "อาจแน่นเกินไป เสี่ยงกลับตัวแรง แม้เทรนด์จะดูแข็งแรง"
    ),
    HEALTHY_TREND_CONTINUATION: (
        "healthy_trend_continuation — ราคาขยับ+OI เพิ่มพอประมาณตามทิศทาง+funding ไม่สุดโต่ง = position ใหม่ "
        "ดูเหมือนของจริง ไม่ใช่ crowded trade — เทรนด์มีโอกาสไปต่อ"
    ),
}


@dataclass
class CombinationReadResult:
    pattern: str
    label: str
    inputs_missing: bool


def classify_combination_pattern(
    price_return_24h_pct: float | None,
    oi_change_24h_pct: float | None,
    funding_annualized_signed_pct: float | None,
    volume_spike_ratio: float | None,
) -> CombinationReadResult:
    """กำหนดแบบ fail-safe: ถ้าข้อมูลที่ต้องใช้ตัวใดตัวหนึ่งขาด (ส่วนใหญ่คือ oi_change_24h_pct ตอนยังไม่มี
    ประวัติสะสมพอ — ดู src/data/oi_tracker.py) -> คืน NONE_PATTERN พร้อม inputs_missing=True แทนการเดา
    """
    if price_return_24h_pct is None or oi_change_24h_pct is None or funding_annualized_signed_pct is None:
        return CombinationReadResult(NONE_PATTERN, PATTERN_LABELS_TH[NONE_PATTERN], True)

    vol_ratio = volume_spike_ratio if volume_spike_ratio is not None else 1.0

    if (
        abs(price_return_24h_pct) >= LARGE_PRICE_MOVE_PCT
        and oi_change_24h_pct <= OI_COLLAPSE_PCT
        and vol_ratio >= VOLUME_SPIKE_RATIO_THRESHOLD
    ):
        return CombinationReadResult(LIQUIDATION_CASCADE_PROXY, PATTERN_LABELS_TH[LIQUIDATION_CASCADE_PROXY], False)

    if (
        price_return_24h_pct > MODERATE_PRICE_MOVE_PCT
        and oi_change_24h_pct >= OI_SURGE_PCT
        and funding_annualized_signed_pct > EXTREME_FUNDING_ANNUAL_PCT
    ):
        return CombinationReadResult(LONG_SQUEEZE_RISK, PATTERN_LABELS_TH[LONG_SQUEEZE_RISK], False)

    if (
        price_return_24h_pct < -MODERATE_PRICE_MOVE_PCT
        and oi_change_24h_pct >= OI_SURGE_PCT
        and funding_annualized_signed_pct < -EXTREME_FUNDING_ANNUAL_PCT
    ):
        return CombinationReadResult(SHORT_SQUEEZE_RISK, PATTERN_LABELS_TH[SHORT_SQUEEZE_RISK], False)

    if (
        abs(price_return_24h_pct) > MODERATE_PRICE_MOVE_PCT
        and 0 < oi_change_24h_pct < OI_SURGE_PCT
        and abs(funding_annualized_signed_pct) <= EXTREME_FUNDING_ANNUAL_PCT
    ):
        return CombinationReadResult(
            HEALTHY_TREND_CONTINUATION, PATTERN_LABELS_TH[HEALTHY_TREND_CONTINUATION], False
        )

    return CombinationReadResult(NONE_PATTERN, PATTERN_LABELS_TH[NONE_PATTERN], False)

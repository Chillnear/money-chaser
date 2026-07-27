"""
จำแนก market regime เป็นตาราง 3x3: trend_up|trend_down|chop x vol_low|vol_mid|vol_high
ตาม BUILD-SPEC.md ข้อ 3 — ใช้เป็น tag ติดกับทุก decision และทุก lesson (สำหรับ Reflector ข้อ 7.2)

Input เป็นตัวเลขที่คำนวณไว้แล้วจาก features.py เท่านั้น (ไม่คำนวณอะไรใหม่ในนี้)
"""
from __future__ import annotations

import math

ADX_TREND_THRESHOLD = 25.0
VOL_LOW_MAX = 0.33
VOL_HIGH_MIN = 0.66


def classify_trend(adx: float, ema_gap_pct: dict[int, float]) -> str:
    """trend_up / trend_down / chop
    ใช้ ADX วัดความแรงของเทรนด์ (ไม่บอกทิศทาง) + สัญลักษณ์ของราคาเทียบ EMA ระยะกลาง (50 หรือ 21) บอกทิศทาง
    """
    if adx is None or (isinstance(adx, float) and math.isnan(adx)):
        return "chop"  # ไม่รู้ความแรงเทรนด์ -> fail-safe เป็น chop (ระมัดระวังไว้ก่อน)

    if adx < ADX_TREND_THRESHOLD:
        return "chop"

    direction_gap = ema_gap_pct.get(50, ema_gap_pct.get(21, ema_gap_pct.get(9)))
    if direction_gap is None or (isinstance(direction_gap, float) and math.isnan(direction_gap)):
        return "chop"

    return "trend_up" if direction_gap > 0 else "trend_down"


def classify_vol(vol_percentile_1y: float) -> str:
    """vol_low / vol_mid / vol_high จาก percentile ของ realized vol เทียบ 1 ปีที่ผ่านมา"""
    if vol_percentile_1y is None or (isinstance(vol_percentile_1y, float) and math.isnan(vol_percentile_1y)):
        return "vol_mid"  # ไม่รู้ -> fail-safe เป็นโซนกลาง ไม่ตื่นตูมหรือชะล่าใจเกิน

    if vol_percentile_1y < VOL_LOW_MAX:
        return "vol_low"
    if vol_percentile_1y > VOL_HIGH_MIN:
        return "vol_high"
    return "vol_mid"


def classify_regime(price_features: dict) -> dict:
    """รับผลจาก features.build_price_features() คืน {trend, vol, tag} เช่น tag='trend_up_vol_high'"""
    trend = classify_trend(price_features.get("adx"), price_features.get("ema_gap_pct", {}))
    vol = classify_vol(price_features.get("vol_percentile_1y"))
    return {"trend": trend, "vol": vol, "tag": f"{trend}_{vol}"}

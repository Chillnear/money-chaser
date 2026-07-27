"""
สูตร position sizing ตาม BUILD-SPEC.md ข้อ 5 — โค้ดล้วน กำหนดตายตัว ไม่มี LLM แตะเลขได้เลย
(non-negotiable ข้อ 2, 3: LLM ไม่คำนวณเลข, risk.yaml แก้ได้โดยมนุษย์เท่านั้น)

สูตร:
  stop_pct   = clamp(atr_multiple x ATR%, stop_floor_pct, stop_cap_pct)
  raw_notional = (equity x risk_per_trade_pct%) / stop_pct%
  hard_cap   = min(max_notional_usd, equity x max_notional_pct_of_equity%, equity x max_leverage)
  notional   = clamp(raw_notional, min_notional_usd, hard_cap)
  ถ้าโดนดันขึ้นถึง min_notional_usd (ทั้งที่ raw ต่ำกว่า) แล้ว implied_risk > min_notional_override_max_risk_pct
    -> FLAT (เสี่ยงเกินกว่าที่ตั้งใจ ดีกว่าฝืนเข้าไม้)
  ถ้า hard_cap < min_notional_usd -> FLAT (ทุนเล็กเกินจะเปิดไม้ขั้นต่ำได้แม้จะ leverage เต็มแล้ว)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SizingResult:
    decision: str  # "OK" หรือ "FLAT"
    reason: str
    stop_pct: float | None = None
    take_profit_pct: float | None = None
    notional_usd: float | None = None
    leverage: float | None = None
    implied_risk_pct: float | None = None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def compute_position_size(
    equity_usd: float,
    atr_pct: float,
    risk_per_trade_pct: float,
    min_notional_usd: float,
    max_notional_usd: float,
    max_notional_pct_of_equity: float,
    min_notional_override_max_risk_pct: float,
    atr_multiple: float,
    stop_floor_pct: float,
    stop_cap_pct: float,
    reward_risk_ratio: float,
    max_leverage: float,
) -> SizingResult:
    if equity_usd <= 0:
        return SizingResult(decision="FLAT", reason=f"equity ไม่ถูกต้อง ({equity_usd})")
    if atr_pct is None or atr_pct != atr_pct:  # NaN check
        return SizingResult(decision="FLAT", reason="ATR% ไม่มีค่า (ข้อมูลไม่พอคำนวณ)")

    stop_pct = _clamp(atr_multiple * atr_pct, stop_floor_pct, stop_cap_pct)
    take_profit_pct = stop_pct * reward_risk_ratio

    raw_notional = (equity_usd * risk_per_trade_pct / 100) / (stop_pct / 100)

    equity_pct_cap = equity_usd * max_notional_pct_of_equity / 100
    leverage_cap = equity_usd * max_leverage
    hard_cap = min(max_notional_usd, equity_pct_cap, leverage_cap)

    if hard_cap < min_notional_usd:
        return SizingResult(
            decision="FLAT",
            reason=(
                f"ทุนเล็กเกินไป: hard_cap={hard_cap:.2f} USD ต่ำกว่า min_notional={min_notional_usd} USD "
                f"แม้ใช้ leverage/สัดส่วนทุนเต็มที่แล้ว"
            ),
            stop_pct=stop_pct,
            take_profit_pct=take_profit_pct,
        )

    notional = _clamp(raw_notional, min_notional_usd, hard_cap)
    was_floored = raw_notional < min_notional_usd
    implied_risk_pct = notional * stop_pct / 100 / equity_usd * 100
    leverage = notional / equity_usd

    if was_floored and implied_risk_pct > min_notional_override_max_risk_pct:
        return SizingResult(
            decision="FLAT",
            reason=(
                f"ขนาดขั้นต่ำ ({min_notional_usd} USD) ดัน implied risk เป็น {implied_risk_pct:.2f}% "
                f"เกิน override cap {min_notional_override_max_risk_pct}% — เสี่ยงเกินไปสำหรับทุนก้อนนี้"
            ),
            stop_pct=stop_pct,
            take_profit_pct=take_profit_pct,
            notional_usd=notional,
            leverage=leverage,
            implied_risk_pct=implied_risk_pct,
        )

    return SizingResult(
        decision="OK",
        reason="ผ่านเกณฑ์ sizing ปกติ",
        stop_pct=stop_pct,
        take_profit_pct=take_profit_pct,
        notional_usd=notional,
        leverage=leverage,
        implied_risk_pct=implied_risk_pct,
    )

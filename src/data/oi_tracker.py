"""
ประวัติ Open Interest (OI) แบบเก็บเอง รายวัน — Hyperliquid Info API ให้ค่า OI ปัจจุบันได้เท่านั้น (จุดเดียว
ไม่มี endpoint ประวัติย้อนหลัง) ระบบเราจึงเก็บ snapshot รายวันไว้เองที่ state/journal/oi_history.jsonl
ทุกรอบที่รันจริง เพื่อคำนวณ "OI เปลี่ยนไปกี่% ใน 24 ชม./7 วัน" ย้อนหลังได้โดยไม่ต้องเสียเงินซื้อ CoinGlass
($29/เดือนขึ้นไป) ตามที่ตกลงกันไว้ตอนอ่าน playbook ของ Earthh Evans (P5.3)

ข้อจำกัดที่ต้องรู้: ข้อมูลจะ "สะสม" ทีละวันตั้งแต่วันที่เริ่มใช้ฟีเจอร์นี้ — ช่วงแรกที่ยังไม่มีข้อมูลย้อนหลัง
พอ compute_oi_change_pct จะคืน None เสมอ (fail-safe: "ไม่มีข้อมูล" ต้องไม่เท่ากับ "เปลี่ยนแปลง 0%")
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from src.util.io import append_jsonl, load_jsonl

OI_HISTORY_FILENAME = "oi_history.jsonl"


def record_oi_snapshot(path: Path, date: str, universe_snapshot: list[dict]) -> None:
    """บันทึก OI ของทุกเหรียญใน universe_snapshot สำหรับวันนี้ — idempotent: ข้ามเหรียญที่บันทึกไปแล้ว
    ในวันเดียวกัน กันข้อมูลซ้ำถ้ามีการรันหลายรอบวันเดียวกัน (เช่นตอนทดสอบ/debug)
    """
    existing = load_jsonl(path)
    already_recorded_coins = {r.get("coin") for r in existing if r.get("date") == date}
    for entry in universe_snapshot:
        coin = entry.get("coin")
        if coin is None or coin in already_recorded_coins:
            continue
        append_jsonl(path, {"date": date, "coin": coin, "open_interest_usd": entry.get("open_interest_usd")})


def load_oi_history(path: Path) -> list[dict]:
    return load_jsonl(path)


def compute_oi_change_pct(
    history: list[dict],
    coin: str,
    current_oi_usd: float | None,
    today_date: str,
    lookback_days: int,
    tolerance_days: int = 1,
) -> float | None:
    """หา snapshot ของ coin นี้ที่ใกล้ "today - lookback_days" ที่สุด (ในกรอบ ±tolerance_days วัน จาก
    เป้าหมาย เผื่อวันที่ระบบไม่ได้รันติดกันทุกวัน) แล้วคำนวณ % เปลี่ยนแปลงเทียบ current_oi_usd

    คืน None (ไม่ใช่ 0.0) ถ้าไม่มีข้อมูลพอ — สำคัญมากเพราะ combo_signals.py ต้องแยกแยะ "ไม่มีข้อมูล"
    ออกจาก "OI ไม่เปลี่ยนเลย" ได้ ไม่งั้นจะทริกเกอร์ pattern ผิดตอนข้อมูลยังไม่สะสมพอ
    """
    if current_oi_usd is None:
        return None
    try:
        today = dt.date.fromisoformat(today_date)
    except ValueError:
        return None
    target_date = today - dt.timedelta(days=lookback_days)

    candidates = [r for r in history if r.get("coin") == coin and r.get("open_interest_usd") is not None]
    if not candidates:
        return None

    best = None
    best_diff = None
    for r in candidates:
        try:
            r_date = dt.date.fromisoformat(r["date"])
        except (KeyError, ValueError, TypeError):
            continue
        diff = abs((r_date - target_date).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best = r
            best_diff = diff

    if best is None:
        return None

    old_oi = best.get("open_interest_usd")
    if not old_oi:
        return None
    return (current_oi_usd - old_oi) / old_oi * 100

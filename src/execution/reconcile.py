"""
Run lock (idempotency) + reconciliation ระหว่าง journal กับสถานะจริงจาก broker
ตาม BUILD-SPEC.md non-negotiable ข้อ 6 (fail-closed) และข้อ 7 (idempotent)

หลักการ: ถ้า journal กับของจริงไม่ตรงกัน (mismatch) -> ไม่เทรดต่อ + แจ้งเตือน เพราะแปลว่ามีอะไร
ผิดปกติที่ระบบยังไม่เข้าใจ (เช่น ออเดอร์ที่คิดว่าเปิดจริงๆไม่เปิด, หรือมีคนไปเทรดมือแทรก)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReconcileResult:
    matched: bool
    reason: str


def has_run_today(last_run_path: Path, today_date: str) -> bool:
    """เช็ค run lock — กันไม่ให้เทรดซ้ำถ้า cron รันมากกว่า 1 ครั้งในวันเดียวกัน (idempotency)"""
    if not last_run_path.exists():
        return False
    try:
        data = json.loads(last_run_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False  # ไฟล์เสีย -> ถือว่ายังไม่รัน ให้รันใหม่ได้ (ปลอดภัยกว่าค้างไปเลย)
    return data.get("date") == today_date and data.get("completed") is True


def mark_run_complete(last_run_path: Path, today_date: str, extra: dict | None = None) -> None:
    last_run_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": today_date, "completed": True}
    if extra:
        payload.update(extra)
    last_run_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def reconcile_position(
    journal_position: dict | None,
    broker_position: dict | None,
    price_tolerance_pct: float = 1.0,
    notional_tolerance_pct: float = 5.0,
) -> ReconcileResult:
    """เทียบ position ที่ journal คิดว่าเปิดอยู่ กับของจริงจาก broker (clearinghouseState)
    dict คาดหวัง keys: asset, side, notional_usd, entry_price (ถ้ามี position) — None ถ้าไม่มีไม้เปิดอยู่
    """
    if journal_position is None and broker_position is None:
        return ReconcileResult(matched=True, reason="ไม่มี position ทั้งสองฝั่ง — ตรงกัน")

    if journal_position is None or broker_position is None:
        return ReconcileResult(
            matched=False,
            reason=f"journal มี position={journal_position is not None} แต่ broker มี position={broker_position is not None}",
        )

    if journal_position["asset"] != broker_position["asset"]:
        return ReconcileResult(
            matched=False,
            reason=f"asset ไม่ตรง: journal={journal_position['asset']} broker={broker_position['asset']}",
        )

    if journal_position["side"] != broker_position["side"]:
        return ReconcileResult(
            matched=False,
            reason=f"side ไม่ตรง: journal={journal_position['side']} broker={broker_position['side']}",
        )

    notional_diff_pct = abs(journal_position["notional_usd"] - broker_position["notional_usd"]) / max(
        journal_position["notional_usd"], 1e-9
    ) * 100
    if notional_diff_pct > notional_tolerance_pct:
        return ReconcileResult(
            matched=False,
            reason=(
                f"notional ต่างกัน {notional_diff_pct:.2f}% เกิน tolerance {notional_tolerance_pct}% "
                f"(journal={journal_position['notional_usd']}, broker={broker_position['notional_usd']})"
            ),
        )

    return ReconcileResult(matched=True, reason="asset/side/notional ตรงกันภายใน tolerance")


def reconcile_equity(journal_equity: float, broker_equity: float, tolerance_pct: float = 1.0) -> ReconcileResult:
    if journal_equity <= 0:
        return ReconcileResult(matched=False, reason=f"journal_equity ผิดปกติ: {journal_equity}")

    diff_pct = abs(journal_equity - broker_equity) / journal_equity * 100
    if diff_pct > tolerance_pct:
        return ReconcileResult(
            matched=False,
            reason=f"equity ต่างกัน {diff_pct:.2f}% เกิน tolerance {tolerance_pct}% (journal={journal_equity}, broker={broker_equity})",
        )
    return ReconcileResult(matched=True, reason=f"equity ตรงกันภายใน tolerance ({diff_pct:.2f}%)")


def reconcile_all(
    journal_position: dict | None,
    broker_position: dict | None,
    journal_equity: float,
    broker_equity: float,
    price_tolerance_pct: float = 1.0,
    notional_tolerance_pct: float = 5.0,
    equity_tolerance_pct: float = 1.0,
) -> ReconcileResult:
    """รวมทุกการเช็ค — mismatch จุดไหนก็ถือว่า reconcile ไม่ผ่านทั้งหมด (fail-closed)"""
    position_result = reconcile_position(journal_position, broker_position, price_tolerance_pct, notional_tolerance_pct)
    if not position_result.matched:
        return position_result

    equity_result = reconcile_equity(journal_equity, broker_equity, equity_tolerance_pct)
    if not equity_result.matched:
        return equity_result

    return ReconcileResult(matched=True, reason="reconcile ผ่านทั้ง position และ equity")

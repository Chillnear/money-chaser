"""
Hard veto rules ตาม BUILD-SPEC.md ข้อ 5 (gates) + non-negotiable ข้อ 4 (risk engine เป็น veto ไม่ใช่ที่ปรึกษา)

หลักการ: ทุกกฎในนี้เป็นโค้ดกำหนดตายตัว ไม่มี LLM ตัดสินใจ ถ้าข้อใดข้อหนึ่งไม่ผ่าน -> veto เป็น FLAT ทันที
ไม่มีการต่อรอง (non-negotiable ข้อ 4) และทุก veto ต้องระบุเหตุผลเป็นข้อความ (ข้อ 5 ใน BUILD-SPEC.md)
"""
from __future__ import annotations

from dataclasses import dataclass

FUNDING_PERIODS_PER_YEAR = 3 * 365  # Hyperliquid จ่าย funding ทุก ~8 ชม. = 3 ครั้ง/วัน


@dataclass
class GateResult:
    passed: bool
    reason: str
    failed_gate: str | None = None


def check_confidence_gate(judge_confidence: float, min_judge_confidence: float) -> GateResult:
    if judge_confidence < min_judge_confidence:
        return GateResult(
            passed=False,
            reason=f"confidence ของ judge ({judge_confidence}) ต่ำกว่าเกณฑ์ {min_judge_confidence}",
            failed_gate="confidence",
        )
    return GateResult(passed=True, reason="confidence ผ่านเกณฑ์")


def check_shortlist_membership_gate(asset: str | None, shortlist_coins: set[str]) -> GateResult:
    if asset is None:
        return GateResult(passed=True, reason="ไม่มี asset ที่ต้องเช็ค (FLAT)")
    if asset not in shortlist_coins:
        return GateResult(
            passed=False,
            reason=f"judge เลือก {asset} ซึ่งไม่อยู่ใน shortlist ของวันนี้ ({sorted(shortlist_coins)})",
            failed_gate="shortlist_membership",
        )
    return GateResult(passed=True, reason=f"{asset} อยู่ใน shortlist")


def check_analyst_agreement_gate(
    judge_action: str,
    analyst_directions: list[str],
    min_analyst_agreement: int,
) -> GateResult:
    """นับ analyst ที่ "ไม่ค้าน" ทิศทางของ judge (ตาม BUILD-SPEC.md ข้อ 5)
    ไม่ค้าน = ทิศทางไม่ใช่ฝั่งตรงข้ามของ judge (flat ไม่ถือว่าค้าน)
    """
    if judge_action == "flat":
        return GateResult(passed=True, reason="judge ตัดสินใจ FLAT อยู่แล้ว ไม่ต้องเช็ค agreement")

    opposite = "short" if judge_action == "long" else "long"
    non_opposing = sum(1 for d in analyst_directions if d != opposite)

    if non_opposing < min_analyst_agreement:
        return GateResult(
            passed=False,
            reason=(
                f"มี analyst ไม่ค้านทิศทาง {judge_action} แค่ {non_opposing}/{len(analyst_directions)} "
                f"ต่ำกว่าเกณฑ์ {min_analyst_agreement}"
            ),
            failed_gate="analyst_agreement",
        )
    return GateResult(passed=True, reason=f"analyst ไม่ค้าน {non_opposing}/{len(analyst_directions)} คน")


def check_funding_gate(judge_action: str, current_funding_rate: float | None, max_funding_pct_annual: float) -> GateResult:
    """funding แพงเกิน = ห้ามเข้าฝั่งที่ต้องจ่าย (BUILD-SPEC.md ข้อ 5)
    Hyperliquid: funding บวก = long จ่าย short, funding ลบ = short จ่าย long
    """
    if judge_action == "flat" or current_funding_rate is None:
        return GateResult(passed=True, reason="ไม่ต้องเช็ค funding (FLAT หรือไม่มีข้อมูล)")

    annualized_pct = abs(current_funding_rate) * FUNDING_PERIODS_PER_YEAR * 100
    if current_funding_rate > 0:
        paying_side = "long"
    elif current_funding_rate < 0:
        paying_side = "short"
    else:
        paying_side = None

    if judge_action == paying_side and annualized_pct > max_funding_pct_annual:
        return GateResult(
            passed=False,
            reason=(
                f"funding annualized ~{annualized_pct:.1f}% เกินเกณฑ์ {max_funding_pct_annual}% "
                f"และฝั่ง {judge_action} เป็นฝั่งที่ต้องจ่าย"
            ),
            failed_gate="funding",
        )
    return GateResult(passed=True, reason=f"funding annualized ~{annualized_pct:.1f}% อยู่ในเกณฑ์")


def check_universe_whitelist_gate(asset: str | None, universe_whitelist: set[str]) -> GateResult:
    if asset is None:
        return GateResult(passed=True, reason="ไม่มี asset ที่ต้องเช็ค (FLAT)")
    if asset not in universe_whitelist:
        return GateResult(
            passed=False,
            reason=f"{asset} ไม่อยู่ใน universe whitelist ที่อนุญาต ({sorted(universe_whitelist)})",
            failed_gate="universe_whitelist",
        )
    return GateResult(passed=True, reason=f"{asset} อยู่ใน universe whitelist")


def evaluate_all_gates(
    judge_action: str,
    judge_asset: str | None,
    judge_confidence: float,
    analyst_directions: list[str],
    shortlist_coins: set[str],
    universe_whitelist: set[str],
    current_funding_rate: float | None,
    gates_cfg: dict,
    require_analyst_agreement: bool = True,
) -> GateResult:
    """รันทุก gate ตามลำดับ — เจอ veto ตัวแรกก็หยุดทันที (fail fast) คืนเหตุผลของตัวที่ veto

    require_analyst_agreement=False ใช้เฉพาะกับ decision ที่ไม่มี analyst มา debate จริง (เช่น
    src/baseline.py ตอน cost governor ตัด LLM ออกทั้งหมด) — gate อื่นทั้งหมดยังบังคับใช้เหมือนเดิม
    เพราะเป็น hard rule ของความเสี่ยง ไม่ใช่ของกระบวนการ debate
    """
    if judge_action == "flat":
        return GateResult(passed=True, reason="judge ตัดสินใจ FLAT — ไม่ต้องเช็ค gate อื่น")

    checks = [
        check_confidence_gate(judge_confidence, gates_cfg["min_judge_confidence"]),
        check_universe_whitelist_gate(judge_asset, universe_whitelist),
        check_shortlist_membership_gate(judge_asset, shortlist_coins),
    ]
    if require_analyst_agreement:
        checks.append(check_analyst_agreement_gate(judge_action, analyst_directions, gates_cfg["min_analyst_agreement"]))
    checks.append(check_funding_gate(judge_action, current_funding_rate, gates_cfg["max_funding_pct_annual"]))

    for result in checks:
        if not result.passed:
            return result

    return GateResult(passed=True, reason="ผ่านทุก gate")

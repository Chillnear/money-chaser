from __future__ import annotations

from src.risk.rules import (
    check_analyst_agreement_gate,
    check_confidence_gate,
    check_funding_gate,
    check_shortlist_membership_gate,
    check_universe_whitelist_gate,
    evaluate_all_gates,
)

GATES_CFG = {"min_judge_confidence": 60, "min_analyst_agreement": 2, "max_funding_pct_annual": 60}


def test_confidence_gate_pass_and_fail():
    assert check_confidence_gate(70, 60).passed is True
    result = check_confidence_gate(50, 60)
    assert result.passed is False
    assert result.failed_gate == "confidence"


def test_shortlist_membership_gate():
    assert check_shortlist_membership_gate("BTC", {"BTC", "ETH"}).passed is True
    result = check_shortlist_membership_gate("DOGE", {"BTC", "ETH"})
    assert result.passed is False
    assert result.failed_gate == "shortlist_membership"


def test_shortlist_membership_gate_flat_always_passes():
    assert check_shortlist_membership_gate(None, {"BTC"}).passed is True


def test_universe_whitelist_gate():
    assert check_universe_whitelist_gate("BTC", {"BTC", "ETH", "SOL", "PAXG"}).passed is True
    assert check_universe_whitelist_gate("SHIB", {"BTC", "ETH", "SOL", "PAXG"}).passed is False


def test_analyst_agreement_gate_pass():
    result = check_analyst_agreement_gate("long", ["long", "long", "short"], min_analyst_agreement=2)
    assert result.passed is True


def test_analyst_agreement_gate_fail_when_majority_opposes():
    result = check_analyst_agreement_gate("long", ["short", "short", "long"], min_analyst_agreement=2)
    assert result.passed is False
    assert result.failed_gate == "analyst_agreement"


def test_analyst_agreement_gate_flat_does_not_oppose():
    # flat ไม่ถือว่าค้าน -> long, flat, flat = ไม่ค้านทั้ง 3
    result = check_analyst_agreement_gate("long", ["long", "flat", "flat"], min_analyst_agreement=2)
    assert result.passed is True


def test_analyst_agreement_gate_skipped_when_judge_flat():
    result = check_analyst_agreement_gate("flat", ["short", "short", "short"], min_analyst_agreement=2)
    assert result.passed is True


def test_funding_gate_blocks_expensive_long():
    # funding positive สูง (long จ่าย) และ annualized เกิน 60%
    # 0.0003 * 1095 * 100 = 32.85% ต่ำกว่า 60 -> ควรผ่าน ต้องใช้ค่าที่แพงกว่านี้
    high_funding = 0.0006  # annualized ~65.7%
    result = check_funding_gate("long", high_funding, max_funding_pct_annual=60)
    assert result.passed is False
    assert result.failed_gate == "funding"


def test_funding_gate_allows_short_when_long_pays():
    high_funding = 0.0006  # long จ่าย -> short ไม่ถูกกระทบ
    result = check_funding_gate("short", high_funding, max_funding_pct_annual=60)
    assert result.passed is True


def test_funding_gate_allows_when_below_threshold():
    low_funding = 0.0001  # annualized ~10.95%
    result = check_funding_gate("long", low_funding, max_funding_pct_annual=60)
    assert result.passed is True


def test_funding_gate_skips_when_flat_or_missing():
    assert check_funding_gate("flat", 0.001, 60).passed is True
    assert check_funding_gate("long", None, 60).passed is True


def test_evaluate_all_gates_flat_shortcuts_everything():
    result = evaluate_all_gates(
        judge_action="flat",
        judge_asset=None,
        judge_confidence=10,  # ต่ำมาก แต่ไม่สำคัญเพราะ flat
        analyst_directions=["short", "short", "short"],
        shortlist_coins={"BTC"},
        universe_whitelist={"BTC", "ETH", "SOL", "PAXG"},
        current_funding_rate=0.01,
        gates_cfg=GATES_CFG,
    )
    assert result.passed is True


def test_evaluate_all_gates_passes_full_valid_case():
    result = evaluate_all_gates(
        judge_action="long",
        judge_asset="BTC",
        judge_confidence=75,
        analyst_directions=["long", "long", "flat"],
        shortlist_coins={"BTC", "ETH", "SOL"},
        universe_whitelist={"BTC", "ETH", "SOL", "PAXG"},
        current_funding_rate=0.0001,
        gates_cfg=GATES_CFG,
    )
    assert result.passed is True


def test_evaluate_all_gates_fails_fast_on_first_bad_gate():
    result = evaluate_all_gates(
        judge_action="long",
        judge_asset="BTC",
        judge_confidence=30,  # ต่ำกว่าเกณฑ์ -> ควร fail ตรงนี้ก่อนเช็คอย่างอื่น
        analyst_directions=["short", "short", "short"],
        shortlist_coins={"ETH"},  # BTC ไม่อยู่ใน shortlist ด้วย แต่ confidence ต้อง fail ก่อน
        universe_whitelist={"BTC", "ETH", "SOL", "PAXG"},
        current_funding_rate=0.01,
        gates_cfg=GATES_CFG,
    )
    assert result.passed is False
    assert result.failed_gate == "confidence"


def test_evaluate_all_gates_fails_on_asset_outside_universe():
    result = evaluate_all_gates(
        judge_action="long",
        judge_asset="SHIB",
        judge_confidence=90,
        analyst_directions=["long", "long", "long"],
        shortlist_coins={"SHIB"},
        universe_whitelist={"BTC", "ETH", "SOL", "PAXG"},
        current_funding_rate=0.0001,
        gates_cfg=GATES_CFG,
    )
    assert result.passed is False
    assert result.failed_gate == "universe_whitelist"

"""
Test ตรรกะการให้คะแนนของ scripts/bakeoff.py แบบ offline (mock LLM) — ต้องแน่ใจว่าตัวให้คะแนน
ทำงานถูกก่อน ไม่งั้นรันจริงบน GitHub Actions แล้วเสียเงินไปโดยได้ผลที่เชื่อไม่ได้
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.agents.schemas import AnalystOutput, CandidateAssessment, JudgeOutput

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "bakeoff.py"


@pytest.fixture(scope="module")
def bk():
    spec = importlib.util.spec_from_file_location("bakeoff_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(asset, direction, confidence=70, invalidation="หลุด EMA50", thesis="เหตุผล"):
    return CandidateAssessment(
        asset=asset, direction=direction, confidence=confidence, thesis=thesis, invalidation=invalidation
    )


# ---- prompt building ----


def test_prompts_build_without_unfilled_placeholders(bk):
    from src.agents.prompt_builder import find_unfilled_placeholders

    assert find_unfilled_placeholders(bk.build_redteam_prompt()) == []
    assert find_unfilled_placeholders(bk.build_judge_prompt()) == []


def test_sample_data_contains_the_intended_trap(bk):
    # ชุดข้อมูลต้องมีกับดักจริง: BTC เทรนด์แรงน่าเข้า long แต่ funding แพง + OI พุ่ง + RSI สูง
    table = bk.SAMPLE_FEATURE_TABLE
    assert "RSI: 78.30" in table
    assert "0.0180%" in table
    assert "+14.2%" in table


# ---- score_redteam_output ----


def test_redteam_score_full_coverage(bk):
    output = AnalystOutput(candidates=[_candidate("BTC", "flat"), _candidate("PAXG", "long"), _candidate("ETH", "flat")])
    score = bk.score_redteam_output(output)
    assert score["coverage"] == 1.0


def test_redteam_score_partial_coverage(bk):
    output = AnalystOutput(candidates=[_candidate("BTC", "flat")])
    score = bk.score_redteam_output(output)
    assert score["coverage"] == pytest.approx(1 / 3, abs=0.01)


def test_redteam_caught_trap_when_not_bullish_on_btc(bk):
    output = AnalystOutput(candidates=[_candidate("BTC", "flat", confidence=60)])
    assert bk.score_redteam_output(output)["caught_funding_trap"] is True


def test_redteam_caught_trap_when_long_but_low_confidence(bk):
    output = AnalystOutput(candidates=[_candidate("BTC", "long", confidence=35)])
    assert bk.score_redteam_output(output)["caught_funding_trap"] is True


def test_redteam_missed_trap_when_confidently_long_on_btc(bk):
    # โมเดลที่เชียร์ BTC long ด้วยความมั่นใจสูงทั้งที่ funding แพง+OI พุ่ง = พลาดกับดัก
    output = AnalystOutput(candidates=[_candidate("BTC", "long", confidence=85)])
    assert bk.score_redteam_output(output)["caught_funding_trap"] is False


def test_redteam_invalidation_rate(bk):
    output = AnalystOutput(
        candidates=[_candidate("BTC", "flat", invalidation="x"), _candidate("ETH", "flat", invalidation="")]
    )
    assert bk.score_redteam_output(output)["invalidation_rate"] == 0.5


def test_redteam_score_handles_missing_btc(bk):
    output = AnalystOutput(candidates=[_candidate("ETH", "flat")])
    score = bk.score_redteam_output(output)
    assert score["caught_funding_trap"] is False
    assert "ไม่ได้พูดถึง BTC" in score["btc_call"]


# ---- score_judge_output ----


def test_judge_score_valid_pick(bk):
    output = JudgeOutput(
        action="long", asset="BTC", confidence=70, stop_pct=3.0, take_profit_pct=6.0,
        reasoning="เหตุผล", why_this_over_others="เพราะ score สูงสุด", redteam_response="รับทราบข้อค้าน",
    )
    score = bk.score_judge_output(output)
    assert score["asset_in_allowed_list"] is True
    assert score["answered_redteam"] is True
    assert score["explained_why_this_over_others"] is True


def test_judge_score_flags_asset_outside_allowed_list(bk):
    output = JudgeOutput(
        action="long", asset="DOGE", confidence=70, stop_pct=3.0, take_profit_pct=6.0, reasoning="x"
    )
    assert bk.score_judge_output(output)["asset_in_allowed_list"] is False


def test_judge_score_flat_counts_as_allowed(bk):
    output = JudgeOutput(action="flat", confidence=40, stop_pct=0, take_profit_pct=0, reasoning="ไม่ชัด")
    assert bk.score_judge_output(output)["asset_in_allowed_list"] is True


def test_judge_score_detects_skipped_redteam_response(bk):
    output = JudgeOutput(
        action="flat", confidence=40, stop_pct=0, take_profit_pct=0, reasoning="x", redteam_response=""
    )
    assert bk.score_judge_output(output)["answered_redteam"] is False


# ---- run_role_bakeoff (mock LLM) ----


def test_run_role_bakeoff_counts_schema_failures(bk):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.agents.llm import LLMClient

    good = json.dumps({"candidates": [{"asset": "BTC", "direction": "flat", "confidence": 60, "thesis": "funding แพง"}]})

    def _resp(content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    # ตัวแรกตอบดี ตัวที่สองพัง -> schema_success_rate ต้องเป็น 0.5
    completion_fn = MagicMock(side_effect=[_resp(good), _resp("ไม่ใช่ json"), _resp("ยังไม่ใช่ json")])
    client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.002), max_validation_retries=0,
    )

    results = bk.run_role_bakeoff(client, "redteam", ["model-a"], "system", "user")

    assert len(results) == 1
    assert results[0]["schema_success_rate"] == 0.5
    assert results[0]["avg_cost_usd"] > 0

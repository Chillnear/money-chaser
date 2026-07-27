from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.schemas import AnalystOutput, CandidateAssessment, JudgeOutput


def test_candidate_assessment_valid():
    c = CandidateAssessment(asset="BTC", direction="long", confidence=70, thesis="เทรนด์ขึ้นแรง")
    assert c.direction == "long"


def test_candidate_assessment_rejects_bad_direction():
    with pytest.raises(ValidationError):
        CandidateAssessment(asset="BTC", direction="up", confidence=70, thesis="x")


def test_candidate_assessment_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        CandidateAssessment(asset="BTC", direction="long", confidence=150, thesis="x")


def test_analyst_output_requires_at_least_one_candidate():
    with pytest.raises(ValidationError):
        AnalystOutput(candidates=[])


def test_analyst_output_valid_multiple_candidates():
    output = AnalystOutput(
        candidates=[
            CandidateAssessment(asset="BTC", direction="long", confidence=60, thesis="a"),
            CandidateAssessment(asset="ETH", direction="flat", confidence=40, thesis="b"),
        ]
    )
    assert len(output.candidates) == 2


def test_judge_output_valid_long():
    j = JudgeOutput(action="long", asset="BTC", confidence=75, stop_pct=3.0, take_profit_pct=6.0, reasoning="x")
    assert j.asset == "BTC"


def test_judge_output_flat_does_not_require_asset():
    j = JudgeOutput(action="flat", confidence=50, stop_pct=0, take_profit_pct=0, reasoning="ไม่มีสัญญาณ")
    assert j.asset is None


def test_judge_output_long_without_asset_rejected():
    with pytest.raises(ValidationError):
        JudgeOutput(action="long", confidence=75, stop_pct=3.0, take_profit_pct=6.0, reasoning="x")


def test_judge_output_rejects_bad_action():
    with pytest.raises(ValidationError):
        JudgeOutput(action="buy", confidence=75, stop_pct=3.0, take_profit_pct=6.0, reasoning="x")

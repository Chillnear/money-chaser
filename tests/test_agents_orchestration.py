from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agents.analysts import ANALYST_ROLES, AgentRunResult, run_all_analysts, run_analyst
from src.agents.judge import (
    format_allowed_assets_markdown,
    format_hit_rate_table_markdown,
    run_judge,
)
from src.agents.llm import LLMClient
from src.agents.redteam import format_analyst_outputs_markdown, run_redteam

REGISTRY = {
    "roles": {
        "analyst_trend": {"model": "m-trend", "provider": "alibaba", "tier": "mid", "source": "litellm"},
        "analyst_positioning": {"model": "m-pos", "provider": "anthropic", "tier": "mid", "source": "litellm"},
        "analyst_macro": {"model": "m-macro", "provider": "deepseek", "tier": "mid", "source": "litellm"},
        "redteam": {"model": "m-red", "provider": "google", "tier": "cheap", "source": "litellm"},
        "judge": {"model": "m-judge", "provider": "anthropic", "tier": "frontier", "source": "litellm"},
        "reflector": {"model": "m-reflect", "provider": "openai", "tier": "frontier", "source": "litellm"},
    }
}


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _analyst_json(asset="BTC", direction="long", confidence=70):
    import json

    return json.dumps(
        {
            "candidates": [
                {
                    "asset": asset,
                    "direction": direction,
                    "confidence": confidence,
                    "thesis": "เทรนด์ขึ้นแรง",
                    "key_evidence": ["adx สูง"],
                    "invalidation": "หลุด EMA50",
                    "expected_move_pct": 3.0,
                    "horizon_days": 2,
                }
            ]
        }
    )


def _judge_json(action="long", asset="BTC", confidence=75):
    import json

    return json.dumps(
        {
            "action": action,
            "asset": asset,
            "confidence": confidence,
            "stop_pct": 3.0,
            "take_profit_pct": 6.0,
            "reasoning": "เทรนด์ชัดและ positioning ไม่สุดโต่ง",
            "why_this_over_others": "BTC มี composite score สูงสุด",
            "agreement_summary": "trend และ macro เห็นตรงกัน",
            "redteam_response": "รับทราบข้อค้านแต่ยังมั่นใจ",
            "lessons_applied": [],
        }
    )


# ---- run_analyst / run_all_analysts ----


def test_run_analyst_success():
    completion_fn = MagicMock(return_value=_fake_response(_analyst_json()))
    cost_fn = MagicMock(return_value=0.001)
    client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn, cost_fn=cost_fn
    )

    result = run_analyst("analyst_trend", client, REGISTRY, feature_table_markdown="## table")

    assert isinstance(result, AgentRunResult)
    assert result.role == "analyst_trend"
    assert result.model == "m-trend"
    assert result.provider == "alibaba"
    assert result.abstained is False
    assert result.output.candidates[0].asset == "BTC"


def test_run_analyst_rejects_invalid_role():
    client = LLMClient(base_url="https://fake.example.com", api_keys=["k1"], completion_fn=MagicMock())
    with pytest.raises(ValueError):
        run_analyst("not_a_role", client, REGISTRY, feature_table_markdown="x")


def test_run_analyst_abstains_on_bad_json_without_raising():
    completion_fn = MagicMock(return_value=_fake_response("not json"))
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        max_validation_retries=0,
    )

    result = run_analyst("analyst_macro", client, REGISTRY, feature_table_markdown="x")

    assert result.abstained is True
    assert result.output is None
    assert result.error is not None


def test_run_all_analysts_calls_all_three_roles_independently():
    completion_fn = MagicMock(return_value=_fake_response(_analyst_json()))
    cost_fn = MagicMock(return_value=0.001)
    client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn, cost_fn=cost_fn
    )

    results = run_all_analysts(client, REGISTRY, feature_table_markdown="## table", lessons_text="lesson x")

    assert [r.role for r in results] == ANALYST_ROLES
    assert all(r.abstained is False for r in results)
    assert completion_fn.call_count == 3


def test_run_all_analysts_one_abstain_does_not_block_others():
    good = _fake_response(_analyst_json())
    bad = _fake_response("garbage")
    # analyst_trend ผ่าน, analyst_positioning ล้ม, analyst_macro ผ่าน
    completion_fn = MagicMock(side_effect=[good, bad, bad, good])
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        max_validation_retries=1,
        cost_fn=MagicMock(return_value=0.001),
    )

    results = run_all_analysts(client, REGISTRY, feature_table_markdown="x")

    assert results[0].abstained is False
    assert results[1].abstained is True
    assert results[2].abstained is False


# ---- format_analyst_outputs_markdown ----


def test_format_analyst_outputs_markdown_includes_candidate_and_abstain():
    good_result = AgentRunResult(
        role="analyst_trend",
        model="m1",
        provider="alibaba",
        output=None,
        abstained=False,
        error=None,
        cost_usd=0.0,
        latency_ms=0.0,
        tokens_in=0,
        tokens_out=0,
        attempts=1,
    )
    # ใส่ output จริงผ่าน schema เพื่อให้ candidates เข้าถึงได้
    from src.agents.schemas import AnalystOutput, CandidateAssessment

    good_result.output = AnalystOutput(
        candidates=[CandidateAssessment(asset="BTC", direction="long", confidence=80, thesis="แรง")]
    )

    abstained_result = AgentRunResult(
        role="analyst_macro",
        model="m2",
        provider="deepseek",
        output=None,
        abstained=True,
        error="parse ไม่ผ่าน",
        cost_usd=0.0,
        latency_ms=0.0,
        tokens_in=0,
        tokens_out=0,
        attempts=3,
    )

    markdown = format_analyst_outputs_markdown([good_result, abstained_result])

    assert "BTC" in markdown
    assert "long" in markdown
    assert "abstain" in markdown
    assert "parse ไม่ผ่าน" in markdown


def test_format_analyst_outputs_markdown_all_abstain():
    abstained_result = AgentRunResult(
        role="analyst_trend",
        model="m1",
        provider="alibaba",
        output=None,
        abstained=True,
        error="ล้มทุก key",
        cost_usd=0.0,
        latency_ms=0.0,
        tokens_in=0,
        tokens_out=0,
        attempts=3,
    )
    markdown = format_analyst_outputs_markdown([abstained_result])
    assert "abstain" in markdown


# ---- run_redteam ----


def test_run_redteam_success():
    completion_fn = MagicMock(return_value=_fake_response(_analyst_json(asset="ETH", direction="short")))
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.001),
    )

    analyst_results = run_all_analysts(client, REGISTRY, feature_table_markdown="x")
    # analyst calls ใช้ completion_fn ตัวเดียวกันไปแล้ว 3 ครั้ง เปลี่ยน mock ใหม่สำหรับ redteam
    completion_fn.return_value = _fake_response(_analyst_json(asset="ETH", direction="short"))

    redteam_result = run_redteam(client, REGISTRY, feature_table_markdown="x", analyst_results=analyst_results)

    assert redteam_result.role == "redteam"
    assert redteam_result.model == "m-red"
    assert redteam_result.provider == "google"
    assert redteam_result.abstained is False
    assert redteam_result.output.candidates[0].asset == "ETH"


# ---- format_hit_rate_table_markdown / format_allowed_assets_markdown ----


def test_format_hit_rate_table_markdown_empty():
    text = format_hit_rate_table_markdown(None)
    assert "ยังไม่มีข้อมูล" in text


def test_format_hit_rate_table_markdown_with_data():
    text = format_hit_rate_table_markdown(
        {"analyst_trend": {"hit_rate": 0.6, "n": 20, "weight": 1.2}}
    )
    assert "analyst_trend" in text
    assert "60%" in text


def test_format_allowed_assets_markdown_empty_list():
    assert "flat" in format_allowed_assets_markdown([])


def test_format_allowed_assets_markdown_with_assets():
    assert format_allowed_assets_markdown(["BTC", "PAXG"]) == "BTC, PAXG"


# ---- run_judge ----


def _make_analyst_results():
    from src.agents.schemas import AnalystOutput, CandidateAssessment

    trend = AgentRunResult(
        role="analyst_trend",
        model="m-trend",
        provider="alibaba",
        output=AnalystOutput(candidates=[CandidateAssessment(asset="BTC", direction="long", confidence=70, thesis="x")]),
        abstained=False,
        error=None,
        cost_usd=0.001,
        latency_ms=100.0,
        tokens_in=10,
        tokens_out=10,
        attempts=1,
    )
    redteam = AgentRunResult(
        role="redteam",
        model="m-red",
        provider="google",
        output=AnalystOutput(candidates=[CandidateAssessment(asset="BTC", direction="long", confidence=55, thesis="y")]),
        abstained=False,
        error=None,
        cost_usd=0.001,
        latency_ms=100.0,
        tokens_in=10,
        tokens_out=10,
        attempts=1,
    )
    return [trend], redteam


def test_run_judge_success_with_allowed_asset():
    analyst_results, redteam_result = _make_analyst_results()
    completion_fn = MagicMock(return_value=_fake_response(_judge_json(action="long", asset="BTC")))
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.002),
    )

    result = run_judge(
        client,
        REGISTRY,
        feature_table_markdown="x",
        allowed_assets=["BTC", "PAXG"],
        analyst_results=analyst_results,
        redteam_result=redteam_result,
    )

    assert result.abstained is False
    assert result.output.action == "long"
    assert result.output.asset == "BTC"
    assert result.model == "m-judge"
    assert result.provider == "anthropic"


def test_run_judge_rejects_asset_outside_allowed_list_as_abstain():
    analyst_results, redteam_result = _make_analyst_results()
    # judge เลือก ETH แต่วันนี้อนุญาตแค่ BTC/PAXG
    completion_fn = MagicMock(return_value=_fake_response(_judge_json(action="long", asset="ETH")))
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.002),
    )

    result = run_judge(
        client,
        REGISTRY,
        feature_table_markdown="x",
        allowed_assets=["BTC", "PAXG"],
        analyst_results=analyst_results,
        redteam_result=redteam_result,
    )

    assert result.abstained is True
    assert result.output is None
    assert "ETH" in result.error


def test_run_judge_flat_action_does_not_need_asset_check():
    analyst_results, redteam_result = _make_analyst_results()
    completion_fn = MagicMock(return_value=_fake_response(_judge_json(action="flat", asset=None, confidence=40)))
    client = LLMClient(
        base_url="https://fake.example.com",
        api_keys=["k1"],
        completion_fn=completion_fn,
        cost_fn=MagicMock(return_value=0.002),
    )

    result = run_judge(
        client,
        REGISTRY,
        feature_table_markdown="x",
        allowed_assets=["BTC"],
        analyst_results=analyst_results,
        redteam_result=redteam_result,
    )

    assert result.abstained is False
    assert result.output.action == "flat"
    assert result.output.asset is None

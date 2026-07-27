from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from src.agents.llm import (
    DEGRADE_DROP_REDTEAM,
    DEGRADE_FULL,
    DEGRADE_JUDGE_ONLY,
    DEGRADE_LLM_OFF,
    DEGRADE_TWO_ANALYSTS_ONLY,
    KeyRotator,
    LLMClient,
    compute_spend,
    get_degradation_level,
    parse_json_from_text,
    roles_active_at_level,
)


class DummySchema(BaseModel):
    action: str
    value: int


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


# ---- parse_json_from_text ----


def test_parse_json_plain():
    assert parse_json_from_text('{"a": 1}') == {"a": 1}


def test_parse_json_strips_code_fence():
    text = '```json\n{"a": 1}\n```'
    assert parse_json_from_text(text) == {"a": 1}


def test_parse_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_from_text("not json at all")


# ---- KeyRotator ----


def test_key_rotator_round_robin():
    rotator = KeyRotator(["k1", "k2"])
    assert rotator.next_key() == "k1"
    assert rotator.next_key() == "k2"
    assert rotator.next_key() == "k1"


def test_key_rotator_requires_at_least_one_key():
    with pytest.raises(ValueError):
        KeyRotator([])


def test_key_rotator_all_keys_in_rotation_order():
    rotator = KeyRotator(["k1", "k2", "k3"])
    rotator.next_key()  # ขยับ index ไป 1
    assert rotator.all_keys_in_rotation_order() == ["k2", "k3", "k1"]


# ---- cost governor ----


def test_compute_spend_filters_by_time_window():
    records = [{"ts": 100, "cost_usd": 1.0}, {"ts": 200, "cost_usd": 2.0}, {"ts": 300, "cost_usd": 4.0}]
    assert compute_spend(records, since_ts=150, until_ts=300) == 2.0


def test_degradation_level_full_when_under_budget():
    assert get_degradation_level(0.1, 1.0, daily_soft_cap_usd=0.5, monthly_hard_stop_usd=15.0) == DEGRADE_FULL


def test_degradation_level_drop_redteam_when_daily_cap_hit():
    level = get_degradation_level(0.6, 5.0, daily_soft_cap_usd=0.5, monthly_hard_stop_usd=15.0)
    assert level == DEGRADE_DROP_REDTEAM


def test_degradation_level_two_analysts_at_80_pct_monthly():
    level = get_degradation_level(0.1, 12.5, daily_soft_cap_usd=0.5, monthly_hard_stop_usd=15.0)  # 83%
    assert level == DEGRADE_TWO_ANALYSTS_ONLY


def test_degradation_level_judge_only_at_90_pct_monthly():
    level = get_degradation_level(0.1, 13.6, daily_soft_cap_usd=0.5, monthly_hard_stop_usd=15.0)  # ~90.7%
    assert level == DEGRADE_JUDGE_ONLY


def test_degradation_level_llm_off_at_hard_stop():
    level = get_degradation_level(0.1, 15.0, daily_soft_cap_usd=0.5, monthly_hard_stop_usd=15.0)
    assert level == DEGRADE_LLM_OFF


def test_roles_active_at_each_level():
    assert "redteam" in roles_active_at_level(DEGRADE_FULL)
    assert "redteam" not in roles_active_at_level(DEGRADE_DROP_REDTEAM)
    assert roles_active_at_level(DEGRADE_TWO_ANALYSTS_ONLY) == ["analyst_trend", "analyst_positioning", "judge"]
    assert roles_active_at_level(DEGRADE_JUDGE_ONLY) == ["judge"]
    assert roles_active_at_level(DEGRADE_LLM_OFF) == []


# ---- LLMClient.call_structured ----


def test_call_structured_success_first_try():
    fake_response = _fake_response('{"action": "long", "value": 5}')
    completion_fn = MagicMock(return_value=fake_response)
    cost_fn = MagicMock(return_value=0.002)

    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1"],
        completion_fn=completion_fn,
        cost_fn=cost_fn,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.abstained is False
    assert result.parsed.action == "long"
    assert result.cost_usd == pytest.approx(0.002)
    assert result.attempts == 1


def test_call_structured_retries_on_bad_json_then_succeeds():
    bad_response = _fake_response("not valid json")
    good_response = _fake_response('{"action": "short", "value": 1}')
    completion_fn = MagicMock(side_effect=[bad_response, good_response])
    cost_fn = MagicMock(return_value=0.001)

    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1"],
        completion_fn=completion_fn,
        max_validation_retries=2,
        cost_fn=cost_fn,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.abstained is False
    assert result.parsed.action == "short"
    assert result.attempts == 2
    assert completion_fn.call_count == 2


def test_call_structured_abstains_after_exhausting_retries():
    bad_response = _fake_response("still not json")
    completion_fn = MagicMock(return_value=bad_response)
    cost_fn = MagicMock(return_value=0.001)

    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1"],
        completion_fn=completion_fn,
        max_validation_retries=1,
        cost_fn=cost_fn,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.abstained is True
    assert result.parsed is None
    assert completion_fn.call_count == 2  # 1 attempt แรก + retry 1 ครั้ง


def test_call_structured_falls_back_to_second_key_on_failure():
    completion_fn = MagicMock(side_effect=[Exception("rate limited"), _fake_response('{"action": "long", "value": 1}')])
    cost_fn = MagicMock(return_value=0.001)

    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1", "key2"],
        completion_fn=completion_fn,
        cost_fn=cost_fn,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.abstained is False
    assert result.parsed.value == 1


def test_call_structured_abstains_when_all_keys_fail():
    completion_fn = MagicMock(side_effect=Exception("down"))
    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1", "key2"],
        completion_fn=completion_fn,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.abstained is True
    assert "เรียกไม่ผ่านทุก key" in result.error


def test_call_structured_skips_call_when_prompt_exceeds_token_cap():
    completion_fn = MagicMock()
    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1"],
        input_token_cap=10,  # เล็กมากตั้งใจให้เกินแน่ๆ
        completion_fn=completion_fn,
    )

    result = client.call_structured("fake-model", "a" * 1000, "user", DummySchema)

    assert result.abstained is True
    assert "token cap" in result.error
    completion_fn.assert_not_called()


def test_call_structured_cost_accumulates_across_retries():
    bad_response = _fake_response("bad")
    good_response = _fake_response('{"action": "flat", "value": 0}')
    completion_fn = MagicMock(side_effect=[bad_response, good_response])
    cost_fn = MagicMock(side_effect=[0.001, 0.002])

    client = LLMClient(
        base_url="https://fake-proxy.example.com",
        api_keys=["key1"],
        completion_fn=completion_fn,
        cost_fn=cost_fn,
        max_validation_retries=2,
    )

    result = client.call_structured("fake-model", "system", "user", DummySchema)

    assert result.cost_usd == pytest.approx(0.003)

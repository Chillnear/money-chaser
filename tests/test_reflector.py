from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents.llm import LLMClient
from src.agents.reflector import ReflectorRunResult, run_reflector

REGISTRY = {
    "roles": {
        "reflector": {"model": "m-reflect", "provider": "openai", "tier": "frontier", "source": "litellm"},
    }
}


def _fake_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_run_reflector_success_returns_raw_markdown():
    lessons_md = "id: L1\ncreated: 2026-07-27\nstatus: hypothesis\n"
    completion_fn = MagicMock(return_value=_fake_response(lessons_md))
    cost_fn = MagicMock(return_value=0.01)
    client = LLMClient(base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn, cost_fn=cost_fn)

    result = run_reflector(
        client, REGISTRY, weekly_journal_markdown="journal...", closed_trades_markdown="trades...",
        current_lessons_text="(ยังไม่มี lesson)",
    )

    assert isinstance(result, ReflectorRunResult)
    assert result.abstained is False
    assert result.lessons_markdown == lessons_md
    assert result.model == "m-reflect"
    assert result.provider == "openai"


def test_run_reflector_abstains_when_all_keys_fail_without_raising():
    completion_fn = MagicMock(side_effect=Exception("down"))
    client = LLMClient(base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn)

    result = run_reflector(
        client, REGISTRY, weekly_journal_markdown="x", closed_trades_markdown="y", current_lessons_text="z"
    )

    assert result.abstained is True
    assert result.lessons_markdown is None
    assert result.error is not None

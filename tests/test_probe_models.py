"""
Test เฉพาะฟังก์ชัน pure ของ scripts/probe_models.py (ไม่แตะ network) — เน้นกันบั๊กการจัดหมวดโมเดล
ที่เคยทำให้ระบบเลือกโมเดลผิดแบบเงียบๆ มาแล้วจริง
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "probe_models.py"


@pytest.fixture(scope="module")
def pm():
    spec = importlib.util.spec_from_file_location("probe_models_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- บั๊กจริงที่เคยเกิด: "mini" เป็น substring ของ "gemini" ----


def test_gemini_pro_is_not_misclassified_as_cheap(pm):
    # บั๊กเดิม: "ge-MINI" ทำให้ Gemini ทุกรุ่นถูกติดป้าย cheap รวมถึงรุ่น pro ที่เป็นรุ่นท็อป
    assert pm.guess_tier("Gemini 3.1 pro") == "frontier"
    assert pm.guess_tier("Gemini 3.0 pro") == "frontier"
    assert pm.guess_tier("Gemini 2.5 pro") == "frontier"


def test_gemini_flash_is_still_correctly_cheap(pm):
    assert pm.guess_tier("Gemini 3.0 flash") == "cheap"
    assert pm.guess_tier("gemini-3.5-flash") == "cheap"
    assert pm.guess_tier("Gemini 2.5 flash lite") == "cheap"


def test_real_mini_models_still_detected_as_cheap(pm):
    # ต้องไม่แก้บั๊กจนพังของเดิม — "mini" ที่เป็นคำจริงต้องยังจับได้
    assert pm.guess_tier("gpt-4o-mini") == "cheap"
    assert pm.guess_tier("groq/compound-mini") == "cheap"


def test_hint_matches_respects_word_boundaries(pm):
    assert pm._hint_matches("mini", "gpt-4o-mini") is True
    assert pm._hint_matches("mini", "gemini 3.1 pro") is False


# ---- tier อื่นๆ ----


def test_opus_is_frontier(pm):
    assert pm.guess_tier("Claude Opus 4.5") == "frontier"
    assert pm.guess_tier("claude-opus-4-7") == "frontier"


def test_haiku_is_cheap(pm):
    assert pm.guess_tier("Claude Haiku 4.5") == "cheap"


def test_pro_and_max_variants_are_frontier(pm):
    assert pm.guess_tier("dashscope/deepseek-v4-pro") == "frontier"
    assert pm.guess_tier("dashscope/qwen3.7-max") == "frontier"


def test_plain_sonnet_stays_mid(pm):
    assert pm.guess_tier("Claude Sonnet 4.5") == "mid"
    assert pm.guess_tier("claude-sonnet-5") == "mid"


def test_small_param_count_models_are_cheap(pm):
    assert pm.guess_tier("llama-3.1-8b-instant") == "cheap"


def test_large_param_count_models_are_frontier(pm):
    assert pm.guess_tier("llama-3.3-70b-versatile") == "frontier"


# ---- non-chat filter ----


def test_non_chat_models_are_excluded(pm):
    for name in [
        "text-embedding-3-large",
        "Cohere-rerank-v3-5",
        "gpt-4o-transcribe",
        "gpt-image-2",
        "Nano Banana Pro",
        "meta-llama/llama-prompt-guard-2-22m",
        "gemini-3.1-flash-tts-preview",
    ]:
        assert pm.is_non_chat_model(name) is True, f"{name} ควรถูกกรองออก"


def test_real_chat_models_are_not_excluded(pm):
    for name in ["claude-opus-4-7", "Gemini 3.1 pro", "dashscope/qwen3.7-max", "glm-5.2", "gpt-5.5"]:
        assert pm.is_non_chat_model(name) is False, f"{name} ไม่ควรถูกกรองออก"


# ---- provider ----


def test_guess_provider_known_families(pm):
    assert pm.guess_provider("claude-opus-4-7") == "anthropic"
    assert pm.guess_provider("Gemini 3.1 pro") == "google"
    assert pm.guess_provider("dashscope/qwen3.7-max") == "alibaba"

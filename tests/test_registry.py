from __future__ import annotations

import yaml

from src.agents.registry import (
    RegistryError,
    assert_provider_diversity,
    get_role_model,
    load_model_registry,
)
import pytest


def _write_registry(tmp_path, roles: dict):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump({"roles": roles}), encoding="utf-8")
    return path


DIVERSE_ROLES = {
    "analyst_trend": {"model": "m1", "provider": "alibaba", "tier": "mid", "source": "litellm"},
    "analyst_positioning": {"model": "m2", "provider": "anthropic", "tier": "mid", "source": "litellm"},
    "analyst_macro": {"model": "m3", "provider": "deepseek", "tier": "mid", "source": "litellm"},
    "redteam": {"model": "m4", "provider": "google", "tier": "cheap", "source": "litellm"},
    "judge": {"model": "m5", "provider": "anthropic", "tier": "frontier", "source": "litellm"},
    "reflector": {"model": "m6", "provider": "openai", "tier": "frontier", "source": "litellm"},
}


def test_load_model_registry_missing_file_raises(tmp_path):
    with pytest.raises(RegistryError):
        load_model_registry(tmp_path / "does_not_exist.yaml")


def test_load_model_registry_missing_roles_key_raises(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump({"probed_at": 123}), encoding="utf-8")
    with pytest.raises(RegistryError):
        load_model_registry(path)


def test_load_model_registry_success(tmp_path):
    path = _write_registry(tmp_path, DIVERSE_ROLES)
    registry = load_model_registry(path)
    assert "roles" in registry


def test_get_role_model_returns_correct_fields(tmp_path):
    path = _write_registry(tmp_path, DIVERSE_ROLES)
    registry = load_model_registry(path)
    role_model = get_role_model(registry, "judge")
    assert role_model.model == "m5"
    assert role_model.provider == "anthropic"
    assert role_model.source == "litellm"


def test_get_role_model_missing_role_raises(tmp_path):
    path = _write_registry(tmp_path, DIVERSE_ROLES)
    registry = load_model_registry(path)
    with pytest.raises(RegistryError):
        get_role_model(registry, "not_a_role")


def test_assert_provider_diversity_passes_with_4_distinct_providers(tmp_path):
    path = _write_registry(tmp_path, DIVERSE_ROLES)
    registry = load_model_registry(path)
    assert_provider_diversity(registry)  # ไม่ raise ก็ถือว่าผ่าน


def test_assert_provider_diversity_fails_when_same_provider_repeated(tmp_path):
    same_provider_roles = dict(DIVERSE_ROLES)
    same_provider_roles["analyst_positioning"] = {"model": "m2", "provider": "alibaba", "tier": "mid", "source": "litellm"}
    same_provider_roles["analyst_macro"] = {"model": "m3", "provider": "alibaba", "tier": "mid", "source": "litellm"}
    same_provider_roles["redteam"] = {"model": "m4", "provider": "alibaba", "tier": "mid", "source": "litellm"}
    path = _write_registry(tmp_path, same_provider_roles)
    registry = load_model_registry(path)

    with pytest.raises(RegistryError):
        assert_provider_diversity(registry)


def test_assert_provider_diversity_fails_when_role_missing(tmp_path):
    incomplete_roles = dict(DIVERSE_ROLES)
    del incomplete_roles["redteam"]
    path = _write_registry(tmp_path, incomplete_roles)
    registry = load_model_registry(path)

    with pytest.raises(RegistryError):
        assert_provider_diversity(registry)


def test_assert_provider_diversity_exactly_at_minimum_passes(tmp_path):
    # 3 ค่ายพอดี (alibaba, anthropic, deepseek) สำหรับ 4 role (ตัวหนึ่งซ้ำ)
    roles = dict(DIVERSE_ROLES)
    roles["redteam"] = {"model": "m4", "provider": "deepseek", "tier": "mid", "source": "litellm"}
    path = _write_registry(tmp_path, roles)
    registry = load_model_registry(path)
    assert_provider_diversity(registry)

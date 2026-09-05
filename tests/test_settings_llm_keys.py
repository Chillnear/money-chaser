from src.settings import Secrets


def test_mimi_coach_key_takes_precedence_without_mixing_entitlements():
    secrets = Secrets(
        litellm_base_url="https://fake.example.com",
        litellm_key_1="legacy-1",
        litellm_key_2="legacy-2",
        mimi_coach_key="mimi",
        mimi_coach_base_url="https://mimi.example.com/",
    )

    assert secrets.llm_api_keys() == ["mimi"]
    assert secrets.llm_base_url() == "https://mimi.example.com"


def test_legacy_keys_remain_as_fallback():
    secrets = Secrets(
        litellm_base_url="https://fake.example.com",
        litellm_key_1="legacy-1",
        litellm_key_2="legacy-2",
    )

    assert secrets.llm_api_keys() == ["legacy-1", "legacy-2"]
    assert secrets.llm_base_url() == "https://fake.example.com"


def test_mimi_key_without_its_endpoint_is_rejected():
    secrets = Secrets(
        litellm_base_url="https://legacy.example.com",
        litellm_key_1="legacy-1",
        litellm_key_2="legacy-2",
        mimi_coach_key="mimi",
    )

    try:
        secrets.llm_base_url()
    except ValueError as exc:
        assert "MIMI_COACH_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected Mimi profile without an endpoint to fail")

from src.settings import Secrets


def test_mimi_coach_key_takes_precedence_without_mixing_entitlements():
    secrets = Secrets(
        litellm_base_url="https://fake.example.com",
        litellm_key_1="legacy-1",
        litellm_key_2="legacy-2",
        mimi_coach_key="mimi",
    )

    assert secrets.llm_api_keys() == ["mimi"]


def test_legacy_keys_remain_as_fallback():
    secrets = Secrets(
        litellm_base_url="https://fake.example.com",
        litellm_key_1="legacy-1",
        litellm_key_2="legacy-2",
    )

    assert secrets.llm_api_keys() == ["legacy-1", "legacy-2"]

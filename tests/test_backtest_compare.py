from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def btc_module():
    spec = importlib.util.spec_from_file_location("backtest_module_for_compare", SCRIPTS_DIR / "backtest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bc(btc_module):
    spec = importlib.util.spec_from_file_location("backtest_compare_under_test", SCRIPTS_DIR / "backtest_compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _make_calendar_candles(start_date: str, n_days: int, start_price: float = 100.0, daily_pct: float = 0.0) -> list[dict]:
    base = dt.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    candles = []
    price = start_price
    for i in range(n_days):
        open_price = price
        close_price = price * (1 + daily_pct / 100)
        day_start_ms = int((base + dt.timedelta(days=i)).timestamp() * 1000)
        day_end_ms = int((base + dt.timedelta(days=i + 1)).timestamp() * 1000)
        candles.append(
            {
                "t": day_start_ms, "T": day_end_ms, "o": open_price,
                "h": max(open_price, close_price) * 1.002, "l": min(open_price, close_price) * 0.998,
                "c": close_price, "v": 1000.0,
            }
        )
        price = close_price
    return candles


def _fake_response(content: str, cached: bool = False):
    ns = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    if cached:
        ns._from_cache = True
    return ns


def _analyst_flat_json():
    return json.dumps({"candidates": [{"asset": "BTC", "direction": "flat", "confidence": 40, "thesis": "เฉยๆ", "invalidation": "-"}]})


def _judge_flat_json():
    return json.dumps(
        {"action": "flat", "asset": None, "confidence": 40, "stop_pct": 0, "take_profit_pct": 0,
         "reasoning": "-", "why_this_over_others": "-", "redteam_response": "-"}
    )


def _settings():
    from src.settings import AppConfig, RiskConfig, Secrets, Settings

    return Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )


# ---- CachedCompletionFn ----


def test_cached_completion_fn_calls_real_fn_on_first_miss(bc, tmp_path):
    real_fn = MagicMock(return_value=_fake_response("hello"))
    cached = bc.CachedCompletionFn(real_fn, tmp_path / "cache.json")

    result = cached(model="m", messages=[{"role": "user", "content": "hi"}])

    assert result.choices[0].message.content == "hello"
    assert real_fn.call_count == 1
    assert cached.misses == 1
    assert cached.hits == 0


def test_cached_completion_fn_reuses_cache_on_identical_call(bc, tmp_path):
    real_fn = MagicMock(return_value=_fake_response("hello"))
    cached = bc.CachedCompletionFn(real_fn, tmp_path / "cache.json")
    messages = [{"role": "user", "content": "hi"}]

    cached(model="m", messages=messages)
    result2 = cached(model="m", messages=messages)

    assert result2.choices[0].message.content == "hello"
    assert real_fn.call_count == 1  # เรียกจริงแค่ครั้งเดียว
    assert cached.hits == 1
    assert getattr(result2, "_from_cache", False) is True


def test_cached_completion_fn_different_messages_are_different_cache_keys(bc, tmp_path):
    real_fn = MagicMock(side_effect=[_fake_response("a"), _fake_response("b")])
    cached = bc.CachedCompletionFn(real_fn, tmp_path / "cache.json")

    r1 = cached(model="m", messages=[{"role": "user", "content": "hi"}])
    r2 = cached(model="m", messages=[{"role": "user", "content": "bye"}])

    assert r1.choices[0].message.content == "a"
    assert r2.choices[0].message.content == "b"
    assert real_fn.call_count == 2


def test_cached_completion_fn_persists_across_instances(bc, tmp_path):
    cache_path = tmp_path / "cache.json"
    real_fn1 = MagicMock(return_value=_fake_response("persisted"))
    cached1 = bc.CachedCompletionFn(real_fn1, cache_path)
    cached1(model="m", messages=[{"role": "user", "content": "hi"}])

    real_fn2 = MagicMock()
    cached2 = bc.CachedCompletionFn(real_fn2, cache_path)  # instance ใหม่ อ่านจากดิสก์
    result = cached2(model="m", messages=[{"role": "user", "content": "hi"}])

    assert result.choices[0].message.content == "persisted"
    real_fn2.assert_not_called()


# ---- make_cached_cost_fn ----


def test_cached_cost_fn_returns_zero_for_cache_hits(bc):
    real_cost_fn = MagicMock(return_value=0.05)
    cost_fn = bc.make_cached_cost_fn(real_cost_fn)

    cost = cost_fn(completion_response=_fake_response("x", cached=True))

    assert cost == 0.0
    real_cost_fn.assert_not_called()


def test_cached_cost_fn_delegates_for_real_calls(bc):
    real_cost_fn = MagicMock(return_value=0.05)
    cost_fn = bc.make_cached_cost_fn(real_cost_fn)

    cost = cost_fn(completion_response=_fake_response("x", cached=False))

    assert cost == 0.05
    real_cost_fn.assert_called_once()


# ---- apply_stops_override ----


def test_apply_stops_override_changes_only_targeted_fields():
    settings = _settings()
    original_holding_days = settings.risk.stops.max_holding_days

    new_settings = None
    import importlib.util

    spec = importlib.util.spec_from_file_location("bc2", SCRIPTS_DIR / "backtest_compare.py")
    bc_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc_mod)

    new_settings = bc_mod.apply_stops_override(settings, {"reward_risk_ratio": 1.0})

    assert new_settings.risk.stops.reward_risk_ratio == 1.0
    assert new_settings.risk.stops.max_holding_days == original_holding_days
    assert settings.risk.stops.reward_risk_ratio != 1.0  # ต้นฉบับต้องไม่ถูกแก้ (immutable copy)


# ---- run_comparison (mocked LLM, cheap FLAT-only days) ----


def test_run_comparison_reuses_cache_across_variants(bc, btc_module, tmp_path):
    from src.agents.llm import LLMClient

    settings = _settings()
    candles = _make_calendar_candles("2025-10-01", n_days=40, daily_pct=0.0)
    hist_client = btc_module.HistoricalHyperliquidClient({"BTC": candles, "PAXG": candles}, {"BTC": [], "PAXG": []})

    responses = [_fake_response(_analyst_flat_json())] * 4 + [_fake_response(_judge_flat_json())]
    real_fn = MagicMock(side_effect=lambda **kw: responses[real_fn.call_count % len(responses)])
    cached_completion = bc.CachedCompletionFn(real_fn, tmp_path / "cache.json")
    cached_cost = bc.make_cached_cost_fn(MagicMock(return_value=0.001))

    llm_client = LLMClient(
        base_url="https://fake.example.com", api_keys=["k1"],
        completion_fn=cached_completion, cost_fn=cached_cost,
    )

    small_variants = {"baseline": {}, "rr_1.0": {"reward_risk_ratio": 1.0}}
    results = bc.run_comparison(
        base_settings=settings, cached_llm_client=llm_client, model_registry=REGISTRY,
        hist_client=hist_client, start_date="2025-12-30", end_date="2026-01-01",
        starting_equity_usd=28.0, compare_root_dir=tmp_path / "compare", variants=small_variants,
    )

    assert set(results.keys()) == {"baseline", "rr_1.0"}
    for name, summary in results.items():
        assert summary["days_simulated"] == 3
        assert summary["stops_override"] == small_variants[name]

    # variant ที่สองต้องแทบไม่เรียก AI จริงเพิ่มเลย (cache hit สูง)
    assert cached_completion.hits > 0
    misses_after_both_variants = cached_completion.misses
    assert misses_after_both_variants <= 15  # จำนวน miss ควรมาจาก variant แรกเป็นหลักเท่านั้น

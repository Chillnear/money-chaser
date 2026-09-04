from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_role_health.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_role_health_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY = {
    "roles": {
        "analyst_trend": {"model": "m1", "provider": "a", "tier": "mid", "source": "litellm"},
        "redteam": {"model": "m2", "provider": "b", "tier": "mid", "source": "litellm"},
    }
}


class FakeClient:
    def __init__(self, failing_model: str | None = None):
        self.failing_model = failing_model

    def call_structured(self, **kwargs):
        failed = kwargs["model"] == self.failing_model
        return SimpleNamespace(
            abstained=failed,
            parsed=None if failed else SimpleNamespace(ok=True),
            latency_ms=12.3,
            attempts=1,
            error="denied" if failed else None,
        )


def test_probe_roles_all_healthy():
    report = _module().probe_roles(FakeClient(), REGISTRY, ["analyst_trend", "redteam"], now_ts=123.0)
    assert report["healthy"] is True
    assert report["unhealthy_roles"] == []
    assert report["checked_at_ts"] == 123.0


def test_probe_roles_reports_unhealthy_for_baseline_fallback():
    report = _module().probe_roles(
        FakeClient(failing_model="m2"), REGISTRY, ["analyst_trend", "redteam"], now_ts=123.0
    )
    assert report["healthy"] is False
    assert report["unhealthy_roles"] == ["redteam"]
    assert report["results"][1]["error"] == "denied"

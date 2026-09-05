"""ตรวจ model entitlement + structured JSON ของ role ที่ใช้จริงก่อน daily pipeline.

สคริปต์นี้ตั้งใจ exit 0 เสมอเมื่อ probe จบ เพื่อให้ ``src.main`` เป็นผู้ตัดสินใจ fallback
ไป deterministic baseline อย่างปลอดภัย แทนการทำให้ workflow หยุดก่อนบันทึก journal/แจ้งเตือน.
การขาด secret/config ยังถือเป็น setup error และ exit non-zero ตามปกติ.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agents.llm import LLMClient  # noqa: E402
from src.agents.registry import get_role_model, load_model_registry  # noqa: E402
from src.settings import CONFIG_DIR, STATE_DIR  # noqa: E402
from src.util.io import append_jsonl  # noqa: E402

DAILY_ROLES = ["analyst_trend", "analyst_positioning", "analyst_macro", "redteam", "judge"]


class HealthResponse(BaseModel):
    ok: Literal[True]


def probe_roles(client: LLMClient, registry: dict, roles: list[str], now_ts: float | None = None) -> dict:
    results = []
    for role in roles:
        role_model = get_role_model(registry, role)
        result = client.call_structured(
            model=role_model.model,
            system_prompt="You are a JSON health check. Return valid JSON only.",
            user_prompt='Return exactly {"ok":true}.',
            schema=HealthResponse,
            is_groq=role_model.source == "groq",
        )
        results.append(
            {
                "role": role,
                "model": role_model.model,
                "provider": role_model.provider,
                "healthy": not result.abstained and result.parsed is not None,
                "latency_ms": round(result.latency_ms, 1),
                "cost_usd": float(getattr(result, "cost_usd", 0.0) or 0.0),
                "attempts": result.attempts,
                "error": result.error,
            }
        )

    unhealthy = [r["role"] for r in results if not r["healthy"]]
    return {
        "checked_at_ts": now_ts if now_ts is not None else time.time(),
        "healthy": not unhealthy,
        "unhealthy_roles": unhealthy,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe configured daily LLM roles with structured JSON")
    parser.add_argument("--roles", default=",".join(DAILY_ROLES))
    args = parser.parse_args()

    registry = load_model_registry(CONFIG_DIR / "models.yaml")
    mimi_key = os.environ.get("MIMI_COACH_KEY")
    keys = [mimi_key] if mimi_key else [
        k for k in [os.environ.get("LITELLM_KEY_1"), os.environ.get("LITELLM_KEY_2")] if k
    ]
    if not keys:
        raise RuntimeError("ไม่พบ MIMI_COACH_KEY หรือ LITELLM_KEY_1/LITELLM_KEY_2 สำหรับ role health check")

    base_url = os.environ.get("MIMI_COACH_BASE_URL") if mimi_key else os.environ["LITELLM_BASE_URL"]
    if mimi_key and not base_url:
        raise RuntimeError("ตั้ง MIMI_COACH_KEY แล้วต้องตั้ง MIMI_COACH_BASE_URL ด้วย")

    client = LLMClient(
        base_url=base_url,
        api_keys=keys,
        input_token_cap=500,
        output_token_cap=256,
        timeout_sec=45,
        max_validation_retries=1,
    )
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    report = probe_roles(client, registry, roles)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "model_health.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checked_at = float(report["checked_at_ts"])
    date_utc = dt.datetime.fromtimestamp(checked_at, tz=dt.timezone.utc).strftime("%Y-%m-%d")
    for item in report["results"]:
        append_jsonl(
            STATE_DIR / "journal" / "llm_cost.jsonl",
            {
                "ts": checked_at,
                "date": date_utc,
                "role": f"health:{item['role']}",
                "model": item["model"],
                "provider": item["provider"],
                "cost_usd": item["cost_usd"],
                "latency_ms": item["latency_ms"],
                "tokens_in": 0,
                "tokens_out": 0,
                "attempts": item["attempts"],
                "abstained": not item["healthy"],
            },
        )

    for item in report["results"]:
        icon = "OK" if item["healthy"] else "FAIL"
        print(f"[{icon}] {item['role']}: {item['model']} ({item['latency_ms']} ms)")
        if item["error"]:
            print(f"  {item['error']}")
    if not report["healthy"]:
        print(f"[fallback] unhealthy roles={report['unhealthy_roles']} -> daily pipeline will use baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

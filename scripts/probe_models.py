"""
Probe โมเดลที่ใช้ได้จริงจาก LiteLLM proxy (2 keys) + Groq โดยตรง แล้วเขียน config/models.yaml

**ต้องรันสคริปต์นี้ในเครื่อง/สภาพแวดล้อมที่มี network เปิดจริง** — sandbox ที่ใช้พัฒนาโค้ดนี้บล็อก
outbound ไปโดเมนภายนอกทั้งหมด (ยืนยันแล้วว่า scgc-llmproxy.scg.com และ api.groq.com ก็โดนบล็อกเหมือนกัน)
เขียนโลจิกไว้ให้ครบตามสเปก แต่ยังไม่ได้ยิงจริงจากที่นี่

วิธีรัน:
    source .venv/bin/activate  (หรือ activate venv ที่ลง requirements.txt แล้ว)
    python scripts/probe_models.py

ผลลัพธ์:
    - พิมพ์ตารางโมเดลที่ probe ได้ + latency + provider ที่เดาได้ ลง stdout
    - เขียน config/models.yaml (ทับของเดิม) พร้อม mapping role -> model
    - เขียน state/model_probe.json เก็บผลดิบทั้งหมดไว้ตรวจสอบย้อนหลังได้

ความเสี่ยงที่ตั้งใจเปิดเผย: การเดา provider/tier จากชื่อโมเดลเป็น heuristic ไม่ใช่ความจริงเสมอไป
ให้ **อ่านตารางที่พิมพ์ออกมาแล้วตรวจด้วยตาก่อน** ว่า role ทั้ง 4 (analyst_trend, analyst_positioning,
analyst_macro, redteam) ได้โมเดลจากค่ายที่ต่างกันจริงตามที่ non-negotiable ข้อ 4.1 ของ BUILD-SPEC.md กำหนด
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

CONFIG_DIR = REPO_ROOT / "config"
STATE_DIR = REPO_ROOT / "state"

# heuristic จับ provider จากชื่อ/prefix ของโมเดล — ปรับเพิ่มได้ถ้าเจอ provider แปลกๆ
PROVIDER_HINTS = [
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("llama", "meta"),
    ("mixtral", "mistral"),
    ("mistral", "mistral"),
    ("deepseek", "deepseek"),
    ("qwen", "alibaba"),
    ("grok", "xai"),
]

# heuristic จับ tier — frontier = รุ่นเรือธง, mid = รุ่นกลาง, cheap = รุ่นเล็ก/เร็ว
FRONTIER_HINTS = ["opus", "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "gemini-1.5-pro", "gemini-2", "405b", "70b"]
CHEAP_HINTS = ["haiku", "mini", "flash", "8b", "nano", "small"]


def guess_provider(model_name: str) -> str:
    lower = model_name.lower()
    for hint, provider in PROVIDER_HINTS:
        if hint in lower:
            return provider
    return "unknown"


def guess_tier(model_name: str) -> str:
    lower = model_name.lower()
    if any(h in lower for h in FRONTIER_HINTS):
        return "frontier"
    if any(h in lower for h in CHEAP_HINTS):
        return "cheap"
    return "mid"


def fetch_litellm_models(base_url: str, api_key: str) -> list[dict]:
    resp = requests.get(
        f"{base_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data.get("models", []))


def fetch_groq_models(api_key: str) -> list[dict]:
    resp = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def test_model_smoke(base_url: str, api_key: str, model_id: str, is_groq: bool = False) -> dict:
    """ยิง completion สั้นๆ 1 ครั้งเพื่อวัด latency และดูว่าเรียกได้จริง (ไม่ใช่แค่ list เฉยๆ)"""
    url = "https://api.groq.com/openai/v1/chat/completions" if is_groq else f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ตอบคำเดียว: OK"}],
        "max_tokens": 10,
    }
    start = time.time()
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        latency_ms = round((time.time() - start) * 1000, 1)
        ok = resp.status_code == 200
        usage = resp.json().get("usage", {}) if ok else {}
        return {"ok": ok, "status_code": resp.status_code, "latency_ms": latency_ms, "usage": usage}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc), "latency_ms": None}


def main() -> None:
    base_url = os.environ["LITELLM_BASE_URL"]
    key1 = os.environ["LITELLM_KEY_1"]
    key2 = os.environ["LITELLM_KEY_2"]
    groq_key = os.environ.get("GROQ_API_KEY", "")

    print(f"Probe LiteLLM: {base_url}")
    models_raw = fetch_litellm_models(base_url, key1)
    litellm_model_ids = sorted({m.get("id") or m.get("model") for m in models_raw if m.get("id") or m.get("model")})
    print(f"  พบ {len(litellm_model_ids)} โมเดลจาก LiteLLM proxy")

    groq_model_ids: list[str] = []
    if groq_key:
        print("Probe Groq (โดยตรง)...")
        groq_raw = fetch_groq_models(groq_key)
        groq_model_ids = sorted({m.get("id") for m in groq_raw if m.get("id")})
        print(f"  พบ {len(groq_model_ids)} โมเดลจาก Groq")

    results = []
    keys_cycle = [key1, key2]
    for i, model_id in enumerate(litellm_model_ids):
        api_key = keys_cycle[i % 2]
        smoke = test_model_smoke(base_url, api_key, model_id, is_groq=False)
        results.append(
            {
                "model": model_id,
                "source": "litellm",
                "provider_guess": guess_provider(model_id),
                "tier_guess": guess_tier(model_id),
                **smoke,
            }
        )

    for model_id in groq_model_ids:
        smoke = test_model_smoke(base_url, groq_key, model_id, is_groq=True)
        results.append(
            {
                "model": model_id,
                "source": "groq",
                "provider_guess": "groq/" + guess_provider(model_id),
                "tier_guess": guess_tier(model_id),
                **smoke,
            }
        )

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "model_probe.json", "w", encoding="utf-8") as f:
        json.dump({"probed_at": time.time(), "results": results}, f, indent=2, ensure_ascii=False)

    print("\n=== ผลลัพธ์ (เรียงตาม provider) ===")
    working = [r for r in results if r.get("ok")]
    working.sort(key=lambda r: (r["provider_guess"], r["tier_guess"]))
    for r in working:
        print(f"  {r['provider_guess']:>15} | {r['tier_guess']:>8} | {r['latency_ms']:>7} ms | {r['model']}")

    failed = [r for r in results if not r.get("ok")]
    if failed:
        print(f"\n⚠️  {len(failed)} โมเดลเรียกไม่ผ่าน (ดูรายละเอียดใน state/model_probe.json)")

    providers_available = sorted({r["provider_guess"] for r in working})
    print(f"\nค่ายที่ใช้งานได้จริง: {providers_available}")
    if len(providers_available) < 3:
        print(
            "⚠️  มีค่ายที่ใช้งานได้จริงน้อยกว่า 3 ค่าย — non-negotiable ข้อ 4.1 (ต้องมี provider diversity "
            ">=3 สำหรับ 4 role แรก) จะไม่ผ่าน ต้องแก้ config ให้ยอมรับ diversity ที่ต่ำกว่านี้ชั่วคราว "
            "หรือหา key/proxy เพิ่ม"
        )

    # เลือกโมเดลแบบง่ายๆ: กระจาย role ไปตาม provider ที่ต่างกัน, judge/reflector เลือกตัวที่ tier=frontier
    # และ latency ต่ำสุดในกลุ่ม frontier ถ้าไม่มีก็ fallback เป็น mid ที่ดีที่สุดที่มี
    def pick(provider_pool: list[dict], tier: str | None = None) -> dict | None:
        pool = provider_pool if tier is None else [r for r in provider_pool if r["tier_guess"] == tier]
        if not pool:
            pool = provider_pool
        return min(pool, key=lambda r: r["latency_ms"] or 9999) if pool else None

    by_provider: dict[str, list[dict]] = {}
    for r in working:
        by_provider.setdefault(r["provider_guess"], []).append(r)

    provider_order = list(by_provider.keys())
    roles_needing_diversity = ["analyst_trend", "analyst_positioning", "analyst_macro", "redteam"]
    role_assignment = {}
    for i, role in enumerate(roles_needing_diversity):
        if not provider_order:
            break
        provider = provider_order[i % len(provider_order)]
        picked = pick(by_provider[provider], tier="mid")
        if picked:
            role_assignment[role] = picked

    frontier_pool = [r for r in working if r["tier_guess"] == "frontier"] or working
    if frontier_pool:
        best_frontier = min(frontier_pool, key=lambda r: r["latency_ms"] or 9999)
        role_assignment["judge"] = best_frontier
        role_assignment["reflector"] = best_frontier

    models_yaml = {
        "roles": {
            role: {
                "model": r["model"],
                "tier": r["tier_guess"],
                "provider": r["provider_guess"],
                "source": r["source"],
            }
            for role, r in role_assignment.items()
        },
        "probed_at": time.time(),
        "provider_diversity_ok": len(providers_available) >= 3,
        "providers_available": providers_available,
    }

    with open(CONFIG_DIR / "models.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(models_yaml, f, allow_unicode=True, sort_keys=False)

    print("\nเขียน config/models.yaml แล้ว — เปิดไฟล์ดูให้แน่ใจก่อนใช้งานจริง (heuristic อาจเดาผิด)")


if __name__ == "__main__":
    main()

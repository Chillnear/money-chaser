"""
สคริปต์ตรวจ setup ทั้งหมดแบบรวดเดียว — ออกแบบให้รันบน GitHub Actions (มี network เปิดจริง)
เพราะ sandbox ที่ใช้พัฒนาโค้ดนี้เข้าเน็ตภายนอกไม่ได้เลย

ตรวจ 3 อย่าง:
  1. Hyperliquid public info API เข้าถึงได้ไหม + ดึงยอดบัญชี main wallet ได้ไหม (read-only, ไม่ใช้ private key)
  2. LiteLLM proxy เข้าถึงได้ไหม + list โมเดลได้ไหม
  3. Groq เข้าถึงได้ไหม (ถ้ามี key)

**ไม่พิมพ์ secret ใดๆ ออกมาเลย** (private key, API key) — พิมพ์แค่ผลลัพธ์ที่ปลอดภัย เพราะ log ของ
GitHub Actions อาจเป็น public repo ที่ใครก็เห็นได้
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from src.data.hl_market import HyperliquidAPIError, HyperliquidClient  # noqa: E402


def check_hyperliquid() -> bool:
    print("=== 1. Hyperliquid public info API ===")
    main_address = os.environ.get("HL_MAIN_ADDRESS", "")
    if not main_address:
        print("  ⚠️  ไม่พบ HL_MAIN_ADDRESS ใน .env — ข้ามการเช็คยอดบัญชี")
        return False
    try:
        client = HyperliquidClient()
        mids = client.get_all_mids()
        btc_price = mids.get("BTC", "ไม่พบ")
        print(f"  ✅ ดึงราคาได้ (BTC mid ≈ {btc_price})")

        state = client.get_clearinghouse_state(main_address)
        account_value = state.get("marginSummary", {}).get("accountValue", "0")
        print(f"  ✅ ดึงสถานะบัญชีของ main wallet ได้ (accountValue ≈ {account_value} USD)")

        snapshot = client.get_universe_snapshot()
        paxg = next((s for s in snapshot if s["coin"] == "PAXG"), None)
        print(f"  ✅ ดึงรายชื่อตลาดทั้งหมดได้ {len(snapshot)} ตลาด (PAXG พบ: {'ใช่' if paxg else 'ไม่พบ'})")
        return True
    except HyperliquidAPIError as exc:
        print(f"  ❌ เชื่อมต่อ Hyperliquid ไม่สำเร็จ: {exc}")
        return False


def check_litellm() -> bool:
    print("\n=== 2. LiteLLM proxy ===")
    base_url = os.environ.get("LITELLM_BASE_URL", "")
    key1 = os.environ.get("LITELLM_KEY_1", "")
    if not base_url or not key1:
        print("  ⚠️  ไม่พบ LITELLM_BASE_URL/LITELLM_KEY_1 — ข้าม")
        return False
    import requests

    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {key1}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", data.get("models", []))
        print(f"  ✅ เข้าถึงได้ พบ {len(models)} โมเดล")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ เชื่อมต่อ LiteLLM ไม่สำเร็จ: {exc}")
        return False


def check_groq() -> bool:
    print("\n=== 3. Groq (โดยตรง) ===")
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        print("  ⚠️  ไม่พบ GROQ_API_KEY — ข้าม (ไม่บังคับ)")
        return True
    import requests

    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {groq_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])
        print(f"  ✅ เข้าถึงได้ พบ {len(models)} โมเดล")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ เชื่อมต่อ Groq ไม่สำเร็จ: {exc}")
        return False


if __name__ == "__main__":
    results = [check_hyperliquid(), check_litellm(), check_groq()]
    print("\n=== สรุป ===")
    if all(results):
        print("✅ ทุกจุดเชื่อมต่อได้ พร้อมไปขั้นต่อไป (P0.3 probe_models.py แบบเต็ม)")
        sys.exit(0)
    else:
        print("❌ มีบางจุดเชื่อมต่อไม่ได้ — ดู log ด้านบนแล้วแก้ก่อนไปขั้นต่อไป")
        sys.exit(1)

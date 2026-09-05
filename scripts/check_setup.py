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
    mimi_key = os.environ.get("MIMI_COACH_KEY", "")
    base_url = os.environ.get("MIMI_COACH_BASE_URL", "") if mimi_key else os.environ.get("LITELLM_BASE_URL", "")
    key1 = mimi_key or os.environ.get("LITELLM_KEY_1", "")
    if not base_url or not key1:
        print("  ⚠️  ไม่พบ endpoint/key ของ credential profile ที่เลือก — ข้าม")
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


def check_macro() -> bool:
    """Macro เป็น best-effort ล้วนๆ (ใช้แค่เสริม context ให้ analyst_macro) — ไม่ทำให้ผลรวม fail
    แม้ดึงไม่ได้เลยสักตัว เพราะ macro.py เองก็ออกแบบให้ทำงานต่อได้โดยไม่มี macro data (data_missing flag)
    """
    print("\n=== 4. Macro data (Yahoo Finance, best-effort) ===")
    from src.data.macro import MacroClient

    client = MacroClient()
    snapshot = client.get_macro_snapshot()
    ok_count = sum(1 for k, v in snapshot.items() if isinstance(v, dict) and v.get("ok"))
    total = len([k for k in snapshot if k not in ("missing", "data_missing")])
    if snapshot.get("missing"):
        print(f"  ⚠️  ดึงได้ {ok_count}/{total} — ขาด: {snapshot['missing']} (ไม่ critical, ไม่กระทบผลรวม)")
    else:
        print(f"  ✅ ดึงได้ครบ {ok_count}/{total}")
    return True  # ไม่ critical เสมอ ต่างจาก Hyperliquid/LiteLLM ที่จำเป็นต้องต่อได้จริง


def check_sentiment() -> bool:
    print("\n=== 5. Fear & Greed Index (alternative.me) ===")
    from src.data.sentiment import SentimentClient

    client = SentimentClient()
    result = client.get_fear_greed()
    if result["ok"]:
        print(f"  ✅ ดึงได้ (value={result['value']}, {result['classification']})")
        return True
    print(f"  ❌ ดึงไม่ได้: {result.get('error')}")
    return False


def check_news() -> bool:
    print("\n=== 6. News RSS (หลายแหล่ง) ===")
    from src.data.news import NewsClient, merge_with_cryptopanic

    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://www.theblock.co/rss.xml",
        "https://bitcoinmagazine.com/feed",
        "https://cryptoslate.com/feed/",
    ]
    client = NewsClient(feed_urls=feeds)
    snapshot = client.get_recent_headlines()
    if snapshot.sources_failed:
        print(f"  ⚠️  บาง feed ล่ม ({len(snapshot.sources_failed)}/{len(feeds)}): {snapshot.sources_failed}")
    print(f"  {'✅' if snapshot.count > 0 else '⚠️ '} RSS เจอ {snapshot.count} หัวข้อข่าวใน 24 ชม.ล่าสุด")

    print("\n=== 7. CryptoPanic (optional) ===")
    cryptopanic_key = os.environ.get("CRYPTOPANIC_API_KEY", "")
    if not cryptopanic_key:
        print("  ⚠️  ไม่พบ CRYPTOPANIC_API_KEY — ข้าม (ไม่บังคับ, สมัครได้ที่ cryptopanic.com/developers/api/keys)")
    else:
        from src.data.cryptopanic import CryptoPanicClient

        cp_client = CryptoPanicClient(auth_token=cryptopanic_key)
        cp_snapshot = cp_client.get_recent_posts()
        if cp_snapshot.ok:
            print(f"  ✅ เข้าถึงได้ พบ {cp_snapshot.count} ข่าว")
            merged = merge_with_cryptopanic(snapshot, cp_snapshot.posts)
            print(f"  รวมกับ RSS แล้วได้ {merged.count} หัวข้อไม่ซ้ำ")
        else:
            print(f"  ❌ เชื่อมต่อไม่สำเร็จ: {cp_snapshot.error}")

    return snapshot.count > 0 or len(snapshot.sources_failed) < len(feeds)


if __name__ == "__main__":
    results = [
        check_hyperliquid(),
        check_litellm(),
        check_groq(),
        check_macro(),
        check_sentiment(),
        check_news(),
    ]
    print("\n=== สรุป ===")
    if all(results):
        print("✅ ทุกจุดเชื่อมต่อได้ พร้อมไปขั้นต่อไป (P0.3 probe_models.py แบบเต็ม)")
        sys.exit(0)
    else:
        print("❌ มีบางจุดเชื่อมต่อไม่ได้ — ดู log ด้านบนแล้วแก้ก่อนไปขั้นต่อไป")
        sys.exit(1)

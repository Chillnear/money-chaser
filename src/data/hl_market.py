"""
ไคลเอนต์สำหรับ Hyperliquid Info API (public, ไม่ต้อง auth/key)
เอกสารอ้างอิง: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

Endpoint เดียว: POST {base_url}/info พร้อม JSON body ต่างกันตาม "type"
ทุกฟังก์ชันในไฟล์นี้:
  - มี retry (tenacity) + timeout ตาม config.yaml
  - cache ผลลง state/cache/*.json เป็น fallback เมื่อ API ล่ม (ตาม non-negotiable #6: fail closed แต่ไม่ทำให้ pipeline ตายเพราะ data ขาด)
  - คืนค่าเป็น dict/list ดิบ — การแปลงเป็น features อยู่ที่ features.py (ห้ามคำนวณอะไรที่นี่)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.settings import STATE_DIR

DEFAULT_BASE_URL = "https://api.hyperliquid.xyz"
CACHE_DIR = STATE_DIR / "cache"


class HyperliquidAPIError(Exception):
    """ใช้แยกจาก exception อื่นๆ เพื่อให้ main.py ตัดสินใจ fallback ได้ชัดเจน"""


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _read_cache(key: str) -> Any | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("data")
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(key: str, data: Any) -> None:
    path = _cache_path(key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cached_at": time.time(), "data": data}, f)


class HyperliquidClient:
    """
    ตัวอย่างการใช้งาน:
        client = HyperliquidClient()
        universe = client.get_meta()                       # รายชื่อตลาดทั้งหมด + specs
        ctxs = client.get_meta_and_asset_ctxs()             # funding, OI, 24h volume, mark price
        candles = client.get_candles("BTC", interval="1d")  # OHLCV
        mids = client.get_all_mids()                        # mid price ปัจจุบันทุกตลาด

    หมายเหตุความถูกต้อง: เขียนตามสเปก Hyperliquid Info API ที่เผยแพร่ไว้ ยังไม่ได้ยิงทดสอบ
    กับ endpoint จริงในสภาพแวดล้อมนี้ (sandbox บล็อก outbound ไป api.hyperliquid.xyz ผ่าน allowlist)
    -> ต้องรันสมอค เทสต์จริงอีกครั้งบนเครื่อง/CI ที่มี network เปิดก่อนใช้งานจริง (ดู tests/test_hl_market.py
    ที่ mock HTTP layer ไว้ให้ตรวจ logic ได้โดยไม่ต้องพึ่ง network ก่อน)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 15.0,
        retry_attempts: int = 3,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.retry_attempts = retry_attempts
        self.session = session or requests.Session()

    def _post(self, body: dict) -> Any:
        @retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((requests.RequestException, HyperliquidAPIError)),
            reraise=True,
        )
        def _do_request():
            resp = self.session.post(
                f"{self.base_url}/info",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout_sec,
            )
            if resp.status_code != 200:
                raise HyperliquidAPIError(
                    f"Hyperliquid info API ตอบ {resp.status_code}: {resp.text[:300]}"
                )
            return resp.json()

        return _do_request()

    def _post_with_cache_fallback(self, body: dict, cache_key: str) -> Any:
        try:
            data = self._post(body)
            _write_cache(cache_key, data)
            return data
        except Exception as exc:  # noqa: BLE001 - ตั้งใจกว้าง เพราะต้อง fallback ทุกกรณี
            cached = _read_cache(cache_key)
            if cached is not None:
                return cached
            raise HyperliquidAPIError(
                f"เรียก Hyperliquid info API ล้มเหลว และไม่มี cache สำรอง (key={cache_key}): {exc}"
            ) from exc

    def get_meta(self) -> dict:
        """รายชื่อตลาด perp ทั้งหมด + specs (szDecimals ฯลฯ) — ใช้เป็น universe pool เบื้องต้น"""
        return self._post_with_cache_fallback({"type": "meta"}, "meta")

    def get_meta_and_asset_ctxs(self) -> list:
        """คืน [meta, assetCtxs] — assetCtxs เรียงตาม index เดียวกับ meta['universe']
        แต่ละ ctx มี: funding, openInterest, dayNtlVlm (24h notional volume), markPx, prevDayPx
        ใช้ field เหล่านี้ในการทำ screening (ข้อ 3b ของ BUILD-SPEC.md)
        """
        return self._post_with_cache_fallback({"type": "metaAndAssetCtxs"}, "meta_and_asset_ctxs")

    def get_all_mids(self) -> dict:
        """dict ของ {coin: mid_price_str} ทุกตลาด"""
        return self._post_with_cache_fallback({"type": "allMids"}, "all_mids")

    def get_candles(
        self,
        coin: str,
        interval: str = "1d",
        lookback_days: int = 400,
    ) -> list[dict]:
        """OHLCV แบบ daily (ปรับ interval ได้) ย้อนหลัง lookback_days วัน
        คืนเป็น list of dict: {t, T, o, h, l, c, v} (timestamp เปิด/ปิดเป็น ms, ราคาเป็น string)
        """
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_days * 24 * 60 * 60 * 1000
        body = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
        return self._post_with_cache_fallback(body, f"candles_{coin}_{interval}")

    def get_funding_history(self, coin: str, lookback_days: int = 14) -> list[dict]:
        """ประวัติ funding rate ย้อนหลัง (ใช้คำนวณ funding average 7d + percentile ในข้อ 3 ของ BUILD-SPEC.md)
        คืน list of {coinFundingTime หรือ time, fundingRate} ตามที่ Hyperliquid ส่งมา
        """
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_days * 24 * 60 * 60 * 1000
        body = {"type": "fundingHistory", "coin": coin, "startTime": start_ms, "endTime": end_ms}
        return self._post_with_cache_fallback(body, f"funding_history_{coin}")

    def get_clearinghouse_state(self, address: str) -> dict:
        """สถานะบัญชี (equity, position ที่เปิดอยู่, margin) ของ address ใดๆ — เป็น public info
        ไม่ต้องมี private key เลย (ต่างจากการเทรดที่ต้อง sign ด้วย agent wallet)
        ใช้เช็คยอด/position จริงตอน reconcile (execution/reconcile.py) และใช้ตรวจสอบ setup ตอน P0.5
        """
        return self._post_with_cache_fallback(
            {"type": "clearinghouseState", "user": address}, f"clearinghouse_{address}"
        )

    def get_universe_snapshot(self) -> list[dict]:
        """ผลลัพธ์รวมสำหรับ screening.py: รวม meta + ctxs เป็น list ของ dict ต่อ 1 ตลาด
        [{coin, funding, open_interest_usd, day_volume_usd, mark_px, prev_day_px}, ...]
        คำนวณ/รวมข้อมูลล้วนๆ ไม่มี logic ตัดสินใจใดๆ (นั่นเป็นหน้าที่ของ screening.py)
        """
        meta, ctxs = self.get_meta_and_asset_ctxs()
        universe = meta["universe"]
        if len(universe) != len(ctxs):
            raise HyperliquidAPIError(
                f"meta.universe ({len(universe)}) กับ assetCtxs ({len(ctxs)}) ยาวไม่เท่ากัน "
                "— schema ของ Hyperliquid อาจเปลี่ยน ต้องตรวจก่อนใช้ต่อ"
            )
        snapshot = []
        for spec, ctx in zip(universe, ctxs):
            snapshot.append(
                {
                    "coin": spec["name"],
                    "funding": float(ctx.get("funding", 0.0)),
                    "open_interest_usd": float(ctx.get("openInterest", 0.0))
                    * float(ctx.get("markPx", 0.0)),
                    "day_volume_usd": float(ctx.get("dayNtlVlm", 0.0)),
                    "mark_px": float(ctx.get("markPx", 0.0)),
                    "prev_day_px": float(ctx.get("prevDayPx", 0.0)),
                }
            )
        return snapshot

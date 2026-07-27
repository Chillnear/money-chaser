"""
ข้อมูลมหภาค: DXY (ดอลลาร์อินเด็กซ์), XAUUSD (ทองคำสปอตอ้างอิง แยกจากราคา PAXG บน Hyperliquid),
SPX (S&P 500), US10Y (ผลตอบแทนพันธบัตรสหรัฐ 10 ปี) — ใช้เป็น cross-asset feature (ข้อ 3 ของ BUILD-SPEC.md)

แหล่งข้อมูล: Yahoo Finance chart API แบบไม่เป็นทางการ (ฟรี ไม่ต้อง API key) — เปลี่ยนมาจาก stooq.com
เพราะยิงทดสอบจริงบน GitHub Actions แล้วพบว่า symbol ของ stooq ที่เดาไว้ผิด (0/4 ดึงไม่ได้)
Yahoo symbol เป็นที่รู้จักกว้างกว่าในหมู่ quant/hobby tooling: DX-Y.NYB (DXY), XAUUSD=X (ทองสปอต),
^GSPC (S&P500), ^TNX (10Y yield x10 ต้องหาร 10 เพื่อได้ % จริง)

⚠️ endpoint นี้ไม่เป็นทางการ (unofficial) เหมือนเดิม ยังต้อง verify อีกรอบผ่าน check_setup.py บน
GitHub Actions — แต่ **ไม่ critical ต่อการเทรด** อยู่แล้ว (ใช้แค่เป็น context เสริมให้ analyst_macro)
ถ้าล่มก็ไม่ทำให้ pipeline หยุดเทรด (non-negotiable ข้อ 6: fail-closed เฉพาะจุดตัดสินใจเทรด)
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

YAHOO_SYMBOLS = {
    "dxy": "DX-Y.NYB",
    "xauusd": "XAUUSD=X",
    "spx": "^GSPC",
    "us10y": "^TNX",
}
# ^TNX รายงานเป็นทศนิยม x10 (เช่น 42.5 แปลว่า 4.25%) ต้องหาร 10
YAHOO_DIVIDE_BY_10 = {"us10y"}


@dataclass
class MacroSeries:
    symbol: str
    last_close: float | None
    change_1d_pct: float | None
    as_of: str | None
    ok: bool
    error: str | None = None


class MacroClient:
    def __init__(self, timeout_sec: float = 15.0, retry_attempts: int = 2):
        self.timeout_sec = timeout_sec
        self.retry_attempts = retry_attempts
        self.session = requests.Session()

    def _fetch_yahoo_chart(self, symbol: str) -> dict:
        @retry(stop=stop_after_attempt(self.retry_attempts), wait=wait_exponential(multiplier=1, min=1, max=5))
        def _do():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            resp = self.session.get(
                url,
                params={"range": "5d", "interval": "1d"},
                timeout=self.timeout_sec,
                headers={"User-Agent": "Mozilla/5.0 (money-chaser-bot)"},
            )
            resp.raise_for_status()
            payload = resp.json()
            result = payload.get("chart", {}).get("result")
            if not result:
                error = payload.get("chart", {}).get("error")
                raise ValueError(f"Yahoo chart API คืน result ว่างสำหรับ {symbol}: {error}")
            return result[0]

        return _do()

    def get_series(self, name: str) -> MacroSeries:
        symbol = YAHOO_SYMBOLS.get(name)
        if symbol is None:
            return MacroSeries(name, None, None, None, ok=False, error=f"ไม่รู้จัก macro series ชื่อ {name}")
        try:
            result = self._fetch_yahoo_chart(symbol)
            timestamps = result.get("timestamp", [])
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])

            # ตัดวันที่ไม่มีราคา (None) ออก เช่นวันหยุดตลาด
            clean = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
            if len(clean) < 1:
                raise ValueError(f"ไม่มีข้อมูลราคาที่ใช้ได้เลยสำหรับ {symbol}")

            divisor = 10.0 if name in YAHOO_DIVIDE_BY_10 else 1.0
            last_ts, last_close = clean[-1]
            last_close = last_close / divisor
            if len(clean) >= 2:
                prev_close = clean[-2][1] / divisor
            else:
                prev_close = last_close
            change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0

            return MacroSeries(name, float(last_close), float(change_pct), str(last_ts), ok=True)
        except Exception as exc:  # noqa: BLE001 - ตั้งใจกว้าง เพราะ macro เป็น best-effort ไม่ critical
            return MacroSeries(name, None, None, None, ok=False, error=str(exc))

    def get_macro_snapshot(self) -> dict:
        """คืน dict {name: {last_close, change_1d_pct, as_of, ok}} + "missing": [ชื่อที่ดึงไม่ได้]
        ใช้ใน features.py เป็น cross-asset context — ถ้าขาดตัวไหน features.py ต้อง handle None ได้
        """
        results = {}
        missing = []
        for name in YAHOO_SYMBOLS:
            series = self.get_series(name)
            results[name] = {
                "last_close": series.last_close,
                "change_1d_pct": series.change_1d_pct,
                "as_of": series.as_of,
                "ok": series.ok,
            }
            if not series.ok:
                missing.append(name)
        results["missing"] = missing
        results["data_missing"] = len(missing) > 0
        return results

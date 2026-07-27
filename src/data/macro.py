"""
ข้อมูลมหภาค: DXY (ดอลลาร์อินเด็กซ์), XAUUSD (ทองคำสปอตอ้างอิง แยกจากราคา PAXG บน Hyperliquid),
SPX (S&P 500), US10Y (ผลตอบแทนพันธบัตรสหรัฐ 10 ปี) — ใช้เป็น cross-asset feature (ข้อ 3 ของ BUILD-SPEC.md)

แหล่งข้อมูล: stooq.com CSV endpoint (ฟรี ไม่ต้อง API key)
⚠️ ความเสี่ยงที่ต้องเปิดเผย: สัญลักษณ์ (symbol) ที่ใช้ด้านล่างเป็นชื่อที่ stooq ใช้กันทั่วไปตามความรู้ที่มี
แต่ **ยังไม่ได้ยิงทดสอบกับ stooq จริง** เพราะ sandbox ที่พัฒนาโค้ดนี้เข้าเน็ตภายนอกไม่ได้เลย (เหมือนกรณี
hl_market.py) ต้องรัน scripts/check_setup.py เวอร์ชันที่อัปเดตแล้ว (ดู P1 ต่อจากนี้) บน GitHub Actions
เพื่อยืนยันว่า symbol ถูกต้อง ถ้า symbol ผิด get_macro_snapshot() จะคืนค่า None สำหรับตัวนั้นและใส่
ไว้ใน "missing" — **ไม่ทำให้ pipeline ทั้งหมดล้ม** ตาม non-negotiable ข้อ 6 (fail-closed เฉพาะจุดที่เกี่ยวกับ
การตัดสินใจเทรด ไม่ใช่ทุก data source ย่อย)
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

STOOQ_SYMBOLS = {
    "dxy": "usdidx",
    "xauusd": "xauusd",
    "spx": "^spx",
    "us10y": "10usy.b",
}


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

    def _fetch_stooq_csv(self, symbol: str) -> pd.DataFrame:
        @retry(stop=stop_after_attempt(self.retry_attempts), wait=wait_exponential(multiplier=1, min=1, max=5))
        def _do():
            url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
            resp = self.session.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            if df.empty or "Close" not in df.columns:
                raise ValueError(f"stooq คืนข้อมูลว่างหรือผิดรูปสำหรับ symbol={symbol}")
            return df

        return _do()

    def get_series(self, name: str) -> MacroSeries:
        symbol = STOOQ_SYMBOLS.get(name)
        if symbol is None:
            return MacroSeries(name, None, None, None, ok=False, error=f"ไม่รู้จัก macro series ชื่อ {name}")
        try:
            df = self._fetch_stooq_csv(symbol)
            df = df.sort_values("Date")
            last_close = float(df["Close"].iloc[-1])
            prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else last_close
            change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0
            as_of = str(df["Date"].iloc[-1])
            return MacroSeries(name, last_close, change_pct, as_of, ok=True)
        except Exception as exc:  # noqa: BLE001 - ตั้งใจกว้าง เพราะ macro เป็น best-effort ไม่ critical
            return MacroSeries(name, None, None, None, ok=False, error=str(exc))

    def get_macro_snapshot(self) -> dict:
        """คืน dict {name: {last_close, change_1d_pct, as_of, ok}} + "missing": [ชื่อที่ดึงไม่ได้]
        ใช้ใน features.py เป็น cross-asset context — ถ้าขาดตัวไหน features.py ต้อง handle None ได้
        """
        results = {}
        missing = []
        for name in STOOQ_SYMBOLS:
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

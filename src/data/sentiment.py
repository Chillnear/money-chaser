"""
Fear & Greed Index จาก alternative.me (ฟรี ไม่ต้อง API key)
เอกสาร: https://alternative.me/crypto/fear-and-greed-index/

Endpoint นี้เป็นที่นิยมและค่อนข้างเสถียรในวงการ crypto tooling — ความเสี่ยงต่ำกว่า macro.py (stooq)
แต่ยังไม่ได้ยิงทดสอบจริงในสภาพแวดล้อมนี้เช่นกัน (ดูคำอธิบายเดียวกันใน macro.py)
"""
from __future__ import annotations

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

FNG_URL = "https://api.alternative.me/fng/"


class SentimentClient:
    def __init__(self, timeout_sec: float = 15.0, retry_attempts: int = 2):
        self.timeout_sec = timeout_sec
        self.retry_attempts = retry_attempts
        self.session = requests.Session()

    def get_fear_greed(self, days: int = 8) -> dict:
        """คืน {value: int (0-100), classification: str, delta_7d: float|None, ok: bool}
        value ต่ำ = Extreme Fear, สูง = Extreme Greed — ใช้เป็น sentiment feature (ข้อ 3 ของ BUILD-SPEC.md)
        """
        try:
            @retry(stop=stop_after_attempt(self.retry_attempts), wait=wait_exponential(multiplier=1, min=1, max=5))
            def _do():
                resp = self.session.get(FNG_URL, params={"limit": days, "format": "json"}, timeout=self.timeout_sec)
                resp.raise_for_status()
                return resp.json()

            payload = _do()
            data = payload.get("data", [])
            if not data:
                raise ValueError("alternative.me คืน data ว่าง")

            latest = data[0]
            value = int(latest["value"])
            classification = latest["value_classification"]

            delta_7d = None
            if len(data) >= 8:
                week_ago = int(data[7]["value"])
                delta_7d = value - week_ago

            return {"value": value, "classification": classification, "delta_7d": delta_7d, "ok": True}
        except Exception as exc:  # noqa: BLE001 - best-effort, ไม่ critical ต่อการเทรด
            return {"value": None, "classification": None, "delta_7d": None, "ok": False, "error": str(exc)}

"""
ปฏิทินข่าวมหภาค (เศรษฐกิจ) — ใช้เป็น hard veto ห้ามเทรดวันที่มีข่าวใหญ่ (เช่น FOMC/CPI/NFP)
ตามคำขอผู้ใช้ที่ได้แรงบันดาลใจจากกฎ "no-trade-on-big-news-day" ใน playbook ของ Earthh Evans
(P5.2 — BUILD-SPEC.md เดิมไม่มีข้อนี้ เพิ่มตามที่ผู้ใช้เลือกไว้ตอนอ่าน playbook)

แหล่งข้อมูล: ForexFactory JSON feed แบบไม่เป็นทางการ (ฟรี ไม่ต้อง API key/login) — verify แล้วว่า
endpoint นี้เปิดสาธารณะจริงและคืนปฏิทินของสัปดาห์ปัจจุบันเป็น JSON array:
[{"title": str, "country": "USD"/"EUR"/..., "date": ISO8601 พร้อม timezone offset,
  "impact": "Low"/"Medium"/"High", "forecast": str, "previous": str}, ...]

⚠️ endpoint ไม่เป็นทางการเหมือน macro.py/news.py — เป็น best-effort เท่านั้น ถ้าดึงไม่ได้ต้อง
fail-safe คือ "ไม่ block การเทรด" (ไม่ใช่ "บล็อกเพราะข้อมูลขาด") ตาม non-negotiable ข้อ 6:
fail-closed ใช้เฉพาะจุดตัดสินใจเทรดที่มีข้อมูลจริงขัดแย้งกันเท่านั้น ไม่ใช่ตอนข้อมูลเสริมขาดหายไป
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass
class EconEvent:
    title: str
    country: str
    impact: str
    event_ts: float  # unix timestamp แปลงจาก ISO string ที่ ForexFactory ให้มา (มี timezone offset ในตัว)


def parse_events(raw: list[dict]) -> list[EconEvent]:
    """แปลง JSON ดิบจาก ForexFactory เป็น EconEvent list — ข้ามรายการที่ parse วันที่ไม่ได้แบบเงียบๆ
    (ไม่ raise) เพราะ feed นี้ไม่เป็นทางการ โครงสร้างอาจเปลี่ยนแปลงได้โดยไม่แจ้งล่วงหน้า
    """
    events: list[EconEvent] = []
    for item in raw:
        try:
            parsed = dt.datetime.fromisoformat(item["date"])
            events.append(
                EconEvent(
                    title=item.get("title", ""),
                    country=item.get("country", ""),
                    impact=item.get("impact", ""),
                    event_ts=parsed.timestamp(),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return events


def find_veto_events(
    events: list[EconEvent],
    now_ts: float,
    impact_levels: set[str],
    countries: set[str],
    lookahead_hours: float,
    lookback_hours: float,
) -> list[EconEvent]:
    """หา event ที่เข้าเกณฑ์ veto: impact/country ตรง และเวลาอยู่ในช่วง [now - lookback, now + lookahead]
    เผื่อทั้งข่าวที่กำลังจะประกาศเร็วๆนี้ (กันความผันผวนก่อนประกาศ) และข่าวที่ประกาศไปแล้วไม่นาน
    (กันความผันผวนหลังประกาศที่ยังไม่นิ่ง)
    """
    window_start = now_ts - lookback_hours * 3600
    window_end = now_ts + lookahead_hours * 3600
    return [
        e
        for e in events
        if e.impact in impact_levels and e.country in countries and window_start <= e.event_ts <= window_end
    ]


class EconCalendarClient:
    def __init__(self, timeout_sec: float = 15.0, retry_attempts: int = 2, url: str = FF_CALENDAR_URL):
        self.timeout_sec = timeout_sec
        self.retry_attempts = retry_attempts
        self.url = url
        self.session = requests.Session()

    def fetch_events(self) -> list[EconEvent]:
        @retry(stop=stop_after_attempt(self.retry_attempts), wait=wait_exponential(multiplier=1, min=1, max=5))
        def _do():
            resp = self.session.get(
                self.url,
                timeout=self.timeout_sec,
                headers={"User-Agent": "Mozilla/5.0 (money-chaser-bot)"},
            )
            resp.raise_for_status()
            return resp.json()

        raw = _do()
        return parse_events(raw)

    def get_veto_status(
        self,
        now_ts: float,
        impact_levels: list[str],
        countries: list[str],
        lookahead_hours: float,
        lookback_hours: float,
    ) -> dict:
        """คืน {"vetoed": bool, "reason": str, "data_missing": bool, "events": [ชื่อข่าว...]}

        fail-safe (non-negotiable ข้อ 6 ใช้เฉพาะจุดตัดสินใจที่มีข้อมูลขัดแย้งจริง): ถ้าดึงปฏิทินไม่ได้
        เลย -> vetoed=False, data_missing=True — ไม่บล็อกการเทรดเพราะข้อมูลเสริมขาด
        """
        try:
            events = self.fetch_events()
        except Exception as exc:  # noqa: BLE001 - best-effort เหมือน macro.py/news.py/sentiment.py
            return {
                "vetoed": False,
                "reason": f"ดึงปฏิทินข่าวมหภาคไม่ได้ (ไม่ block การเทรด): {exc}",
                "data_missing": True,
                "events": [],
            }

        hits = find_veto_events(events, now_ts, set(impact_levels), set(countries), lookahead_hours, lookback_hours)
        if hits:
            names = ", ".join(sorted({h.title for h in hits}))
            return {
                "vetoed": True,
                "reason": f"วันนี้มีข่าวมหภาคสำคัญใกล้เวลา: {names} — ห้ามเทรดวันนี้ตามกฎ no-trade-on-news-day",
                "data_missing": False,
                "events": [h.title for h in hits],
            }
        return {
            "vetoed": False,
            "reason": "ไม่มีข่าวมหภาคสำคัญในช่วงเวลาที่เช็ค",
            "data_missing": False,
            "events": [],
        }

"""
ดึงพาดหัวข่าวคริปโต 24 ชม.ล่าสุดจาก RSS feed (ฟรี ไม่ต้อง API key) — ตัด dedupe หัวข้อซ้ำ
ใช้เป็น sentiment/context feature เท่านั้น ไม่ใช่แหล่งข้อมูลตัวเลข (ข้อ 3 ของ BUILD-SPEC.md)

ออกแบบให้ fail-soft: feed ไหนล่มก็ข้ามไปตัวถัดไป ไม่ทำให้ pipeline ตาย
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import feedparser
import requests


def _normalize_title(title: str) -> str:
    """ตัดช่องว่าง/ตัวพิมพ์เล็กใหญ่/เครื่องหมายวรรคตอนออก เพื่อเทียบหัวข้อซ้ำแบบหยาบๆ"""
    lower = title.lower().strip()
    return re.sub(r"[^a-z0-9฀-๿]+", " ", lower).strip()


@dataclass
class NewsHeadline:
    title: str
    source: str
    published_ts: float
    link: str = ""


@dataclass
class NewsSnapshot:
    headlines: list[NewsHeadline] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.headlines)


class NewsClient:
    def __init__(self, feed_urls: list[str], timeout_sec: float = 15.0, lookback_hours: float = 24.0):
        self.feed_urls = feed_urls
        self.timeout_sec = timeout_sec
        self.lookback_hours = lookback_hours
        self.session = requests.Session()

    def get_recent_headlines(self) -> NewsSnapshot:
        cutoff = time.time() - self.lookback_hours * 3600
        seen_normalized: set[str] = set()
        headlines: list[NewsHeadline] = []
        failed: list[str] = []

        for url in self.feed_urls:
            try:
                resp = self.session.get(url, timeout=self.timeout_sec, headers={"User-Agent": "money-chaser-bot/1.0"})
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                if parsed.bozo and not parsed.entries:
                    raise ValueError(f"feedparser แปลง RSS ไม่ได้: {parsed.bozo_exception}")

                source_name = parsed.feed.get("title", url)
                for entry in parsed.entries:
                    published_ts = time.time()
                    if getattr(entry, "published_parsed", None):
                        published_ts = time.mktime(entry.published_parsed)
                    if published_ts < cutoff:
                        continue

                    norm = _normalize_title(entry.get("title", ""))
                    if not norm or norm in seen_normalized:
                        continue
                    seen_normalized.add(norm)

                    headlines.append(
                        NewsHeadline(
                            title=entry.get("title", "").strip(),
                            source=source_name,
                            published_ts=published_ts,
                            link=entry.get("link", ""),
                        )
                    )
            except Exception:  # noqa: BLE001 - feed ไหนล่มก็ข้าม ไม่ทำให้ทั้งระบบตาย
                failed.append(url)

        headlines.sort(key=lambda h: h.published_ts, reverse=True)
        return NewsSnapshot(headlines=headlines, sources_failed=failed)


def merge_with_cryptopanic(rss_snapshot: NewsSnapshot, cryptopanic_posts: list) -> NewsSnapshot:
    """รวมข่าวจาก RSS กับ CryptoPanic เข้าด้วยกัน ตัดหัวข้อซ้ำออก (normalize เดียวกับ RSS)
    ทำให้ได้แหล่งข่าวหลากหลายขึ้น (บล็อก/สื่อ/สรุปโซเชียลผ่าน CryptoPanic) โดยไม่ต้อง scrape เอง
    """
    seen = {_normalize_title(h.title) for h in rss_snapshot.headlines}
    merged = list(rss_snapshot.headlines)

    for post in cryptopanic_posts:
        norm = _normalize_title(post.title)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        merged.append(NewsHeadline(title=post.title, source=f"CryptoPanic/{post.source}", published_ts=post.published_ts, link=post.link))

    merged.sort(key=lambda h: h.published_ts, reverse=True)
    return NewsSnapshot(headlines=merged, sources_failed=rss_snapshot.sources_failed)

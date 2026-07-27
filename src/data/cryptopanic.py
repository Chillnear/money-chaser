"""
CryptoPanic API — news aggregator ที่รวมข่าวจากหลายแหล่ง (บล็อก, สื่อ, และสรุปจากโซเชียล) พร้อม
sentiment แบบ community voting ในตัว — เป็นทางเลือกให้ "หลากหลายและน่าเชื่อถือ" กว่า RSS เดี่ยวๆ
โดยไม่ต้อง scrape Twitter/Telegram ตรงๆ (ผิด ToS และเปราะบางกว่ามาก)

สมัครฟรีที่ https://cryptopanic.com/developers/api/keys เพื่อได้ auth token
**Optional** — ถ้าไม่ตั้ง CRYPTOPANIC_API_KEY ระบบจะข้ามแหล่งนี้ไปเฉยๆ ไม่ทำให้ pipeline ตาย
(เหมือน macro/sentiment/news RSS — ทุกแหล่งข่าวเป็น best-effort ไม่ critical ต่อการเทรด)

⚠️ ยังไม่ได้ยิงทดสอบกับ API จริง เพราะ sandbox ที่พัฒนาโค้ดนี้เข้าเน็ตภายนอกไม่ได้ (เหมือนไฟล์อื่นในชุดนี้)
ต้อง verify ผ่าน scripts/check_setup.py บน GitHub Actions ก่อนเชื่อถือ 100%
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


@dataclass
class CryptoPanicPost:
    title: str
    source: str
    published_ts: float
    link: str = ""
    votes_positive: int = 0
    votes_negative: int = 0
    votes_important: int = 0


@dataclass
class CryptoPanicSnapshot:
    posts: list[CryptoPanicPost] = field(default_factory=list)
    ok: bool = True
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.posts)


class CryptoPanicClient:
    def __init__(self, auth_token: str, timeout_sec: float = 15.0):
        self.auth_token = auth_token
        self.timeout_sec = timeout_sec
        self.session = requests.Session()

    def get_recent_posts(self, currencies: str = "BTC,ETH,SOL", lookback_hours: float = 24.0) -> CryptoPanicSnapshot:
        if not self.auth_token:
            return CryptoPanicSnapshot(posts=[], ok=False, error="ไม่ได้ตั้ง CRYPTOPANIC_API_KEY (optional, ข้ามได้)")

        try:
            resp = self.session.get(
                CRYPTOPANIC_URL,
                params={"auth_token": self.auth_token, "public": "true", "currencies": currencies, "kind": "news"},
                timeout=self.timeout_sec,
            )
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results", [])

            cutoff = time.time() - lookback_hours * 3600
            posts = []
            for item in results:
                published_ts = time.time()
                published_at = item.get("published_at")
                if published_at:
                    try:
                        published_ts = time.mktime(time.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:
                        pass
                if published_ts < cutoff:
                    continue

                votes = item.get("votes", {})
                posts.append(
                    CryptoPanicPost(
                        title=item.get("title", "").strip(),
                        source=(item.get("source", {}) or {}).get("title", "cryptopanic"),
                        published_ts=published_ts,
                        link=item.get("url", ""),
                        votes_positive=votes.get("positive", 0),
                        votes_negative=votes.get("negative", 0),
                        votes_important=votes.get("important", 0),
                    )
                )
            return CryptoPanicSnapshot(posts=posts, ok=True)
        except Exception as exc:  # noqa: BLE001 - best-effort, ไม่ critical
            return CryptoPanicSnapshot(posts=[], ok=False, error=str(exc))

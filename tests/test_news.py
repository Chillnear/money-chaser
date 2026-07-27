from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.data.news import NewsClient, NewsHeadline, NewsSnapshot, _normalize_title, merge_with_cryptopanic
from src.data.cryptopanic import CryptoPanicPost


def _rss(entries_xml: str, feed_title: str = "Test Feed") -> bytes:
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>{feed_title}</title>
{entries_xml}
</channel></rss>""".encode("utf-8")


def _entry(title: str, pub_date: str, link: str = "https://example.com/a") -> str:
    return f"<item><title>{title}</title><link>{link}</link><pubDate>{pub_date}</pubDate></item>"


def _mock_resp(content: bytes):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


NOW_RFC822 = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
OLD_RFC822 = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(time.time() - 48 * 3600))


def test_normalize_title_strips_punctuation_and_case():
    assert _normalize_title("Bitcoin Hits $70K!!") == _normalize_title("bitcoin hits 70k")


def test_get_recent_headlines_filters_old_and_dedupes():
    xml = "\n".join(
        [
            _entry("BTC pumps hard", NOW_RFC822, "https://a.com/1"),
            _entry("BTC pumps hard!!", NOW_RFC822, "https://a.com/2"),  # ซ้ำหลัง normalize
            _entry("Old news nobody cares", OLD_RFC822),  # เก่าเกิน 24 ชม.
        ]
    )
    client = NewsClient(feed_urls=["https://feed.example.com/rss"])
    client.session = MagicMock()
    client.session.get.return_value = _mock_resp(_rss(xml))

    snapshot = client.get_recent_headlines()

    assert snapshot.count == 1
    assert snapshot.headlines[0].title == "BTC pumps hard"
    assert snapshot.sources_failed == []


def test_get_recent_headlines_handles_feed_failure_gracefully():
    client = NewsClient(feed_urls=["https://dead-feed.example.com/rss", "https://ok-feed.example.com/rss"])
    client.session = MagicMock()

    def side_effect(url, timeout, headers):
        if "dead-feed" in url:
            raise ConnectionError("boom")
        return _mock_resp(_rss(_entry("Still working feed", NOW_RFC822)))

    client.session.get.side_effect = side_effect

    snapshot = client.get_recent_headlines()

    assert snapshot.count == 1
    assert "https://dead-feed.example.com/rss" in snapshot.sources_failed


def test_merge_with_cryptopanic_adds_unique_posts():
    rss = NewsSnapshot(headlines=[NewsHeadline(title="BTC pumps hard", source="CoinDesk", published_ts=time.time())])
    cp_posts = [
        CryptoPanicPost(title="ETH upgrade coming soon", source="TheBlock", published_ts=time.time()),
        CryptoPanicPost(title="BTC pumps hard!!", source="Dup", published_ts=time.time()),  # ซ้ำกับ RSS
    ]

    merged = merge_with_cryptopanic(rss, cp_posts)

    titles = [h.title for h in merged.headlines]
    assert "ETH upgrade coming soon" in titles
    assert titles.count("BTC pumps hard") + titles.count("BTC pumps hard!!") == 1  # ไม่ซ้ำ


def test_merge_with_cryptopanic_empty_posts_is_noop():
    rss = NewsSnapshot(headlines=[NewsHeadline(title="Solo headline", source="X", published_ts=time.time())])
    merged = merge_with_cryptopanic(rss, [])
    assert merged.count == 1

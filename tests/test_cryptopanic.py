from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.data.cryptopanic import CryptoPanicClient


def _mock_json_response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_recent_posts_no_token_skips_gracefully():
    client = CryptoPanicClient(auth_token="")
    snapshot = client.get_recent_posts()
    assert snapshot.ok is False
    assert snapshot.count == 0
    assert "optional" in snapshot.error.lower() or snapshot.error is not None


def test_get_recent_posts_success():
    # ใช้เวลาสัมพัทธ์กับ "ตอนนี้" ไม่ใช่วันที่ตายตัว — ของเดิม hardcode วันที่ไว้ทำให้เทสหมดอายุ
    # (ผ่านไปวันเดียวก็เกิน lookback_hours=24 คืนค่าว่างเปล่าแม้โค้ดไม่มีบั๊กเลย)
    recent_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    client = CryptoPanicClient(auth_token="fake-token")
    client.session = MagicMock()
    client.session.get.return_value = _mock_json_response(
        {
            "results": [
                {
                    "title": "BTC breaks $70k",
                    "url": "https://example.com/1",
                    "published_at": recent_ts,
                    "source": {"title": "Decrypt"},
                    "votes": {"positive": 10, "negative": 1, "important": 3},
                }
            ]
        }
    )

    snapshot = client.get_recent_posts()

    assert snapshot.ok is True
    assert snapshot.count == 1
    assert snapshot.posts[0].title == "BTC breaks $70k"
    assert snapshot.posts[0].votes_positive == 10


def test_get_recent_posts_handles_api_failure():
    client = CryptoPanicClient(auth_token="fake-token")
    client.session = MagicMock()
    client.session.get.side_effect = ConnectionError("boom")

    snapshot = client.get_recent_posts()

    assert snapshot.ok is False
    assert snapshot.count == 0

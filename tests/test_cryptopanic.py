from __future__ import annotations

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
    client = CryptoPanicClient(auth_token="fake-token")
    client.session = MagicMock()
    client.session.get.return_value = _mock_json_response(
        {
            "results": [
                {
                    "title": "BTC breaks $70k",
                    "url": "https://example.com/1",
                    "published_at": "2026-07-27T00:00:00Z",
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

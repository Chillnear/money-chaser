from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.data.hl_market import HyperliquidClient


def _mock_response(status_code: int, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


def test_get_funding_history_calls_correct_body(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    session = MagicMock()
    session.post.return_value = _mock_response(200, [{"time": 1, "fundingRate": "0.0001"}])
    client = HyperliquidClient(session=session)

    history = client.get_funding_history("BTC", lookback_days=14)

    assert history[0]["fundingRate"] == "0.0001"
    body = session.post.call_args.kwargs["json"]
    assert body["type"] == "fundingHistory"
    assert body["coin"] == "BTC"
    assert "startTime" in body and "endTime" in body

from __future__ import annotations

from unittest.mock import MagicMock

from src.data.sentiment import SentimentClient


def _mock_json_response(payload: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_fear_greed_success_with_delta():
    client = SentimentClient()
    client.session = MagicMock()
    data = [{"value": str(70 - i), "value_classification": "Greed", "timestamp": str(1000 - i)} for i in range(8)]
    client.session.get.return_value = _mock_json_response({"data": data})

    result = client.get_fear_greed()

    assert result["ok"] is True
    assert result["value"] == 70
    assert result["classification"] == "Greed"
    assert result["delta_7d"] == 70 - 63


def test_get_fear_greed_handles_short_history():
    client = SentimentClient()
    client.session = MagicMock()
    data = [{"value": "55", "value_classification": "Neutral", "timestamp": "1000"}]
    client.session.get.return_value = _mock_json_response({"data": data})

    result = client.get_fear_greed()

    assert result["ok"] is True
    assert result["value"] == 55
    assert result["delta_7d"] is None


def test_get_fear_greed_handles_api_failure():
    client = SentimentClient()
    client.session = MagicMock()
    client.session.get.side_effect = ConnectionError("boom")

    result = client.get_fear_greed()

    assert result["ok"] is False
    assert result["value"] is None

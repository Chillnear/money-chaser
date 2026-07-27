from __future__ import annotations

from unittest.mock import MagicMock

from src.data.macro import MacroClient


def _mock_yahoo_response(closes: list[float | None], timestamps: list[int] | None = None):
    if timestamps is None:
        timestamps = list(range(1_700_000_000, 1_700_000_000 + len(closes) * 86400, 86400))
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def test_get_series_success():
    client = MacroClient()
    client.session = MagicMock()
    client.session.get.return_value = _mock_yahoo_response([104.2, 104.8])

    series = client.get_series("dxy")

    assert series.ok is True
    assert series.last_close == 104.8
    assert round(series.change_1d_pct, 4) == round((104.8 - 104.2) / 104.2 * 100, 4)


def test_get_series_unknown_name():
    client = MacroClient()
    series = client.get_series("not_a_real_series")
    assert series.ok is False
    assert series.error is not None


def test_get_series_handles_empty_result():
    client = MacroClient()
    client.session = MagicMock()
    client.session.get.return_value = _mock_yahoo_response([None, None])

    series = client.get_series("spx")
    assert series.ok is False


def test_get_series_divides_us10y_by_10():
    client = MacroClient()
    client.session = MagicMock()
    client.session.get.return_value = _mock_yahoo_response([42.0, 42.5])  # ^TNX x10

    series = client.get_series("us10y")

    assert series.ok is True
    assert series.last_close == 4.25


def test_get_macro_snapshot_reports_missing():
    client = MacroClient()
    client.session = MagicMock()

    def side_effect(url, params, timeout, headers):
        if "DX-Y.NYB" in url:
            return _mock_yahoo_response([104.2, 104.8])
        raise ConnectionError("boom")

    client.session.get.side_effect = side_effect

    snapshot = client.get_macro_snapshot()

    assert snapshot["dxy"]["ok"] is True
    assert snapshot["data_missing"] is True
    assert "xauusd" in snapshot["missing"]

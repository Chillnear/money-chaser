from __future__ import annotations

from unittest.mock import MagicMock

from src.data.macro import MacroClient


def _mock_csv_response(csv_text: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = csv_text
    resp.raise_for_status = MagicMock()
    return resp


SAMPLE_CSV = "Date,Open,High,Low,Close,Volume\n2026-07-24,104.0,104.5,103.8,104.2,0\n2026-07-25,104.2,104.9,104.0,104.8,0\n"


def test_get_series_success():
    client = MacroClient()
    client.session = MagicMock()
    client.session.get.return_value = _mock_csv_response(SAMPLE_CSV)

    series = client.get_series("dxy")

    assert series.ok is True
    assert series.last_close == 104.8
    assert series.change_1d_pct is not None
    assert round(series.change_1d_pct, 4) == round((104.8 - 104.2) / 104.2 * 100, 4)


def test_get_series_unknown_name():
    client = MacroClient()
    series = client.get_series("not_a_real_series")
    assert series.ok is False
    assert series.error is not None


def test_get_series_handles_empty_csv():
    client = MacroClient()
    client.session = MagicMock()
    client.session.get.return_value = _mock_csv_response("Date,Open,High,Low,Close,Volume\n")

    series = client.get_series("spx")
    assert series.ok is False


def test_get_macro_snapshot_reports_missing():
    client = MacroClient()
    client.session = MagicMock()

    def side_effect(url, timeout):
        if "usdidx" in url:
            return _mock_csv_response(SAMPLE_CSV)
        raise ConnectionError("boom")

    client.session.get.side_effect = side_effect

    snapshot = client.get_macro_snapshot()

    assert snapshot["dxy"]["ok"] is True
    assert snapshot["data_missing"] is True
    assert "xauusd" in snapshot["missing"]

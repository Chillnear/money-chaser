"""
Test แบบ mock HTTP layer เพราะ sandbox ที่ใช้พัฒนาบล็อก outbound ไป api.hyperliquid.xyz
(ยืนยันแล้วว่า proxy คืน 403 blocked-by-allowlist) — ต้องรัน smoke test แยกอีกครั้งบนเครื่อง/CI
ที่มี network เปิดจริงก่อนใช้งาน (ดู README สำหรับคำสั่ง)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.data.hl_market import HyperliquidAPIError, HyperliquidClient


def _mock_response(status_code: int, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data) if not isinstance(json_data, str) else json_data
    return resp


def test_get_all_mids_success(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"BTC": "65000.5", "ETH": "3400.1"})
    client = HyperliquidClient(session=session)

    mids = client.get_all_mids()

    assert mids["BTC"] == "65000.5"
    session.post.assert_called_once()
    called_body = session.post.call_args.kwargs["json"]
    assert called_body == {"type": "allMids"}


def test_falls_back_to_cache_when_api_down(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    session = MagicMock()

    # เรียกครั้งแรกสำเร็จ -> เขียน cache
    session.post.return_value = _mock_response(200, {"BTC": "65000.5"})
    client = HyperliquidClient(session=session, retry_attempts=1)
    first = client.get_all_mids()
    assert first["BTC"] == "65000.5"

    # เรียกครั้งที่สอง API ล่ม -> ต้องได้ค่าจาก cache ไม่ raise
    session.post.side_effect = ConnectionError("boom")
    second = client.get_all_mids()
    assert second["BTC"] == "65000.5"


def test_raises_when_no_cache_and_api_down(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    session = MagicMock()
    session.post.side_effect = ConnectionError("boom")
    client = HyperliquidClient(session=session, retry_attempts=1)

    with pytest.raises(HyperliquidAPIError):
        client.get_all_mids()


def test_get_universe_snapshot_combines_meta_and_ctxs(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    meta = {"universe": [{"name": "BTC"}, {"name": "PAXG"}]}
    ctxs = [
        {"funding": "0.0001", "openInterest": "100", "dayNtlVlm": "5000000", "markPx": "65000", "prevDayPx": "64000"},
        {"funding": "0.00005", "openInterest": "50", "dayNtlVlm": "1000000", "markPx": "2400", "prevDayPx": "2390"},
    ]
    session = MagicMock()
    session.post.return_value = _mock_response(200, [meta, ctxs])
    client = HyperliquidClient(session=session)

    snapshot = client.get_universe_snapshot()

    assert len(snapshot) == 2
    btc = next(s for s in snapshot if s["coin"] == "BTC")
    assert btc["open_interest_usd"] == pytest.approx(100 * 65000)
    assert btc["day_volume_usd"] == pytest.approx(5000000)


def test_get_clearinghouse_state_calls_correct_body(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    session = MagicMock()
    session.post.return_value = _mock_response(200, {"marginSummary": {"accountValue": "28.4"}})
    client = HyperliquidClient(session=session)

    state = client.get_clearinghouse_state("0x3AF84E46339D80EDcebedF1E5CcCb1aE869E57AF")

    assert state["marginSummary"]["accountValue"] == "28.4"
    called_body = session.post.call_args.kwargs["json"]
    assert called_body == {
        "type": "clearinghouseState",
        "user": "0x3AF84E46339D80EDcebedF1E5CcCb1aE869E57AF",
    }


def test_get_universe_snapshot_raises_on_length_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr("src.data.hl_market.CACHE_DIR", tmp_path)
    meta = {"universe": [{"name": "BTC"}, {"name": "PAXG"}]}
    ctxs = [{"funding": "0", "openInterest": "1", "dayNtlVlm": "1", "markPx": "1", "prevDayPx": "1"}]
    session = MagicMock()
    session.post.return_value = _mock_response(200, [meta, ctxs])
    client = HyperliquidClient(session=session)

    with pytest.raises(HyperliquidAPIError):
        client.get_universe_snapshot()

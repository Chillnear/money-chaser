"""
Test แบบ mock HTTP layer (เหมือน tests/test_hl_market.py) เพราะ sandbox บล็อก outbound ไป api.line.me
ต้องรัน smoke test จริงอีกครั้งบน GitHub Actions ก่อนใช้งานจริง
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.report.notify import (
    LINE_PUSH_URL,
    MAX_MESSAGES_PER_PUSH,
    MAX_TEXT_LENGTH,
    LineNotifier,
    format_breaker_alert,
    format_budget_alert,
    format_daily_summary,
    format_exception_alert,
    format_reconcile_mismatch_alert,
    notify_budget_thresholds,
)


def _mock_response(status_code: int, text: str = "ok"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class FakeRunResult:
    def __init__(self, date="2026-07-27", action_taken="opened_long", reason="เทรนด์ชัด", equity_usd=29.5):
        self.date = date
        self.action_taken = action_taken
        self.reason = reason
        self.equity_usd = equity_usd


# ---- LineNotifier.is_configured ----


def test_is_configured_true_when_both_set():
    notifier = LineNotifier(channel_access_token="token", user_id="U123")
    assert notifier.is_configured() is True


def test_is_configured_false_when_missing_token():
    notifier = LineNotifier(channel_access_token="", user_id="U123")
    assert notifier.is_configured() is False


def test_is_configured_false_when_missing_user_id():
    notifier = LineNotifier(channel_access_token="token", user_id="")
    assert notifier.is_configured() is False


# ---- send_text / send_texts ----


def test_send_text_skips_when_not_configured_and_does_not_raise():
    notifier = LineNotifier(channel_access_token="", user_id="")
    result = notifier.send_text("hello")
    assert result.sent is False
    assert "ยังไม่ตั้งค่า" in result.reason


def test_send_texts_empty_list_returns_not_sent():
    session = MagicMock()
    notifier = LineNotifier(channel_access_token="token", user_id="U123", session=session)
    result = notifier.send_texts([])
    assert result.sent is False
    session.post.assert_not_called()


def test_send_text_success_calls_correct_endpoint_and_payload():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok123", user_id="Uabc", session=session)

    result = notifier.send_text("สวัสดี")

    assert result.sent is True
    session.post.assert_called_once()
    call = session.post.call_args
    assert call.args[0] == LINE_PUSH_URL
    assert call.kwargs["json"] == {"to": "Uabc", "messages": [{"type": "text", "text": "สวัสดี"}]}
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok123"


def test_send_text_failure_status_code_reported_not_raised():
    session = MagicMock()
    session.post.return_value = _mock_response(400, text="invalid user id")
    notifier = LineNotifier(channel_access_token="tok", user_id="Ubad", session=session)

    result = notifier.send_text("hi")

    assert result.sent is False
    assert "400" in result.reason


def test_send_text_network_exception_caught_not_raised():
    import requests

    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("no network")
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    result = notifier.send_text("hi")

    assert result.sent is False
    assert "network" in result.reason.lower() or "no network" in result.reason


def test_send_texts_truncates_to_max_messages_per_push():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    too_many = [f"msg{i}" for i in range(MAX_MESSAGES_PER_PUSH + 3)]
    notifier.send_texts(too_many)

    sent_messages = session.post.call_args.kwargs["json"]["messages"]
    assert len(sent_messages) == MAX_MESSAGES_PER_PUSH


def test_send_texts_truncates_long_text_to_max_length():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    long_text = "a" * (MAX_TEXT_LENGTH + 500)
    notifier.send_texts([long_text])

    sent_text = session.post.call_args.kwargs["json"]["messages"][0]["text"]
    assert len(sent_text) == MAX_TEXT_LENGTH


# ---- formatters ----


def test_format_daily_summary_includes_key_fields():
    text = format_daily_summary(FakeRunResult())
    assert "2026-07-27" in text
    assert "opened_long" in text
    assert "เทรนด์ชัด" in text
    assert "29.50" in text


def test_format_budget_alert_includes_percentage_and_amounts():
    text = format_budget_alert(pct_used=72.5, monthly_spend_usd=10.9, monthly_hard_stop_usd=15.0)
    assert "72" in text or "73" in text  # ปัดเศษได้ทั้งสองแบบ ขึ้นกับ python round
    assert "10.90" in text
    assert "15.00" in text


def test_format_breaker_alert_includes_reason():
    text = format_breaker_alert("daily loss 7% เกินเกณฑ์")
    assert "daily loss 7%" in text


def test_format_reconcile_mismatch_alert_includes_reason():
    text = format_reconcile_mismatch_alert("equity ต่างกัน 10%")
    assert "equity ต่างกัน 10%" in text


def test_format_exception_alert_truncates_long_error():
    text = format_exception_alert("x" * 2000)
    assert len(text) < 1200


# ---- notify_budget_thresholds ----


def test_notify_budget_thresholds_sends_new_threshold_only():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    newly_notified = notify_budget_thresholds(
        notifier, monthly_spend_usd=11.0, monthly_hard_stop_usd=15.0, thresholds_pct=[70, 90],
        already_notified_pct=[],
    )

    assert newly_notified == [70]
    session.post.assert_called_once()


def test_notify_budget_thresholds_skips_already_notified():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    newly_notified = notify_budget_thresholds(
        notifier, monthly_spend_usd=11.0, monthly_hard_stop_usd=15.0, thresholds_pct=[70, 90],
        already_notified_pct=[70],
    )

    assert newly_notified == []
    session.post.assert_not_called()


def test_notify_budget_thresholds_sends_multiple_when_both_crossed():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=session)

    newly_notified = notify_budget_thresholds(
        notifier, monthly_spend_usd=14.5, monthly_hard_stop_usd=15.0, thresholds_pct=[70, 90],
        already_notified_pct=[],
    )

    assert newly_notified == [70, 90]
    assert session.post.call_count == 2


def test_notify_budget_thresholds_handles_zero_hard_stop_gracefully():
    notifier = LineNotifier(channel_access_token="tok", user_id="U1", session=MagicMock())
    result = notify_budget_thresholds(
        notifier, monthly_spend_usd=5.0, monthly_hard_stop_usd=0.0, thresholds_pct=[70, 90], already_notified_pct=[]
    )
    assert result == []

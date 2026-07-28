from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

from src.data.econ_calendar import (
    EconCalendarClient,
    EconEvent,
    find_veto_events,
    parse_events,
)

# ---- parse_events ----


def test_parse_events_converts_iso_dates():
    raw = [{"title": "FOMC Statement", "country": "USD", "impact": "High", "date": "2026-07-29T14:00:00-04:00"}]
    events = parse_events(raw)
    assert len(events) == 1
    assert events[0].title == "FOMC Statement"
    assert events[0].country == "USD"
    assert events[0].impact == "High"


def test_parse_events_skips_malformed_rows_without_raising():
    raw = [
        {"title": "good", "country": "USD", "impact": "High", "date": "2026-07-29T14:00:00-04:00"},
        {"title": "bad date", "country": "USD", "impact": "High", "date": "not-a-date"},
        {"title": "missing date field", "country": "USD", "impact": "High"},
        {},
    ]
    events = parse_events(raw)
    assert len(events) == 1
    assert events[0].title == "good"


# ---- find_veto_events ----


def _event(title, country, impact, event_ts):
    return EconEvent(title=title, country=country, impact=impact, event_ts=event_ts)


def test_find_veto_events_matches_within_lookahead_window():
    now = 1_800_000_000.0
    events = [_event("FOMC", "USD", "High", now + 3 * 3600)]  # 3 ชม.ข้างหน้า
    hits = find_veto_events(events, now, {"High"}, {"USD"}, lookahead_hours=6, lookback_hours=2)
    assert len(hits) == 1


def test_find_veto_events_matches_within_lookback_window():
    now = 1_800_000_000.0
    events = [_event("CPI", "USD", "High", now - 1 * 3600)]  # ประกาศไปแล้ว 1 ชม.
    hits = find_veto_events(events, now, {"High"}, {"USD"}, lookahead_hours=6, lookback_hours=2)
    assert len(hits) == 1


def test_find_veto_events_excludes_events_outside_window():
    now = 1_800_000_000.0
    events = [_event("NFP", "USD", "High", now + 100 * 3600)]  # ไกลเกินไป
    hits = find_veto_events(events, now, {"High"}, {"USD"}, lookahead_hours=6, lookback_hours=2)
    assert hits == []


def test_find_veto_events_excludes_wrong_impact_level():
    now = 1_800_000_000.0
    events = [_event("Some low-impact release", "USD", "Low", now + 1 * 3600)]
    hits = find_veto_events(events, now, {"High"}, {"USD"}, lookahead_hours=6, lookback_hours=2)
    assert hits == []


def test_find_veto_events_excludes_wrong_country():
    now = 1_800_000_000.0
    events = [_event("BOJ Policy Rate", "JPY", "High", now + 1 * 3600)]
    hits = find_veto_events(events, now, {"High"}, {"USD"}, lookahead_hours=6, lookback_hours=2)
    assert hits == []


# ---- EconCalendarClient.get_veto_status ----


def test_get_veto_status_vetoes_when_high_impact_usd_event_is_near():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    client = EconCalendarClient()
    client.session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = [
        {"title": "FOMC Statement", "country": "USD", "impact": "High", "date": "2026-07-29T14:00:00+00:00"},
    ]
    client.session.get.return_value = resp

    status = client.get_veto_status(now, ["High"], ["USD"], lookahead_hours=6, lookback_hours=2)

    assert status["vetoed"] is True
    assert "FOMC Statement" in status["reason"]
    assert status["data_missing"] is False


def test_get_veto_status_does_not_veto_when_no_matching_events():
    now = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    client = EconCalendarClient()
    client.session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = [
        {"title": "German ifo", "country": "EUR", "impact": "Low", "date": "2026-07-27T04:00:00+00:00"},
    ]
    client.session.get.return_value = resp

    status = client.get_veto_status(now, ["High"], ["USD"], lookahead_hours=6, lookback_hours=2)

    assert status["vetoed"] is False
    assert status["data_missing"] is False


def test_get_veto_status_is_fail_safe_when_fetch_fails():
    # non-negotiable ข้อ 6: ข้อมูลเสริมขาดไม่ควรบล็อกการเทรด — ต้องได้ vetoed=False เสมอตอนดึงไม่ได้
    client = EconCalendarClient(retry_attempts=1)
    client.session = MagicMock()
    client.session.get.side_effect = ConnectionError("network down")

    status = client.get_veto_status(1_800_000_000.0, ["High"], ["USD"], lookahead_hours=6, lookback_hours=2)

    assert status["vetoed"] is False
    assert status["data_missing"] is True

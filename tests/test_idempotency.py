from __future__ import annotations

from src.execution.reconcile import has_run_today, mark_run_complete


def test_has_run_today_false_when_file_missing(tmp_path):
    path = tmp_path / "state" / "last_run.json"
    assert has_run_today(path, "2026-07-27") is False


def test_mark_run_complete_then_has_run_today_true(tmp_path):
    path = tmp_path / "state" / "last_run.json"
    mark_run_complete(path, "2026-07-27")
    assert has_run_today(path, "2026-07-27") is True


def test_has_run_today_false_for_different_date(tmp_path):
    path = tmp_path / "state" / "last_run.json"
    mark_run_complete(path, "2026-07-27")
    assert has_run_today(path, "2026-07-28") is False  # วันใหม่ -> รันได้อีกครั้ง


def test_running_twice_same_day_is_idempotent(tmp_path):
    """จำลอง pipeline รัน 2 ครั้งในวันเดียวกัน (เช่น cron รันซ้ำ) — ครั้งที่ 2 ต้องถูกกันไว้"""
    path = tmp_path / "state" / "last_run.json"
    today = "2026-07-27"

    # รอบแรก: ยังไม่เคยรัน -> ควรได้รับอนุญาตให้รัน
    assert has_run_today(path, today) is False
    mark_run_complete(path, today, extra={"decision": "long BTC"})

    # รอบสอง (จำลอง cron รันซ้ำวันเดียวกัน): ต้องถูกล็อกไว้ ไม่ให้เทรดซ้ำ
    assert has_run_today(path, today) is True


def test_mark_run_complete_stores_extra_metadata(tmp_path):
    path = tmp_path / "state" / "last_run.json"
    mark_run_complete(path, "2026-07-27", extra={"decision": "flat", "reason": "confidence ต่ำ"})
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["decision"] == "flat"
    assert data["reason"] == "confidence ต่ำ"


def test_has_run_today_handles_corrupt_file_gracefully(tmp_path):
    path = tmp_path / "state" / "last_run.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ corrupt json", encoding="utf-8")
    assert has_run_today(path, "2026-07-27") is False

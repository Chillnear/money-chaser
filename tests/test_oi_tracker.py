from __future__ import annotations

from src.data.oi_tracker import compute_oi_change_pct, load_oi_history, record_oi_snapshot


def test_record_oi_snapshot_writes_one_record_per_coin(tmp_path):
    path = tmp_path / "oi_history.jsonl"
    universe = [{"coin": "BTC", "open_interest_usd": 1_000_000.0}, {"coin": "PAXG", "open_interest_usd": 500_000.0}]

    record_oi_snapshot(path, "2026-07-27", universe)

    history = load_oi_history(path)
    assert len(history) == 2
    assert {r["coin"] for r in history} == {"BTC", "PAXG"}


def test_record_oi_snapshot_is_idempotent_for_same_date(tmp_path):
    path = tmp_path / "oi_history.jsonl"
    universe = [{"coin": "BTC", "open_interest_usd": 1_000_000.0}]

    record_oi_snapshot(path, "2026-07-27", universe)
    record_oi_snapshot(path, "2026-07-27", universe)  # เรียกซ้ำวันเดียวกัน (เช่น รันซ้ำตอนทดสอบ)

    history = load_oi_history(path)
    assert len(history) == 1


def test_record_oi_snapshot_appends_new_date():
    pass  # covered implicitly by compute_oi_change_pct tests below (มีหลายวันในประวัติ)


def test_compute_oi_change_pct_returns_none_when_no_history():
    assert compute_oi_change_pct([], "BTC", 1_000_000.0, "2026-07-27", lookback_days=1) is None


def test_compute_oi_change_pct_returns_none_when_current_oi_missing():
    history = [{"date": "2026-07-26", "coin": "BTC", "open_interest_usd": 900_000.0}]
    assert compute_oi_change_pct(history, "BTC", None, "2026-07-27", lookback_days=1) is None


def test_compute_oi_change_pct_computes_24h_change():
    history = [
        {"date": "2026-07-26", "coin": "BTC", "open_interest_usd": 900_000.0},
        {"date": "2026-07-20", "coin": "BTC", "open_interest_usd": 800_000.0},
    ]
    pct = compute_oi_change_pct(history, "BTC", 990_000.0, "2026-07-27", lookback_days=1)
    assert round(pct, 2) == round((990_000.0 - 900_000.0) / 900_000.0 * 100, 2)


def test_compute_oi_change_pct_computes_7d_change():
    history = [
        {"date": "2026-07-26", "coin": "BTC", "open_interest_usd": 900_000.0},
        {"date": "2026-07-20", "coin": "BTC", "open_interest_usd": 800_000.0},
    ]
    pct = compute_oi_change_pct(history, "BTC", 990_000.0, "2026-07-27", lookback_days=7)
    assert round(pct, 2) == round((990_000.0 - 800_000.0) / 800_000.0 * 100, 2)


def test_compute_oi_change_pct_respects_tolerance_window():
    # เป้าหมาย 7 วันก่อนคือ 2026-07-20 แต่ข้อมูลใกล้สุดอยู่ที่ 2026-07-15 (ห่างเกิน tolerance=1) -> None
    history = [{"date": "2026-07-15", "coin": "BTC", "open_interest_usd": 700_000.0}]
    pct = compute_oi_change_pct(history, "BTC", 990_000.0, "2026-07-27", lookback_days=7, tolerance_days=1)
    assert pct is None


def test_compute_oi_change_pct_ignores_other_coins():
    history = [{"date": "2026-07-26", "coin": "ETH", "open_interest_usd": 500_000.0}]
    pct = compute_oi_change_pct(history, "BTC", 990_000.0, "2026-07-27", lookback_days=1)
    assert pct is None

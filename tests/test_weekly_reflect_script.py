from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "weekly_reflect.py"


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location("weekly_reflect_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_filter_last_n_days_keeps_only_recent_records(script_module):
    now_ts = 1_000_000.0
    records = [
        {"ts": now_ts - 100},  # ล่าสุด
        {"ts": now_ts - 6 * 86400},  # ยังอยู่ใน 7 วัน
        {"ts": now_ts - 10 * 86400},  # เก่าเกิน
    ]
    kept = script_module.filter_last_n_days(records, now_ts, days=7)
    assert len(kept) == 2


def test_filter_last_n_days_missing_ts_treated_as_epoch_zero_and_dropped(script_module):
    now_ts = 1_000_000.0
    kept = script_module.filter_last_n_days([{"no_ts": True}], now_ts, days=7)
    assert kept == []


def test_render_journal_markdown_empty_returns_placeholder(script_module):
    text = script_module.render_journal_markdown([])
    assert "ไม่มี decision" in text


def test_render_journal_markdown_llm_source_includes_action_and_asset(script_module):
    records = [
        {
            "date": "2026-07-27",
            "source": "llm",
            "judge_output": {"action": "long", "asset": "BTC"},
            "judge_abstained": False,
            "degrade_level": 0,
        }
    ]
    text = script_module.render_journal_markdown(records)
    assert "source=llm" in text
    assert "action=long" in text
    assert "asset=BTC" in text


def test_render_journal_markdown_baseline_source_includes_action_and_asset(script_module):
    records = [
        {
            "date": "2026-07-27",
            "source": "baseline",
            "baseline_decision": {"action": "short", "asset": "ETH"},
            "degrade_level": 4,
        }
    ]
    text = script_module.render_journal_markdown(records)
    assert "source=baseline" in text
    assert "action=short" in text
    assert "asset=ETH" in text


def test_render_trades_markdown_empty_returns_placeholder(script_module):
    text = script_module.render_trades_markdown([])
    assert "ไม่มีไม้ที่ปิด" in text


def test_render_trades_markdown_includes_pnl_and_reason(script_module):
    records = [
        {"asset": "BTC", "side": "long", "pnl_usd": 1.23, "exit_reason": "take_profit_hit", "fee_usd": 0.05}
    ]
    text = script_module.render_trades_markdown(records)
    assert "BTC" in text
    assert "1.23" in text
    assert "take_profit_hit" in text

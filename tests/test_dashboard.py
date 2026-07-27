from __future__ import annotations

from src.report.dashboard import (
    render_decisions_table_html,
    render_dashboard_html,
    render_equity_summary_html,
    render_trades_table_html,
)


def test_render_equity_summary_html_no_data():
    html_out = render_equity_summary_html(None)
    assert "ยังไม่มีข้อมูล" in html_out


def test_render_equity_summary_html_shows_equity_and_drawdown():
    state = {"equity_usd": 27.0, "peak_equity_usd": 30.0, "open_position": None, "breaker": {}}
    html_out = render_equity_summary_html(state)
    assert "27.00" in html_out
    assert "30.00" in html_out
    assert "10.00%" in html_out  # (30-27)/30 = 10%


def test_render_equity_summary_html_shows_open_position():
    state = {
        "equity_usd": 28.0,
        "peak_equity_usd": 28.0,
        "open_position": {
            "asset": "BTC", "side": "long", "notional_usd": 15.0, "entry_price": 100.0,
            "stop_price": 97.0, "take_profit_price": 106.0,
        },
        "breaker": {},
    }
    html_out = render_equity_summary_html(state)
    assert "BTC" in html_out
    assert "long" in html_out
    assert "15.00" in html_out


def test_render_equity_summary_html_shows_pause_reason():
    state = {
        "equity_usd": 20.0, "peak_equity_usd": 28.0, "open_position": None,
        "breaker": {"paused_until_ts": 123456.0, "pause_reason": "daily loss เกินเกณฑ์"},
    }
    html_out = render_equity_summary_html(state)
    assert "พักการเทรด" in html_out
    assert "daily loss เกินเกณฑ์" in html_out


def test_render_trades_table_html_empty():
    assert "ยังไม่มีไม้" in render_trades_table_html([])


def test_render_trades_table_html_shows_rows_most_recent_first():
    trades = [
        {"asset": "BTC", "side": "long", "notional_usd": 10.0, "pnl_usd": 1.5, "exit_reason": "take_profit_hit"},
        {"asset": "ETH", "side": "short", "notional_usd": 12.0, "pnl_usd": -0.8, "exit_reason": "stop_loss_hit"},
    ]
    html_out = render_trades_table_html(trades)
    assert "BTC" in html_out
    assert "ETH" in html_out
    # ETH (ล่าสุด) ต้องปรากฏก่อน BTC ในตาราง (list.reverse ทำให้อันสุดท้ายขึ้นก่อน)
    assert html_out.index("ETH") < html_out.index("BTC")


def test_render_trades_table_html_limits_to_max_rows():
    trades = [{"asset": f"C{i}", "side": "long", "notional_usd": 1.0, "pnl_usd": 0.1, "exit_reason": "x"} for i in range(50)]
    html_out = render_trades_table_html(trades)
    assert html_out.count("<tr>") - 1 == 30  # 1 header row ไม่ถูกนับใน <tr> ของ tbody เพราะ header ใช้ th ใน thead


def test_render_decisions_table_html_empty():
    assert "ยังไม่มี decision" in render_decisions_table_html([])


def test_render_decisions_table_html_llm_source():
    decisions = [{"date": "2026-07-27", "source": "llm", "judge_output": {"action": "long", "asset": "BTC"}, "degrade_level": 0}]
    html_out = render_decisions_table_html(decisions)
    assert "llm" in html_out
    assert "long" in html_out
    assert "BTC" in html_out


def test_render_decisions_table_html_baseline_source():
    decisions = [{"date": "2026-07-27", "source": "baseline", "baseline_decision": {"action": "short", "asset": "ETH"}, "degrade_level": 4}]
    html_out = render_decisions_table_html(decisions)
    assert "baseline" in html_out
    assert "short" in html_out
    assert "ETH" in html_out


def test_render_dashboard_html_is_valid_self_contained_page():
    html_out = render_dashboard_html(None, [], [], "2026-07-27T00:20:00")
    assert html_out.startswith("<!DOCTYPE html>")
    assert "Money Chaser" in html_out
    assert "2026-07-27T00:20:00" in html_out


def test_render_dashboard_html_escapes_untrusted_looking_values():
    state = {"equity_usd": 28.0, "peak_equity_usd": 28.0, "open_position": None, "breaker": {"pause_reason": "<script>alert(1)</script>"}}
    state["breaker"]["paused_until_ts"] = 1.0
    html_out = render_dashboard_html(state, [], [], "now")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out

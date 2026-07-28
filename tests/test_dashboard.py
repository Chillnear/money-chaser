from __future__ import annotations

from src.report.dashboard import (
    render_agent_scorecard_html,
    render_decision_log_html,
    render_decisions_table_html,
    render_dashboard_html,
    render_equity_curve_html,
    render_equity_summary_html,
    render_lessons_html,
    render_llm_budget_html,
    render_performance_stats_html,
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


def test_render_equity_curve_html_no_data():
    assert "ยังไม่มีข้อมูล" in render_equity_curve_html(None)
    assert "ยังไม่มีข้อมูล" in render_equity_curve_html([])


def test_render_equity_curve_html_needs_at_least_two_points():
    assert "น้อยเกินไป" in render_equity_curve_html([{"date": "2026-07-27", "equity_usd": 28.0}])


def test_render_equity_curve_html_draws_svg_polyline():
    history = [
        {"date": "2026-07-27", "equity_usd": 28.0},
        {"date": "2026-07-28", "equity_usd": 30.0},
        {"date": "2026-07-29", "equity_usd": 27.5},
    ]
    html_out = render_equity_curve_html(history)
    assert "<svg" in html_out
    assert "<polyline" in html_out
    assert "2026-07-27" in html_out
    assert "2026-07-29" in html_out


def test_render_performance_stats_html_empty():
    assert "ยังไม่มีไม้" in render_performance_stats_html([])


def test_render_performance_stats_html_computes_win_rate_and_profit_factor():
    trades = [
        {"pnl_usd": 2.0},
        {"pnl_usd": -1.0},
        {"pnl_usd": 3.0},
    ]
    html_out = render_performance_stats_html(trades)
    assert "66.7%" in html_out  # 2/3 ไม้ชนะ
    assert "4.00" in html_out  # PnL รวม = 2 - 1 + 3 = 4


def test_render_llm_budget_html_no_data():
    assert "ยังไม่มีข้อมูล" in render_llm_budget_html(None, None)


def test_render_llm_budget_html_shows_spend_and_per_role_table():
    now = 1_800_000_000.0
    records = [
        {"ts": now - 100, "role": "judge", "cost_usd": 0.01, "latency_ms": 500, "abstained": False},
        {"ts": now - 200, "role": "redteam", "cost_usd": 0.02, "latency_ms": 1000, "abstained": True},
    ]
    html_out = render_llm_budget_html(records, {"daily_soft_cap_usd": 0.5, "hard_stop_usd": 15.0}, now_ts=now)
    assert "judge" in html_out
    assert "redteam" in html_out
    assert "100%" in html_out  # redteam abstain rate = 1/1 = 100%


def test_render_agent_scorecard_html_no_llm_decisions():
    assert "ยังไม่มี decision" in render_agent_scorecard_html([])
    assert "ยังไม่มี decision" in render_agent_scorecard_html([{"source": "baseline"}])


def test_render_agent_scorecard_html_counts_agreement_with_judge():
    decisions = [
        {
            "source": "llm",
            "judge_output": {"action": "long", "asset": "BTC"},
            "analyst_results": [
                {
                    "role": "analyst_trend",
                    "abstained": False,
                    "output": {"candidates": [{"asset": "BTC", "direction": "long", "confidence": 70}]},
                },
                {
                    "role": "analyst_positioning",
                    "abstained": False,
                    "output": {"candidates": [{"asset": "BTC", "direction": "short", "confidence": 60}]},
                },
            ],
            "redteam_result": None,
        }
    ]
    html_out = render_agent_scorecard_html(decisions)
    assert "analyst_trend" in html_out
    assert "100%" in html_out  # analyst_trend เห็นตรงกับ judge (long)
    assert "0%" in html_out  # analyst_positioning ไม่ตรง (short vs long)


def test_render_decision_log_html_empty():
    assert "ยังไม่มี decision" in render_decision_log_html([])


def test_render_decision_log_html_shows_full_llm_reasoning_in_collapsible_details():
    decisions = [
        {
            "date": "2026-07-27",
            "source": "llm",
            "shortlist": [{"coin": "BTC", "composite": 0.8}],
            "pinned_extra": None,
            "rest_summary": [],
            "degrade_level": 0,
            "analyst_results": [
                {
                    "role": "analyst_trend",
                    "model": "m-trend",
                    "abstained": False,
                    "cost_usd": 0.001,
                    "latency_ms": 500,
                    "output": {
                        "candidates": [
                            {"asset": "BTC", "direction": "long", "confidence": 70, "thesis": "เทรนด์แข็งแรง", "invalidation": "หลุด EMA50"}
                        ]
                    },
                },
            ],
            "redteam_result": None,
            "judge_abstained": False,
            "judge_output": {
                "action": "long", "asset": "BTC", "confidence": 66, "stop_pct": 2.0, "take_profit_pct": 4.0,
                "reasoning": "เหตุผลของ judge", "why_this_over_others": "score สูงสุด", "redteam_response": "รับทราบ",
            },
        }
    ]
    html_out = render_decision_log_html(decisions)
    assert "<details" in html_out
    assert "<summary>" in html_out
    assert "เทรนด์แข็งแรง" in html_out
    assert "เหตุผลของ judge" in html_out
    assert "2026-07-27" in html_out


def test_render_decision_log_html_shows_baseline_decision():
    decisions = [
        {
            "date": "2026-07-27",
            "source": "baseline",
            "shortlist": [{"coin": "PAXG", "composite": 0.5}],
            "degrade_level": 4,
            "baseline_decision": {"action": "long", "asset": "PAXG", "confidence": 50},
        }
    ]
    html_out = render_decision_log_html(decisions)
    assert "PAXG" in html_out
    assert "baseline" in html_out.lower() or "Baseline" in html_out


def test_render_lessons_html_empty():
    assert "ยังไม่มี lessons.md" in render_lessons_html("")


def test_render_lessons_html_shows_content_escaped():
    html_out = render_lessons_html("# lessons\n<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


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

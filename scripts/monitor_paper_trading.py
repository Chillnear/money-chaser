#!/usr/bin/env python3
"""
Monitor paper trading — track daily decisions and P&L.

Run this daily to see:
- Did all 3 agents execute?
- What decisions were made?
- Current portfolio balance
- Any errors?

Usage:
    python scripts/monitor_paper_trading.py
"""

import json
from pathlib import Path
from datetime import datetime
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
JOURNAL_DIR = STATE_DIR / "journal"


def load_daily_summary() -> dict | None:
    """Load today's summary."""
    summary_path = STATE_DIR / "daily_summary.json"
    if not summary_path.exists():
        return None

    with open(summary_path) as f:
        return json.load(f)


def load_journal_state() -> dict | None:
    """Load current state."""
    state_path = JOURNAL_DIR / "state.json"
    if not state_path.exists():
        return None

    with open(state_path) as f:
        return json.load(f)


def count_trades_this_week() -> dict:
    """Count total trades in last 7 days."""
    json_files = sorted(JOURNAL_DIR.glob("*.json"))
    trades = {"directional": 0, "farming": 0, "grid": 0}

    # This is simplified; in reality you'd parse the jsonl files
    return trades


def print_header(title: str) -> None:
    """Print formatted header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_section(title: str) -> None:
    """Print formatted section."""
    print(f"\n📊 {title}")
    print("-" * 80)


def main():
    """Main monitoring routine."""

    print_header("📋 PAPER TRADING MONITOR")

    # Check if paper trading is actually running
    summary = load_daily_summary()
    state = load_journal_state()

    if not summary and not state:
        print("\n⚠️  No trading data found yet.")
        print("   Have you run the pipeline? (python src/main.py)")
        print("   Expected: state/daily_summary.json and state/journal/state.json")
        return

    # Portfolio Status
    if state:
        print_section("PORTFOLIO STATUS")
        print(f"  Equity: ${state.get('equity_usd', 100.0):.2f}")
        print(f"  Peak: ${state.get('peak_equity_usd', 100.0):.2f}")
        breaker = state.get("breaker", {})
        print(f"  Daily loss: {breaker.get('cumulative_daily_loss_pct', 0):.2f}%")
        print(f"  Drawdown: {breaker.get('max_drawdown_pct', 0):.2f}%")

    # Today's Summary
    if summary:
        print_section("TODAY'S DECISIONS")
        print(f"  Date: {summary.get('date', 'N/A')}")
        print(f"  Daily P&L: {summary.get('daily_pnl_pct', 0):+.2f}%")

        # Parse directional decision
        directional = summary.get("directional", {})
        if isinstance(directional, dict):
            action = directional.get("action", "unknown")
            reason = directional.get("reason", "")[:60]
            print(f"\n  🤖 Directional: {action}")
            if reason:
                print(f"     └─ {reason}...")

        # Parse farming decision
        farming = summary.get("farming", {})
        if isinstance(farming, dict):
            action = farming.get("action", "unknown")
            reason = farming.get("reason", "")[:60]
            print(f"\n  🌾 Farming: {action}")
            if reason:
                print(f"     └─ {reason}...")

        # Parse grid decision
        grid = summary.get("grid", {})
        if isinstance(grid, dict):
            action = grid.get("action", "unknown")
            reason = grid.get("reason", "")[:60]
            print(f"\n  📈 Grid: {action}")
            if reason:
                print(f"     └─ {reason}...")

    # Positions
    print_section("CURRENT POSITIONS")
    if state:
        directional_pos = state.get("directional_position")
        farming_pos = state.get("farming_position")
        grid_pos = state.get("grid_position")

        if directional_pos:
            print(f"  🤖 Directional: OPEN")
            print(f"     Symbol: {directional_pos.get('symbol', 'N/A')}")
            print(f"     Size: {directional_pos.get('notional_usd', 0):.0f} USD")
        else:
            print(f"  🤖 Directional: CLOSED (waiting for signal)")

        if farming_pos:
            print(f"\n  🌾 Farming: OPEN")
            print(f"     Pair: {farming_pos.get('symbol', 'N/A')}")
            print(f"     Long: ${farming_pos.get('long_notional_usd', 0):.0f}")
            print(f"     Short: ${farming_pos.get('short_notional_usd', 0):.0f}")
            print(f"     Collected: ${farming_pos.get('accumulated_funding_usd', 0):.2f}")
        else:
            print(f"\n  🌾 Farming: CLOSED (waiting for funding)")

        if grid_pos:
            print(f"\n  📈 Grid: ACTIVE")
            print(f"     Price: ${grid_pos.get('center_price', 0):.0f}")
            print(f"     Width: ±{grid_pos.get('grid_width_pct', 5)}%")
            print(f"     Levels: {grid_pos.get('num_levels', 5)}")
        else:
            print(f"\n  📈 Grid: INACTIVE (waiting for volatility)")

    # Alerts
    print_section("ALERTS & NOTES")
    if summary and summary.get("notes"):
        print(f"  {summary['notes']}")
    else:
        print(f"  ✅ No alerts")

    # Summary
    print_section("NEXT STEPS")
    print(f"  1. Check logs: tail state/journal/*.jsonl")
    print(f"  2. Review decisions above")
    print(f"  3. Come back tomorrow for Day 2")
    print(f"  4. After 7 days: Decide if ready for live trading")

    print("\n" + "=" * 80)
    print("  Monitor updated at:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()

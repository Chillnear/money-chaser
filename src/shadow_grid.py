"""
Shadow tracker — คำนวณกลยุทธ์ grid trading (GridTradingAgent) คู่กับ AI จริงทุกวันแบบ deterministic
(ไม่ใช้ AI/LLM เลย) เก็บ equity เสมือนแยกจากบัญชีจริง/paper หลัก 100% ไม่กระทบเงินจริงหรืองบ AI เด็ดขาด
เหมือน src/shadow.py (funding_carry shadow) แต่ใช้ engine v2: แท่ง 1h, pending order, cash/base inventory,
fee/slippage และ mark-to-market ร่วมกับ scripts/backtest_grid_farming.py

ผลจาก engine daily-OHLC รุ่นเก่าถูกยกเลิกเพราะ positive bias สูงเกินจริง เมื่อพบ state รุ่นเก่าจะ reset
shadow เป็น $28 ใหม่โดยอัตโนมัติ และยังห้ามเชื่อมเงินจริงจน walk-forward กับ paper sample ผ่านเกณฑ์

เลือก BTC เป็นเหรียญ shadow (ไม่ใช้ shortlist เหมือน funding_carry) เพราะ:
  - เป็นเหรียญ default ดั้งเดิมของระบบ (DEFAULT_COINS ใน scripts/backtest.py = ["BTC", "PAXG"])
  - สภาพคล่องสูงสุด ความเสี่ยงเรื่อง manipulation/liquidity ต่ำกว่าเหรียญ meme/alt เล็กๆ ที่ backtest
    ให้ผลตอบแทนสูงกว่า (PUMP/ZEC/HYPE) แต่ก็มีความเสี่ยงสูงกว่ามากเช่นกัน — เริ่มจากตัวที่มั่นคงที่สุดก่อน

ต้อง fail-safe เสมอ: exception ใดๆในโมดูลนี้ต้องไม่ทำให้ pipeline การเทรดจริงพังหรือหยุดทำงานเด็ดขาด —
ฟังก์ชันนี้จับ exception ไว้เองข้างในแล้วคืน {"action": "error", ...} แทนการ raise ต่อ (caller ไม่ต้อง
ครอบ try/except ซ้ำ แต่ main.py ยังครอบไว้อีกชั้นเผื่อ defensive เพราะจุดนี้กระทบการเทรดจริงไม่ได้เด็ดขาด)
"""
from __future__ import annotations

from pathlib import Path

from dataclasses import asdict

from src.agents.grid_trader import (
    GridPosition,
    GridTradingAgent,
    apply_intraday_grid_candles,
    close_grid_position,
    grid_position_from_dict,
    initialize_grid_position,
    mark_grid_to_market,
)
from src.data.market_volatility import compute_volatility_24h
from src.util.io import append_jsonl, load_json, save_json

SHADOW_GRID_COIN = "BTC"
SHADOW_GRID_STARTING_EQUITY_USD = 28.0  # เริ่มเท่าทุนจริงตอนสร้างเพื่อเทียบกันตรงๆได้ง่าย ไม่ผูกกับทุนจริงปัจจุบัน


def _state_path(journal_dir: Path) -> Path:
    return journal_dir / "shadow_grid_state.json"


GRID_ENGINE_VERSION = 2


def _default_state() -> dict:
    return {
        "engine_version": GRID_ENGINE_VERSION,
        "cash_usd": SHADOW_GRID_STARTING_EQUITY_USD,
        "open_position": None,
        "low_volatility_days_count": 0,
    }


def _load_state(journal_dir: Path) -> tuple[dict, bool]:
    raw = load_json(_state_path(journal_dir), default=None)
    if not raw or raw.get("engine_version") != GRID_ENGINE_VERSION:
        return _default_state(), raw is not None
    return raw, False


def _save_state(journal_dir: Path, state: dict) -> None:
    save_json(_state_path(journal_dir), state)


def run_grid_shadow_day(
    settings,
    hl_client,
    now_ts: float,
    journal_dir: Path,
    coin: str = SHADOW_GRID_COIN,
) -> dict:
    """รันกลยุทธ์ grid trading แบบ deterministic คู่กับ AI จริง 1 วัน คืน dict สรุปสำหรับ LINE report"""
    try:
        state, reset_from_legacy = _load_state(journal_dir)
        agent = GridTradingAgent()
        agent.low_volatility_days_count = int(state.get("low_volatility_days_count", 0))
        fee_per_leg_pct = settings.risk.costs.taker_fee_pct + settings.risk.costs.assumed_slippage_pct
        if reset_from_legacy:
            append_jsonl(journal_dir / "shadow_grid_trades.jsonl", {
                "date_ts": now_ts,
                "action": "engine_reset",
                "reason": "reset legacy daily-OHLC grid state; engine v2 starts fresh at $28",
                "engine_version": GRID_ENGINE_VERSION,
            })

        candles = hl_client.get_candles(coin, interval="1d", lookback_days=30)
        if len(candles) < 8:
            # ข้อมูลไม่พอคำนวณ volatility — ไม่ทำอะไรวันนี้ ไม่ถือว่า error (แค่รอข้อมูลพอ)
            equity_now = state["cash_usd"] + (
                state["open_position"]["total_capital"] + state["open_position"]["accumulated_pnl"]
                if state.get("open_position") else 0.0
            )
            return {"action": "waiting_for_data", "equity_usd": round(equity_now, 4), "open_position": state.get("open_position")}

        hourly_candles = hl_client.get_candles(coin, interval="1h", lookback_days=3)
        closes = [float(c["c"]) for c in candles]
        current_price = closes[-1]
        vol_24h = compute_volatility_24h(closes, lookback=7)

        position: GridPosition | None = grid_position_from_dict(state["open_position"]) if state.get("open_position") else None
        action_taken = "reset_engine_v2" if reset_from_legacy else ("held_position" if position else "flat")

        if position is not None:
            fills = apply_intraday_grid_candles(position, hourly_candles, fee_per_leg_pct)
            if fills:
                append_jsonl(journal_dir / "shadow_grid_fills.jsonl", {"date_ts": now_ts, "fills": fills})

        decision = agent.decide(
            current_position=position, current_price=current_price, volatility_24h=vol_24h,
            available_capital_usd=state["cash_usd"] if position is None else 0.0,
        )

        if decision.action == "close" and position is not None:
            liquidated_cash = close_grid_position(position, current_price, fee_per_leg_pct)
            state["cash_usd"] += liquidated_cash
            action_taken = f"closed_{'profit' if position.accumulated_pnl >= 0 else 'loss'}"
            append_jsonl(journal_dir / "shadow_grid_trades.jsonl", {
                "date_ts": now_ts, "action": "close", "reason": decision.reason,
                "pnl_usd": round(position.accumulated_pnl, 4), "deployed_capital_usd": round(position.total_capital, 4),
            })
            position = None
        elif decision.action == "open" and position is None:
            latest_hour_ts = int(hourly_candles[-1].get("T", hourly_candles[-1].get("t", 0))) if hourly_candles else 0
            position = initialize_grid_position(
                symbol=coin, center_price=current_price, grid_width_pct=decision.grid_width_pct,
                num_levels=decision.num_levels, total_capital_usd=decision.total_capital_usd,
                entry_time=str(now_ts), last_processed_ts=latest_hour_ts,
            )
            state["cash_usd"] -= decision.total_capital_usd
            action_taken = "opened"
            append_jsonl(journal_dir / "shadow_grid_trades.jsonl", {
                "date_ts": now_ts, "action": "open", "reason": decision.reason,
                "deployed_capital_usd": round(decision.total_capital_usd, 4),
            })

        state["engine_version"] = GRID_ENGINE_VERSION
        state["low_volatility_days_count"] = agent.low_volatility_days_count
        state["open_position"] = asdict(position) if position else None
        equity_now = state["cash_usd"] + (mark_grid_to_market(position, current_price) if position else 0.0)
        _save_state(journal_dir, state)
        append_jsonl(journal_dir / "shadow_grid_equity.jsonl", {"ts": now_ts, "equity_usd": round(equity_now, 4)})

        return {"action": action_taken, "equity_usd": round(equity_now, 4), "open_position": state.get("open_position")}
    except Exception as exc:  # noqa: BLE001 - shadow ต้องไม่พังของจริงเด็ดขาด
        return {"action": "error", "error": str(exc), "equity_usd": None, "open_position": None}

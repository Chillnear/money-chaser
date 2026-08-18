"""
Shadow tracker — คำนวณกลยุทธ์ grid trading (GridTradingAgent) คู่กับ AI จริงทุกวันแบบ deterministic
(ไม่ใช้ AI/LLM เลย) เก็บ equity เสมือนแยกจากบัญชีจริง/paper หลัก 100% ไม่กระทบเงินจริงหรืองบ AI เด็ดขาด
เหมือน src/shadow.py (funding_carry shadow) แต่คนละไฟล์เพราะ grid position ไม่มี "exit price" เดียว
(เปิดหลายระดับพร้อมกัน) ใช้ apply_daily_grid_pnl() ร่วมกับ scripts/backtest_grid_farming.py

ที่มา: backtest จริง (state/grid_farming_backtest/, ดู scripts/backtest_grid_farming.py docstring) หลังแก้
บั๊ก win_rate 100% แล้ว ให้ผลบวกทุกเหรียญที่ทดสอบ (8/8) ในช่วง 2026-02-19 ถึง 2026-08-17 แต่ backtest ใช้
daily OHLC ประมาณการ fill ไม่ใช่ tick data จริง ยังมี bias ทางบวกหลงเหลืออยู่บ้าง — ต้องยืนยันด้วยข้อมูลจริง
วันต่อวัน (shadow) ก่อนตัดสินใจเปลี่ยนแปลงเงินจริงใดๆ เหมือนที่เคยทำกับ funding_carry มาก่อน

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

from src.agents.grid_trader import GridPosition, GridTradingAgent, apply_daily_grid_pnl
from src.data.market_volatility import compute_volatility_24h
from src.util.io import append_jsonl, load_json, save_json

SHADOW_GRID_COIN = "BTC"
SHADOW_GRID_STARTING_EQUITY_USD = 28.0  # เริ่มเท่าทุนจริงตอนสร้างเพื่อเทียบกันตรงๆได้ง่าย ไม่ผูกกับทุนจริงปัจจุบัน


def _state_path(journal_dir: Path) -> Path:
    return journal_dir / "shadow_grid_state.json"


def _default_state() -> dict:
    return {"cash_usd": SHADOW_GRID_STARTING_EQUITY_USD, "open_position": None, "realized_pnl_usd": 0.0}


def _load_state(journal_dir: Path) -> dict:
    return load_json(_state_path(journal_dir), default=_default_state())


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
        state = _load_state(journal_dir)
        agent = GridTradingAgent()
        fee_per_leg_pct = settings.risk.costs.taker_fee_pct + settings.risk.costs.assumed_slippage_pct

        candles = hl_client.get_candles(coin, interval="1d", lookback_days=30)
        if len(candles) < 8:
            # ข้อมูลไม่พอคำนวณ volatility — ไม่ทำอะไรวันนี้ ไม่ถือว่า error (แค่รอข้อมูลพอ)
            equity_now = state["cash_usd"] + (
                state["open_position"]["total_capital"] + state["open_position"]["accumulated_pnl"]
                if state.get("open_position") else 0.0
            )
            return {"action": "waiting_for_data", "equity_usd": round(equity_now, 4), "open_position": state.get("open_position")}

        closes = [float(c["c"]) for c in candles]
        current_price = closes[-1]
        day_high, day_low = float(candles[-1]["h"]), float(candles[-1]["l"])
        vol_24h = compute_volatility_24h(closes, lookback=7)

        position: GridPosition | None = GridPosition(**state["open_position"]) if state.get("open_position") else None
        realized_pnl_usd = state.get("realized_pnl_usd", 0.0)
        action_taken = "held_position" if position else "flat"

        if position is not None:
            realized_pnl_usd = apply_daily_grid_pnl(
                position, day_high, day_low, current_price, closes, fee_per_leg_pct, realized_pnl_usd,
            )

        decision = agent.decide(
            current_position=position, current_price=current_price, volatility_24h=vol_24h,
            available_capital_usd=state["cash_usd"] if position is None else 0.0,
        )

        if decision.action == "close" and position is not None:
            state["cash_usd"] += position.total_capital + position.accumulated_pnl
            action_taken = f"closed_{'profit' if position.accumulated_pnl >= 0 else 'loss'}"
            append_jsonl(journal_dir / "shadow_grid_trades.jsonl", {
                "date_ts": now_ts, "action": "close", "reason": decision.reason,
                "pnl_usd": round(position.accumulated_pnl, 4), "deployed_capital_usd": round(position.total_capital, 4),
            })
            position = None
            realized_pnl_usd = 0.0
        elif decision.action == "open" and position is None:
            position = GridPosition(
                symbol=coin, center_price=current_price, grid_width_pct=decision.grid_width_pct,
                num_levels=decision.num_levels, total_capital=decision.total_capital_usd, entry_time=str(now_ts),
            )
            state["cash_usd"] -= decision.total_capital_usd
            realized_pnl_usd = 0.0
            action_taken = "opened"
            append_jsonl(journal_dir / "shadow_grid_trades.jsonl", {
                "date_ts": now_ts, "action": "open", "reason": decision.reason,
                "deployed_capital_usd": round(decision.total_capital_usd, 4),
            })

        state["open_position"] = {
            "symbol": position.symbol, "center_price": position.center_price,
            "grid_width_pct": position.grid_width_pct, "num_levels": position.num_levels,
            "total_capital": position.total_capital, "orders": [], "filled_volume_usd": position.filled_volume_usd,
            "accumulated_pnl": position.accumulated_pnl, "entry_time": position.entry_time,
        } if position else None
        state["realized_pnl_usd"] = realized_pnl_usd
        equity_now = state["cash_usd"] + (position.total_capital + position.accumulated_pnl if position else 0.0)
        _save_state(journal_dir, state)
        append_jsonl(journal_dir / "shadow_grid_equity.jsonl", {"ts": now_ts, "equity_usd": round(equity_now, 4)})

        return {"action": action_taken, "equity_usd": round(equity_now, 4), "open_position": state.get("open_position")}
    except Exception as exc:  # noqa: BLE001 - shadow ต้องไม่พังของจริงเด็ดขาด
        return {"action": "error", "error": str(exc), "equity_usd": None, "open_position": None}

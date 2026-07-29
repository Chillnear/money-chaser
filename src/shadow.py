"""
Shadow tracker — คำนวณกลยุทธ์ funding_carry คู่กับ AI จริงทุกวันแบบ deterministic (ไม่มี AI/LLM เลย)
เก็บ equity เสมือนแยกจากบัญชีจริง/paper หลัก 100% ไม่กระทบเงินจริงหรืองบ AI เด็ดขาด
(BUILD-SPEC.md §2 ขั้น 12 "baseline_shadow" ที่วางแผนไว้ตั้งแต่ต้นแต่ยังไม่เคยสร้างจริง — P5.9)

ที่มา: scripts/rule_backtest.py (ย้อนหลัง 2 ปี) พบว่า funding_carry ให้ผลบวกทั้งช่วง 2 ปี (+1.53 USD/139
ไม้) และ 180 วันล่าสุด (+2.67 USD/32 ไม้ ชนะ 62.5%) ขณะที่ trend-following (ใกล้เคียงแนวที่ AI จริงเอนเอียง
ไปทาง) กลับขาดทุนหนักช่วงหลัง (-4.23 USD/27 ไม้) — ตรงกับผลขาดทุนจริงของระบบ AI ช่วงนี้พอดี แต่ backtest
ย้อนอดีตไม่รับประกันผลใน live ต้องยืนยันด้วยข้อมูลจริงวันต่อวันก่อนตัดสินใจเปลี่ยนแปลงเงินจริงใดๆ

ต้อง fail-safe เสมอ: exception ใดๆในโมดูลนี้ต้องไม่ทำให้ pipeline การเทรดจริงพังหรือหยุดทำงานเด็ดขาด —
ฟังก์ชันนี้จับ exception ไว้เองข้างในแล้วคืน {"action": "error", ...} แทนการ raise ต่อ (caller ไม่ต้อง
ครอบ try/except ซ้ำ แต่ main.py ยังครอบไว้อีกชั้นเผื่อ defensive เพราะจุดนี้กระทบการเทรดจริงไม่ได้เด็ดขาด)
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from src.execution.broker_base import Position
from src.execution.broker_paper import PaperBroker
from src.risk.sizing import compute_position_size
from src.shadow_strategies import decide_funding_carry
from src.util.io import append_jsonl, load_json, save_json

SHADOW_RISK_MULTIPLIER = 0.5  # เหมือน BASELINE_RISK_MULTIPLIER — กฎง่ายๆไม่ผ่าน agent debate เช่นกัน
SHADOW_STARTING_EQUITY_USD = 28.0  # เริ่มเท่าทุนจริงตอนสร้างเพื่อเทียบกันตรงๆได้ง่าย ไม่ผูกกับทุนจริงปัจจุบัน


def _state_path(journal_dir: Path) -> Path:
    return journal_dir / "shadow_funding_carry_state.json"


def _load_state(journal_dir: Path) -> dict:
    return load_json(_state_path(journal_dir), default={"equity_usd": SHADOW_STARTING_EQUITY_USD, "open_position": None})


def _save_state(journal_dir: Path, state: dict) -> None:
    save_json(_state_path(journal_dir), state)


def run_funding_carry_shadow_day(
    settings,
    hl_client,
    shortlist: list[dict],
    regime_by_coin: dict,
    price_features_by_coin: dict,
    universe_snapshot: list[dict],
    now_ts: float,
    journal_dir: Path,
) -> dict:
    """รันกลยุทธ์ funding_carry แบบ deterministic คู่กับ AI จริง 1 วัน คืน dict สรุปสำหรับ LINE report"""
    try:
        state = _load_state(journal_dir)
        broker = PaperBroker(
            starting_equity_usd=state["equity_usd"],
            taker_fee_pct=settings.risk.costs.taker_fee_pct,
            slippage_pct=settings.risk.costs.assumed_slippage_pct,
        )
        action_taken = "held_position" if state.get("open_position") else "flat"

        if state.get("open_position"):
            position = Position(**state["open_position"])
            candles = hl_client.get_candles(position.asset, interval="1d", lookback_days=2)
            if candles:
                latest = candles[-1]
                candle_high, candle_low = float(latest["h"]), float(latest["l"])
                mid_price = float(latest["c"])
                exit_decision = broker.evaluate_exit(
                    position, candle_high, candle_low, now_ts,
                    settings.risk.stops.max_holding_days, invalidation_triggered=False,
                )
                if exit_decision.should_exit:
                    if exit_decision.reason == "stop_loss_hit":
                        exit_price = position.stop_price
                    elif exit_decision.reason == "take_profit_hit":
                        exit_price = position.take_profit_price
                    else:
                        exit_price = mid_price
                    closed = broker.close_position(position, exit_price, now_ts, exit_decision.reason)
                    append_jsonl(journal_dir / "shadow_funding_carry_trades.jsonl", asdict(closed))
                    state["open_position"] = None
                    state["equity_usd"] = broker.get_account_equity()
                    action_taken = f"closed_{exit_decision.reason}"
        else:
            decision = decide_funding_carry(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot)
            if decision.action != "flat" and decision.asset:
                sizing_result = compute_position_size(
                    equity_usd=broker.get_account_equity(),
                    atr_pct=price_features_by_coin.get(decision.asset, {}).get("atr_pct"),
                    risk_per_trade_pct=settings.risk.sizing.risk_per_trade_pct * SHADOW_RISK_MULTIPLIER,
                    min_notional_usd=settings.risk.sizing.min_notional_usd,
                    max_notional_usd=settings.risk.sizing.max_notional_usd,
                    max_notional_pct_of_equity=settings.risk.sizing.max_notional_pct_of_equity,
                    min_notional_override_max_risk_pct=settings.risk.sizing.min_notional_override_max_risk_pct,
                    atr_multiple=settings.risk.stops.atr_multiple,
                    stop_floor_pct=settings.risk.stops.stop_floor_pct,
                    stop_cap_pct=settings.risk.stops.stop_cap_pct,
                    reward_risk_ratio=settings.risk.stops.reward_risk_ratio,
                    max_leverage=settings.risk.mode_defaults.max_leverage,
                )
                if sizing_result.decision == "OK":
                    mid_price = next((e["mark_px"] for e in universe_snapshot if e["coin"] == decision.asset), None)
                    if mid_price:
                        position = broker.open_position(
                            asset=decision.asset, side=decision.action, notional_usd=sizing_result.notional_usd,
                            mid_price=mid_price, stop_pct=sizing_result.stop_pct,
                            take_profit_pct=sizing_result.take_profit_pct, now_ts=now_ts,
                        )
                        state["open_position"] = asdict(position)
                        action_taken = f"opened_{decision.action}"

        state["equity_usd"] = broker.get_account_equity()
        _save_state(journal_dir, state)
        append_jsonl(journal_dir / "shadow_funding_carry_equity.jsonl", {"ts": now_ts, "equity_usd": state["equity_usd"]})

        return {"action": action_taken, "equity_usd": state["equity_usd"], "open_position": state.get("open_position")}
    except Exception as exc:  # noqa: BLE001 - shadow ต้องไม่พังของจริงเด็ดขาด
        return {"action": "error", "error": str(exc), "equity_usd": None, "open_position": None}

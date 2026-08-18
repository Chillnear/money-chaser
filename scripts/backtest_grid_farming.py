"""
Backtest จริงสำหรับ GridTradingAgent และ FundingFarmingAgent (P5.10) — ใช้ราคา/funding ย้อนหลังจริงจาก
Hyperliquid เหมือน scripts/rule_backtest.py แต่แยกเป็นสคริปต์ของตัวเอง เพราะ position ของสองกลยุทธ์นี้มี
รูปแบบต่างจาก trend/mean-reversion/funding-carry เดิม (ที่เป็น long/short เดี่ยวมี stop/TP ชัดเจน ใช้
PaperBroker เดิมได้ตรงๆ):
  - Grid: เปิดออเดอร์หลายระดับพร้อมกัน ไม่มี "exit price" เดียว
  - Funding farmer: ถือ position คู่ (long spot + short perp) เก็บ funding สะสมไปเรื่อยๆ ไม่ใช่เดิมพันทิศทาง

ที่มา: ตรวจสอบ scripts/backtest_multi_strategy.py (ที่ Codex สร้างไว้ก่อนหน้า) แล้วพบว่า**ไม่ใช่ backtest
จริง** — ใช้ราคาสุ่มปลอม (generate_synthetic_prices) และ win rate/return ที่ตั้งไว้ตายตัวในโค้ดเลย ไม่ได้
เรียก GridTradingAgent/FundingFarmingAgent จริงด้วยซ้ำ ผลลัพธ์จากสคริปต์นั้นไม่มีความหมายอะไรเลย ต้องสร้าง
ใหม่ให้ใช้ข้อมูลจริงและเรียก agent จริงแทน

ข้อจำกัดที่ต้องรู้ (fail-safe โดยตั้งใจ ไม่ใช่บั๊ก — ต่างจาก backtest_multi_strategy.py เดิมที่ไม่บอกข้อจำกัด):
  - ไม่มีข้อมูล spot price แยกจาก perp ย้อนหลังฟรี -> สมมติ spot_price = perp close ราคาเดียวกัน (basis
    ~0%) ผล backtest ของ funding farmer จึงเป็น "best case" ที่ไม่มี basis risk จริง อาจดีกว่าของจริงได้
  - ไม่มี tick-level data ให้จำลอง grid fill ทีละออเดอร์จริง -> ประมาณจาก daily high-low range เทียบกับ
    ขอบเขต grid แทน (ยิ่งวันไหน range กว้างครอบคลุมทั้ง 2 ฝั่งของ grid มาก ยิ่งนับว่าเก็บ cycle ได้มาก)
  - liquidity ประมาณจาก daily volume ของแท่งเทียน (v * close) ไม่ใช่ order book depth จริง — ใช้แค่ log
    warning ใน agent ไม่ได้ hard-block อยู่แล้ว จึงไม่กระทบผลลัพธ์มากนัก

บั๊กที่เจอและแก้แล้ว (รันครั้งแรก 2026-08-17): grid backtest ให้ win_rate 100% ทุกเหรียญที่ทดสอบ (8/8) พร้อม
กำไรเกินจริงมาก (เช่น PUMP $28 -> $7,655 ใน 180 วัน) ซึ่งเป็นไปไม่ได้ในชีวิตจริง — สาเหตุคือสูตรนับ cycle
เดิมบวกกำไรได้อย่างเดียว ไม่มีทางลบเลย (ไม่ว่าราคาจะวิ่งทางไหน) จึงชนะทุกไม้เสมอ 100% แล้วยิ่ง compound ทบต้น
ผ่านทุนที่โตขึ้นเรื่อยๆ ยิ่งดูเกินจริงหนักขึ้น แก้โดยเพิ่ม mark-to-market ขาดทุนที่ยังไม่ realize เวลาราคาหลุด
ขอบล่างของ grid โดยไม่กลับมา (ดูคอมเมนต์ในโค้ด run_grid_backtest) ตอนนี้ผลลัพธ์ควรมีทั้งไม้ชนะและไม้แพ้แล้ว
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from src.agents.funding_farmer import FarmingPosition, FundingFarmingAgent, FundingRate  # noqa: E402
from src.agents.grid_trader import GridPosition, GridTradingAgent, apply_daily_grid_pnl  # noqa: E402
from src.data.market_volatility import compute_volatility_24h  # noqa: E402
from src.settings import load_settings  # noqa: E402
from src.util.io import save_json  # noqa: E402

from backtest import DEFAULT_COINS, HistoricalHyperliquidClient, date_range, date_to_ms, fetch_backtest_history  # noqa: E402
from rule_backtest import get_current_liquid_universe_coins  # noqa: E402

WARMUP_DAYS = 10  # ต้องมีแท่งเทียนย้อนหลังพอคำนวณ 7-day volatility ตั้งแต่วันแรกที่จำลอง


def run_grid_backtest(
    settings, hist_client: HistoricalHyperliquidClient, coin: str, start_date: str, end_date: str, starting_equity_usd: float,
) -> dict:
    """จำลอง GridTradingAgent วันต่อวันด้วยราคาจริง — ประมาณ cycle ที่เก็บได้จาก daily high-low range
    เทียบกับขอบเขต grid จริง (ไม่ใช่สุ่ม) ดู docstring บนสุดของไฟล์สำหรับข้อจำกัดของการประมาณนี้
    """
    agent = GridTradingAgent()
    fee_per_leg_pct = settings.risk.costs.taker_fee_pct + settings.risk.costs.assumed_slippage_pct

    cash = starting_equity_usd
    position: GridPosition | None = None
    realized_pnl_usd = 0.0  # กำไรจาก cycle ที่ "เกิดจริง" สะสมตลอดชีวิตของ position ปัจจุบัน (monotonic)
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for date_str in date_range(start_date, end_date):
        hist_client.set_as_of(date_to_ms(date_str))
        candles = hist_client.get_candles(coin, interval="1d", lookback_days=400)
        if len(candles) < 8:
            continue  # ยังไม่พ้น warm-up ของ 7-day volatility

        closes = [float(c["c"]) for c in candles]
        current_price = closes[-1]
        day_high, day_low = float(candles[-1]["h"]), float(candles[-1]["l"])
        vol_24h = compute_volatility_24h(closes, lookback=7)

        if position is not None:
            # ตรรกะคำนวณ P&L รายวัน (รวม fix ของบั๊ก win_rate 100%) อยู่ใน apply_daily_grid_pnl() ใช้
            # ร่วมกับ src/shadow_grid.py เพื่อไม่ให้ backtest กับ live shadow ตรรกะเพี้ยนไปคนละทาง
            realized_pnl_usd = apply_daily_grid_pnl(
                position, day_high, day_low, current_price, closes, fee_per_leg_pct, realized_pnl_usd,
            )

        decision = agent.decide(
            current_position=position, current_price=current_price, volatility_24h=vol_24h,
            available_capital_usd=cash if position is None else 0.0,
        )

        if decision.action == "close" and position is not None:
            realized = position.total_capital + position.accumulated_pnl
            cash += realized
            trades.append({
                "date": date_str, "action": "close", "reason": decision.reason,
                "pnl_usd": position.accumulated_pnl, "deployed_capital_usd": position.total_capital,
            })
            position = None
            realized_pnl_usd = 0.0
        elif decision.action == "open" and position is None:
            position = GridPosition(
                symbol=coin, center_price=current_price, grid_width_pct=decision.grid_width_pct,
                num_levels=decision.num_levels, total_capital=decision.total_capital_usd, entry_time=date_str,
            )
            cash -= decision.total_capital_usd
            realized_pnl_usd = 0.0
            trades.append({"date": date_str, "action": "open", "reason": decision.reason, "deployed_capital_usd": decision.total_capital_usd})

        equity_today = cash + (position.total_capital + position.accumulated_pnl if position else 0.0)
        equity_curve.append({"date": date_str, "equity_usd": equity_today})

    final_equity = equity_curve[-1]["equity_usd"] if equity_curve else starting_equity_usd
    closes_only = [t for t in trades if t["action"] == "close"]
    wins = [t for t in closes_only if t["pnl_usd"] > 0]

    return {
        "coin": coin, "start_date": start_date, "end_date": end_date,
        "grids_opened": len([t for t in trades if t["action"] == "open"]),
        "grids_closed": len(closes_only),
        "win_rate_pct": round(len(wins) / len(closes_only) * 100, 1) if closes_only else 0.0,
        "starting_equity_usd": starting_equity_usd, "final_equity_usd": round(final_equity, 2),
        "total_pnl_usd": round(final_equity - starting_equity_usd, 2),
        "trade_list": trades, "equity_curve": equity_curve,
    }


def run_funding_farmer_backtest(
    settings, hist_client: HistoricalHyperliquidClient, coin: str, start_date: str, end_date: str, starting_equity_usd: float,
) -> dict:
    """จำลอง FundingFarmingAgent วันต่อวันด้วย funding rate จริงจาก Hyperliquid — สมมติ spot_price=perp
    price (ไม่มี basis risk จริง, ดู docstring บนสุดของไฟล์) ค่าธรรมเนียมใช้ค่าจริงจาก risk.yaml (2 ขา
    เข้า/ออก) แทนสมมติฐาน 0.8%/ปี ที่ estimate_annual_return() ใช้
    """
    agent = FundingFarmingAgent()
    fee_per_leg_pct = settings.risk.costs.taker_fee_pct + settings.risk.costs.assumed_slippage_pct

    cash = starting_equity_usd
    position: FarmingPosition | None = None
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for date_str in date_range(start_date, end_date):
        hist_client.set_as_of(date_to_ms(date_str))
        snapshot = hist_client.get_universe_snapshot()
        entry = next((e for e in snapshot if e["coin"] == coin), None)
        if entry is None:
            continue  # ยังไม่พ้น warm-up

        current_price = entry["mark_px"]
        rate_per_8h_pct = entry["funding"] * 100  # Hyperliquid ส่ง fraction ต่อรอบ 8 ชม. -> แปลงเป็น %
        liquidity_usd = entry["day_volume_usd"]

        funding_rate = FundingRate(
            symbol=coin, rate_per_8h=rate_per_8h_pct, spot_price=current_price, perp_price=current_price,
            spot_liquidity_usd=liquidity_usd, perp_liquidity_usd=liquidity_usd,
        )

        if position is not None:
            total_notional = position.long_notional_usd + position.short_notional_usd
            earned_today = (rate_per_8h_pct / 100) * 3 * total_notional  # 3 รอบ 8 ชม./วัน
            position.accumulated_funding_usd += earned_today

        decision = agent.decide(
            current_position=position, funding_rate=funding_rate,
            available_capital_usd=cash if position is None else 0.0,
        )

        if decision.action == "close" and position is not None:
            total_notional = position.long_notional_usd + position.short_notional_usd
            exit_fee_usd = total_notional * fee_per_leg_pct / 100 * 2  # 2 ขา (spot+perp)
            realized = total_notional + position.accumulated_funding_usd - exit_fee_usd
            cash += realized
            trades.append({
                "date": date_str, "action": "close", "reason": decision.reason,
                "pnl_usd": position.accumulated_funding_usd - exit_fee_usd, "deployed_capital_usd": total_notional,
            })
            position = None
        elif decision.action == "open" and position is None:
            entry_notional = decision.long_notional_usd + decision.short_notional_usd
            entry_fee_usd = entry_notional * fee_per_leg_pct / 100 * 2
            position = FarmingPosition(
                symbol=coin, long_notional_usd=decision.long_notional_usd, short_notional_usd=decision.short_notional_usd,
                entry_price=current_price, entry_time=date_str, accumulated_funding_usd=-entry_fee_usd,
            )
            cash -= entry_notional
            trades.append({"date": date_str, "action": "open", "reason": decision.reason, "deployed_capital_usd": entry_notional})

        deployed = (position.long_notional_usd + position.short_notional_usd + position.accumulated_funding_usd) if position else 0.0
        equity_curve.append({"date": date_str, "equity_usd": cash + deployed})

    final_equity = equity_curve[-1]["equity_usd"] if equity_curve else starting_equity_usd
    closes_only = [t for t in trades if t["action"] == "close"]
    wins = [t for t in closes_only if t["pnl_usd"] > 0]

    return {
        "coin": coin, "start_date": start_date, "end_date": end_date,
        "positions_opened": len([t for t in trades if t["action"] == "open"]),
        "positions_closed": len(closes_only),
        "win_rate_pct": round(len(wins) / len(closes_only) * 100, 1) if closes_only else 0.0,
        "starting_equity_usd": starting_equity_usd, "final_equity_usd": round(final_equity, 2),
        "total_pnl_usd": round(final_equity - starting_equity_usd, 2),
        "trade_list": trades, "equity_curve": equity_curve,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Backtest จริง (ไม่ใช้ AI) สำหรับ grid_trader และ funding_farmer ด้วยข้อมูลราคา/funding ย้อนหลังจริง")
    parser.add_argument("--coins", default=",".join(DEFAULT_COINS), help='รายชื่อเหรียญคั่นด้วย comma หรือ "auto" เพื่อดึงพูล liquid จริงจาก Hyperliquid')
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--starting-equity-usd", type=float, default=28.0)
    args = parser.parse_args()

    settings = load_settings()

    if args.coins.strip().lower() == "auto":
        coins = get_current_liquid_universe_coins(
            settings.risk.mode_defaults.min_24h_volume_usd, settings.risk.mode_defaults.min_open_interest_usd,
            settings.risk.mode_defaults.always_include,
        )
        print(f"โหมด auto: พบเหรียญ liquid พอ {len(coins)} ตัว: {coins}")
    else:
        coins = [c.strip() for c in args.coins.split(",") if c.strip()]

    lookback_needed = args.days + WARMUP_DAYS
    print(f"กำลังดึงข้อมูลราคา/funding ย้อนหลัง {lookback_needed} วันสำหรับ {coins} (ฟรี ไม่เสีย AI)...")
    candles_by_coin, funding_by_coin = fetch_backtest_history(coins, lookback_needed)
    hist_client = HistoricalHyperliquidClient(candles_by_coin, funding_by_coin)

    import datetime as dt

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)

    out_dir = REPO_ROOT / "state" / "grid_farming_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: dict[str, dict] = {"grid": {}, "funding_farmer": {}}
    for coin in coins:
        print(f"--- grid: {coin} ---")
        grid_result = run_grid_backtest(settings, hist_client, coin, start.isoformat(), end.isoformat(), args.starting_equity_usd)
        all_results["grid"][coin] = grid_result
        save_json(out_dir / f"grid_{coin}_trades.json", grid_result["trade_list"])
        print(f"  grids opened={grid_result['grids_opened']} closed={grid_result['grids_closed']} win_rate={grid_result['win_rate_pct']}% pnl={grid_result['total_pnl_usd']}")

        print(f"--- funding_farmer: {coin} ---")
        farmer_result = run_funding_farmer_backtest(settings, hist_client, coin, start.isoformat(), end.isoformat(), args.starting_equity_usd)
        all_results["funding_farmer"][coin] = farmer_result
        save_json(out_dir / f"funding_farmer_{coin}_trades.json", farmer_result["trade_list"])
        print(f"  positions opened={farmer_result['positions_opened']} closed={farmer_result['positions_closed']} win_rate={farmer_result['win_rate_pct']}% pnl={farmer_result['total_pnl_usd']}")

    summary = {
        strategy: {coin: {k: v for k, v in r.items() if k not in ("trade_list", "equity_curve")} for coin, r in per_coin.items()}
        for strategy, per_coin in all_results.items()
    }
    save_json(out_dir / "summary.json", summary)
    print(f"\nเขียนสรุปผลลง {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

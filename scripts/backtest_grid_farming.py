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
  - Grid engine v2 ใช้แท่ง 1h และจำลอง pending orders + cash/base inventory ทีละแท่ง ลด bias จาก daily
    high-low ลงมาก แต่ยังไม่ใช่ tick/order-book replay; order ที่สร้างจาก fill จะ active แท่งถัดไปเท่านั้น
  - liquidity ประมาณจาก daily volume ของแท่งเทียน (v * close) ไม่ใช่ order book depth จริง — ใช้แค่ log
    warning ใน agent ไม่ได้ hard-block อยู่แล้ว จึงไม่กระทบผลลัพธ์มากนัก

ผล engine daily-OHLC รุ่นก่อนหน้าถูกยกเลิกทั้งหมด เพราะสูตรยังสร้างกำไรเกินจริงแม้แก้เบื้องต้นแล้ว
ผลที่ใช้พิจารณาต่อได้ต้องมาจาก engine_version=2 และ out-of-sample walk-forward เท่านั้น
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
from src.agents.grid_trader import (  # noqa: E402
    GridPosition,
    GridTradingAgent,
    apply_intraday_grid_candles,
    close_grid_position,
    initialize_grid_position,
    mark_grid_to_market,
)
from src.data.hl_market import HyperliquidClient  # noqa: E402
from src.data.market_volatility import compute_volatility_24h  # noqa: E402
from src.settings import load_settings  # noqa: E402
from src.util.io import save_json  # noqa: E402

from backtest import DEFAULT_COINS, HistoricalHyperliquidClient, date_range, date_to_ms, fetch_backtest_history  # noqa: E402
from rule_backtest import get_current_liquid_universe_coins  # noqa: E402

WARMUP_DAYS = 10  # ต้องมีแท่งเทียนย้อนหลังพอคำนวณ 7-day volatility ตั้งแต่วันแรกที่จำลอง


def run_grid_backtest(
    settings,
    hist_client: HistoricalHyperliquidClient,
    coin: str,
    start_date: str,
    end_date: str,
    starting_equity_usd: float,
    *,
    grid_width_pct: float = 5.0,
    min_volatility_open: float = 1.5,
    num_levels: int = 5,
) -> dict:
    """จำลอง fully-funded spot grid ด้วยแท่ง 1h, inventory, pending orders และ mark-to-market."""
    agent = GridTradingAgent(
        grid_width_pct=grid_width_pct,
        min_volatility_open=min_volatility_open,
        num_levels=num_levels,
    )
    fee_per_leg_pct = settings.risk.costs.taker_fee_pct + settings.risk.costs.assumed_slippage_pct

    cash = starting_equity_usd
    position: GridPosition | None = None
    trades: list[dict] = []
    fills: list[dict] = []
    equity_curve: list[dict] = []

    for date_str in date_range(start_date, end_date):
        hist_client.set_as_of(date_to_ms(date_str))
        candles = hist_client.get_candles(coin, interval="1d", lookback_days=400)
        if len(candles) < 8:
            continue  # ยังไม่พ้น warm-up ของ 7-day volatility

        closes = [float(c["c"]) for c in candles]
        current_price = closes[-1]
        vol_24h = compute_volatility_24h(closes, lookback=7)

        if position is not None:
            fills.extend(apply_intraday_grid_candles(
                position, hist_client.get_candles(coin, interval="1h", lookback_days=3), fee_per_leg_pct,
            ))

        decision = agent.decide(
            current_position=position, current_price=current_price, volatility_24h=vol_24h,
            available_capital_usd=cash if position is None else 0.0,
        )

        if decision.action == "close" and position is not None:
            liquidated = close_grid_position(position, current_price, fee_per_leg_pct)
            cash += liquidated
            trades.append({
                "date": date_str, "action": "close", "reason": decision.reason,
                "pnl_usd": position.accumulated_pnl, "deployed_capital_usd": position.total_capital,
            })
            position = None
        elif decision.action == "open" and position is None:
            hourly = hist_client.get_candles(coin, interval="1h", lookback_days=1)
            latest_hour_ts = int(hourly[-1].get("T", hourly[-1].get("t", 0))) if hourly else 0
            position = initialize_grid_position(
                symbol=coin, center_price=current_price, grid_width_pct=decision.grid_width_pct,
                num_levels=decision.num_levels, total_capital_usd=decision.total_capital_usd,
                entry_time=date_str, last_processed_ts=latest_hour_ts,
            )
            cash -= decision.total_capital_usd
            trades.append({"date": date_str, "action": "open", "reason": decision.reason, "deployed_capital_usd": decision.total_capital_usd})

        equity_today = cash + (mark_grid_to_market(position, current_price) if position else 0.0)
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
        "fills_count": len(fills),
        "fill_volume_usd": round(sum(f["size_usd"] for f in fills), 2),
        "execution_cost_usd": round(sum(f["fee_usd"] for f in fills), 4),
        "trade_list": trades, "fill_list": fills, "equity_curve": equity_curve,
    }


def run_grid_walk_forward(
    settings,
    hist_client: HistoricalHyperliquidClient,
    coin: str,
    start_date: str,
    end_date: str,
    starting_equity_usd: float,
    train_days: int = 60,
    test_days: int = 30,
) -> dict:
    """เลือก parameter บน train window แล้ววัดเฉพาะ test window ถัดไปแบบ rolling."""
    import datetime as dt

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    candidates = [(2.0, 1.0), (3.0, 1.5), (5.0, 1.5), (5.0, 2.5)]
    folds = []
    equity = starting_equity_usd
    cursor = start
    while cursor + dt.timedelta(days=train_days + test_days - 1) <= end:
        train_end = cursor + dt.timedelta(days=train_days - 1)
        test_start = train_end + dt.timedelta(days=1)
        test_end = test_start + dt.timedelta(days=test_days - 1)
        scored = []
        for width, min_vol in candidates:
            result = run_grid_backtest(
                settings, hist_client, coin, cursor.isoformat(), train_end.isoformat(), starting_equity_usd,
                grid_width_pct=width, min_volatility_open=min_vol,
            )
            scored.append((result["final_equity_usd"], width, min_vol))
        _, width, min_vol = max(scored)
        test_result = run_grid_backtest(
            settings, hist_client, coin, test_start.isoformat(), test_end.isoformat(), equity,
            grid_width_pct=width, min_volatility_open=min_vol,
        )
        equity = test_result["final_equity_usd"]
        folds.append({
            "train_start": cursor.isoformat(), "train_end": train_end.isoformat(),
            "test_start": test_start.isoformat(), "test_end": test_end.isoformat(),
            "selected": {"grid_width_pct": width, "min_volatility_open": min_vol},
            "test_pnl_usd": test_result["total_pnl_usd"], "test_final_equity_usd": equity,
        })
        cursor += dt.timedelta(days=test_days)
    return {
        "coin": coin, "folds": folds, "starting_equity_usd": starting_equity_usd,
        "final_equity_usd": equity, "total_pnl_usd": round(equity - starting_equity_usd, 2),
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
    parser.add_argument("--walk-forward", action="store_true", help="รัน rolling 60d train / 30d out-of-sample")
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
    live_client = HyperliquidClient()
    hourly_by_coin = {
        coin: live_client.get_candles(coin, interval="1h", lookback_days=lookback_needed)
        for coin in coins
    }
    hist_client = HistoricalHyperliquidClient(
        candles_by_coin, funding_by_coin, candles_by_interval={"1d": candles_by_coin, "1h": hourly_by_coin}
    )

    import datetime as dt

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)

    out_dir = REPO_ROOT / "state" / "grid_farming_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: dict[str, dict] = {"grid": {}, "funding_farmer": {}, "grid_walk_forward": {}}
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

        if args.walk_forward:
            wf_result = run_grid_walk_forward(
                settings, hist_client, coin, start.isoformat(), end.isoformat(), args.starting_equity_usd
            )
            all_results["grid_walk_forward"][coin] = wf_result
            print(f"  walk-forward folds={len(wf_result['folds'])} out-of-sample pnl={wf_result['total_pnl_usd']}")

    summary = {
        strategy: {coin: {k: v for k, v in r.items() if k not in ("trade_list", "fill_list", "equity_curve")} for coin, r in per_coin.items()}
        for strategy, per_coin in all_results.items()
    }
    summary["_meta"] = {
        "grid_engine_version": 2,
        "fill_interval": "1h",
        "inventory_accounting": True,
        "fees_and_slippage_included": True,
        "walk_forward": bool(args.walk_forward),
    }
    save_json(out_dir / "summary.json", summary)
    print(f"\nเขียนสรุปผลลง {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Rule-based backtest — ทดสอบ "แก่น" ของไอเดียกลยุทธ์ (ไม่มี AI/LLM เข้ามาเกี่ยวข้องเลย) กับข้อมูลราคา/
funding ย้อนหลังหลายปีได้ในไม่กี่วินาที ไม่เสียเงิน AI แม้แต่บาทเดียว

ที่มา: ผู้ใช้ถามว่า "มีแนวทางเทรดแบบอื่นไหมที่น่าจะได้กำไรมากกว่าและเห็นผลเร็วกว่านี้" หลัง backtest_compare
(P5.8) พบว่า 60 วันมีแค่ 3 ไม้ — ตัวอย่างน้อยเกินจะเชื่อผลทางสถิติได้ การรัน AI backtest ซ้ำหลายรอบเพื่อ
เทียบไอเดียกลยุทธ์ใหม่ๆ (เช่น mean-reversion, funding carry) จะช้าและเสียเงินซ้ำเหมือนที่ผ่านมา

หลักการ: reuse โค้ดจริงของระบบทั้งหมด (build_price_features, classify_regime, build_shortlist,
compute_position_size, PaperBroker.evaluate_exit/close_position) — ไม่เขียน indicator/sizing/exit
logic ซ้ำเลย เปลี่ยนแค่ "ตัวตัดสินใจ" (decide_fn) ให้เป็นกฎง่ายๆแทน LLM analyst/redteam/judge เพื่อวัดว่า
สัญญาณพื้นฐานของแต่ละไอเดียมี edge จริงไหม ก่อนจะเอาไปทดสอบกับ AI จริงต่อ (ประหยัดเงิน/เวลากว่ามาก)

กลยุทธ์ที่มีให้ทดสอบ:
  - trend_following: เหมือน src.baseline.decide() ของจริง (regime trend_up/trend_down -> long/short)
  - mean_reversion: fade ที่ขอบ Donchian สุดขั้ว + RSI สุดขั้ว (สมมติฐานตรงข้ามกับ trend_following)
  - funding_carry: เข้าฝั่งตรงข้าม funding ที่สุดขั้วที่สุดในชอร์ตลิสต์ เพื่อเก็บ funding แทนการเดาทิศทางราคา

ข้อจำกัดที่ตั้งใจ (เหมือน scripts/backtest.py): ไม่มี macro veto, ไม่มี breaker size multiplier ย้อนหลัง,
ไม่มี OI ย้อนหลัง (0.0 เสมอ) — ใช้ค่า risk_per_trade_pct x BASELINE_RISK_MULTIPLIER คงที่ทุกไม้ (เหมือนที่
baseline fallback จริงใช้ เพราะไม่มี agent debate มาช่วยยืนยัน)
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from src.baseline import BASELINE_RISK_MULTIPLIER  # noqa: E402
from src.baseline import decide as baseline_decide  # noqa: E402
from src.data.features import build_price_features  # noqa: E402
from src.data.regime import classify_regime  # noqa: E402
from src.data.screening import build_shortlist  # noqa: E402
from src.execution.broker_paper import PaperBroker  # noqa: E402
from src.risk.sizing import compute_position_size  # noqa: E402
from src.settings import load_settings  # noqa: E402
from src.shadow_strategies import Decision, decide_funding_carry, decide_mean_reversion  # noqa: E402
from src.util.io import save_json  # noqa: E402

from backtest import (  # noqa: E402
    DEFAULT_COINS,
    HistoricalHyperliquidClient,
    date_range,
    date_to_ms,
    fetch_backtest_history,
)

WARMUP_DAYS = 400  # ให้ vol_percentile_1y (มองย้อน 365 วัน) มีข้อมูลพอตั้งแต่วันแรกที่จำลอง


def get_current_liquid_universe_coins(min_24h_volume_usd: float, min_open_interest_usd: float, always_include: list[str]) -> list[str]:
    """ดึงรายชื่อเหรียญที่ liquid พอ ณ ตอนนี้จริงจาก Hyperliquid (กรองด้วยเกณฑ์เดียวกับที่ production ใช้จริง
    ทุกวัน — src.data.screening.filter_universe) แทนการจำกัดแค่ ["BTC","PAXG"] ที่เป็นเพียงความง่ายของ
    สคริปต์ทดสอบ — production จริงสแกนทั้งพูล (~15-30+ เหรียญที่ liquid) ไม่ได้จำกัดแค่ 2 ตัวนี้เลย

    หมายเหตุ: การกรองนี้เป็นภาพ ณ ปัจจุบัน ไม่ใช่ย้อนอดีต (Hyperliquid ไม่มี historical universe list ให้)
    แต่พูล coin ที่ liquid พอบน Hyperliquid ไม่เปลี่ยนบ่อยมาก จึงเพียงพอสำหรับทดสอบไอเดียกลยุทธ์ย้อนหลัง
    """
    from src.data.hl_market import HyperliquidClient
    from src.data.screening import filter_universe

    live_client = HyperliquidClient()
    snapshot = live_client.get_universe_snapshot()
    filtered = filter_universe(snapshot, min_24h_volume_usd, min_open_interest_usd, always_include)
    return [s["coin"] for s in filtered]

# decide_mean_reversion / decide_funding_carry ย้ายไป src/shadow_strategies.py (P5.9) เพื่อให้
# src/shadow.py (รัน funding_carry คู่ AI จริงทุกวันแบบ shadow) เรียกกฎเดียวกันเป๊ะๆ ไม่ต้องเขียนซ้ำสองที่


def decide_trend_following(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot) -> Decision:
    """เหมือน src.baseline.decide() เป๊ะ — ตามเทรนด์ตรงๆ ไม่มีการตีความเพิ่ม"""
    bd = baseline_decide(shortlist, regime_by_coin, default_stop_pct=0.0, default_take_profit_pct=0.0)
    return Decision(action=bd.action, asset=bd.asset, reasoning=bd.reasoning)


def decide_regime_switch(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot) -> Decision:
    """ผู้ใช้ถามว่า "ถ้ากลยุทธ์นึงดีกับตลาดแบบนึง อีกอันดีกับอีกแบบ จะสลับใช้ตามสถานการณ์ได้ไหม" — นี่คือ
    ไอเดียนั้น (regime switching): ถ้ามีเหรียญใน shortlist ที่ trend ชัด (trend_up/trend_down ตาม
    src.data.regime — decide_trend_following จะไม่ flat) ให้ตามเทรนด์ไปเลย; ถ้าไม่มีเทรนด์ชัดสักตัว (ตลาด
    เป็น chop ทั้งพูล) ค่อยลอง funding_carry แทน (เก็บ funding ระหว่างรอเทรนด์ชัดกลับมา)

    หลักการนี้มีเหตุผลรองรับ (ต่างจาก override ที่เคยลองแล้วถอดออก P5.9b เพราะ percentile หลอกตาในพูลเล็ก)
    แต่ก็ยังต้องพิสูจน์กับ backtest พูลเหรียญจริงก่อนเชื่อ — เทสต์นี้ยังเป็นแค่ rule_backtest ไม่ใช่การ
    ตัดสินใจจริงใน src/main.py
    """
    trend_decision = decide_trend_following(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot)
    if trend_decision.action != "flat":
        return Decision(trend_decision.action, trend_decision.asset, f"[regime=trend] {trend_decision.reasoning}")

    funding_decision = decide_funding_carry(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot)
    if funding_decision.action != "flat":
        return Decision(funding_decision.action, funding_decision.asset, f"[regime=chop->funding] {funding_decision.reasoning}")

    return Decision("flat", None, "ไม่มีเทรนด์ชัดและ funding ก็ไม่สุดขั้ว — ไม่มีกลยุทธ์ไหนอยากเข้า")


STRATEGIES = {
    "trend_following": decide_trend_following,
    "mean_reversion": decide_mean_reversion,
    "funding_carry": decide_funding_carry,
    "regime_switch": decide_regime_switch,
}


def run_rule_backtest(
    settings,
    hist_client: HistoricalHyperliquidClient,
    coins: list[str],
    start_date: str,
    end_date: str,
    starting_equity_usd: float,
    decide_fn,
) -> dict:
    broker = PaperBroker(
        starting_equity_usd=starting_equity_usd,
        taker_fee_pct=settings.risk.costs.taker_fee_pct,
        slippage_pct=settings.risk.costs.assumed_slippage_pct,
    )
    mode_defaults_dict = settings.risk.mode_defaults.model_dump()

    position = None
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for date_str in date_range(start_date, end_date):
        as_of_ms = date_to_ms(date_str)
        hist_client.set_as_of(as_of_ms)
        now_ts = as_of_ms / 1000.0 + 3600

        universe_snapshot = hist_client.get_universe_snapshot()
        price_features_by_coin = {}
        regime_by_coin = {}
        for coin in coins:
            pf = build_price_features(hist_client.get_candles(coin, interval="1d", lookback_days=400), {})
            price_features_by_coin[coin] = pf
            if pf.get("ok"):
                regime_by_coin[coin] = classify_regime(pf)

        if position is not None:
            candles = hist_client.get_candles(position.asset, interval="1d", lookback_days=2)
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
                    trades.append(asdict(closed))
                    position = None
            equity_curve.append({"date": date_str, "equity": broker.get_account_equity()})
            continue  # มีไม้เปิดอยู่แล้ว (max_open_positions=1 เหมือนของจริง) — ไม่เปิดไม้ใหม่วันนี้

        shortlist_result = build_shortlist(universe_snapshot, price_features_by_coin, mode_defaults_dict)
        decision = decide_fn(shortlist_result["shortlist"], regime_by_coin, price_features_by_coin, universe_snapshot)

        if decision.action != "flat" and decision.asset:
            sizing_result = compute_position_size(
                equity_usd=broker.get_account_equity(),
                atr_pct=price_features_by_coin.get(decision.asset, {}).get("atr_pct"),
                risk_per_trade_pct=settings.risk.sizing.risk_per_trade_pct * BASELINE_RISK_MULTIPLIER,
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

        equity_curve.append({"date": date_str, "equity": broker.get_account_equity()})

    final_equity = broker.get_account_equity()
    wins = [t for t in trades if (t.get("pnl_usd") or 0.0) > 0]

    return {
        "start_date": start_date,
        "end_date": end_date,
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "starting_equity_usd": starting_equity_usd,
        "final_equity_usd": round(final_equity, 2),
        "total_pnl_usd": round(final_equity - starting_equity_usd, 2),
        "trade_list": trades,
    }


def print_comparison_table(results: dict) -> None:
    print("\n=== เทียบไอเดียกลยุทธ์ (rule-based, ไม่มี AI) ===")
    header = f"{'strategy':<20} {'trades':<8} {'win%':<8} {'pnl(USD)':<10} {'final_eq':<10}"
    print(header)
    print("-" * len(header))
    for name, r in sorted(results.items(), key=lambda kv: -kv[1]["total_pnl_usd"]):
        print(f"{name:<20} {r['trades']:<8} {r['win_rate_pct']:<8} {r['total_pnl_usd']:<10} {r['final_equity_usd']:<10}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ทดสอบไอเดียกลยุทธ์แบบไม่ใช้ AI กับข้อมูลราคาย้อนหลังหลายปี (ฟรี เร็ว)")
    parser.add_argument(
        "--coins", default=",".join(DEFAULT_COINS),
        help='รายชื่อเหรียญคั่นด้วย comma หรือใส่ "auto" เพื่อดึงพูล liquid ทั้งหมดจาก Hyperliquid ตอนนี้จริง (เหมือน production)',
    )
    parser.add_argument("--days", type=int, default=720, help="จำลองกี่วันย้อนหลังจากเมื่อวาน (ค่าเริ่มต้น ~2 ปี เพราะฟรี)")
    parser.add_argument("--starting-equity-usd", type=float, default=28.0)
    parser.add_argument("--strategies", default=",".join(STRATEGIES.keys()))
    args = parser.parse_args()

    settings = load_settings()

    if args.coins.strip().lower() == "auto":
        # ดึงพูล liquid จริงตามเกณฑ์เดียวกับ production (min_24h_volume_usd/min_open_interest_usd จาก
        # risk.yaml) — ต่างจากค่า default ["BTC","PAXG"] ที่เป็นเพียงความง่ายของสคริปต์ทดสอบเท่านั้น
        coins = get_current_liquid_universe_coins(
            settings.risk.mode_defaults.min_24h_volume_usd,
            settings.risk.mode_defaults.min_open_interest_usd,
            settings.risk.mode_defaults.always_include,
        )
        print(f"โหมด auto: พบเหรียญ liquid พอ {len(coins)} ตัวจาก Hyperliquid ตอนนี้: {coins}")
    else:
        coins = [c.strip() for c in args.coins.split(",") if c.strip()]

    strategy_names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in strategy_names if s not in STRATEGIES]
    if unknown:
        print(f"ไม่รู้จักกลยุทธ์: {unknown} — เลือกได้จาก {list(STRATEGIES.keys())}")
        return 1

    mode_defaults = settings.risk.mode_defaults.model_copy(update={"min_24h_volume_usd": 0.0, "min_open_interest_usd": 0.0})
    risk_for_backtest = settings.risk.model_copy(update={"mode_defaults": mode_defaults})
    settings = settings.model_copy(update={"risk": risk_for_backtest})

    lookback_needed = args.days + WARMUP_DAYS
    print(f"กำลังดึงข้อมูลราคา/funding ย้อนหลัง {lookback_needed} วันสำหรับ {coins} (ฟรี ไม่เสีย AI)...")
    candles_by_coin, funding_by_coin = fetch_backtest_history(coins, lookback_needed)
    hist_client = HistoricalHyperliquidClient(candles_by_coin, funding_by_coin)

    import datetime as dt

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)

    print(f"เริ่มเทียบ {len(strategy_names)} กลยุทธ์ บนช่วง {start.isoformat()} ถึง {end.isoformat()}\n")

    results = {}
    out_dir = REPO_ROOT / "state" / "rule_backtest"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in strategy_names:
        print(f"--- {name} ---")
        summary = run_rule_backtest(
            settings=settings, hist_client=hist_client, coins=coins,
            start_date=start.isoformat(), end_date=end.isoformat(),
            starting_equity_usd=args.starting_equity_usd, decide_fn=STRATEGIES[name],
        )
        results[name] = summary
        save_json(out_dir / f"{name}_trades.json", summary["trade_list"])
        print(f"  trades={summary['trades']} win_rate={summary['win_rate_pct']}% pnl={summary['total_pnl_usd']} final_eq={summary['final_equity_usd']}")

    print_comparison_table(results)

    summary_for_save = {name: {k: v for k, v in r.items() if k != "trade_list"} for name, r in results.items()}
    save_json(out_dir / "comparison_summary.json", summary_for_save)
    print(f"\nเขียนสรุปผลลง {out_dir / 'comparison_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

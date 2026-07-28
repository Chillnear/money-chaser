"""
Backtest — เดินเครื่อง run_daily_pipeline จริงย้อนอดีตหลายวันติดกันแบบเข้มข้น (เรียก AI จริงทุกวันที่
จำลอง) เพื่อให้ได้ข้อมูลผลงานเยอะพอจะประเมินกลยุทธ์ได้ โดยไม่ต้องรอ cron รายวันจริงหลายเดือน (P5.7 —
ตามที่ผู้ใช้ขอ: "อยากให้ได้ข้อมูลเพียงพอเร็วๆ ยอมเสียค่า AI เพิ่ม")

ใช้ src.main.run_daily_pipeline() **ตัวเดียวกันเป๊ะๆ** กับที่ daily.yml เรียกทุกเช้าจริง (ไม่มี logic
การเทรดชุดที่สองมาซ้อน กันไม่ให้ backtest วัดพฤติกรรมที่ไม่ตรงกับของจริง) — สร้าง
HistoricalHyperliquidClient ที่ "ตัด" ให้เห็นแค่ราคา/funding ที่มีอยู่จริง ณ วันที่กำลังจำลองเท่านั้น
(กัน lookahead bias) แล้ววนเรียก pipeline วันละ 1 ครั้งด้วย journal_dir แยกต่างหากจาก state/ ของจริง

ข้อจำกัดที่ต้องรู้ (fail-safe โดยตั้งใจ ไม่ใช่บั๊ก):
  - Hyperliquid ไม่มี endpoint OI ย้อนหลัง -> combination read (P5.3) จะเริ่มจากไม่มีข้อมูลเหมือนวันแรกๆ
    ของระบบจริง แล้วค่อยมีข้อมูลมากขึ้นเรื่อยๆ ตาม backtest สะสมวันไปเรื่อยๆ (เหมือนของจริงตอนเริ่มระบบ)
  - ไม่มีปฏิทินข่าวมหภาคย้อนหลังฟรี (ForexFactory feed ให้แค่สัปดาห์ปัจจุบัน) -> macro event veto (P5.2)
    ปิดในโหมด backtest เสมอ
  - min_24h_volume_usd / min_open_interest_usd ของ screening ถูก override เป็น 0 เพราะไม่มีข้อมูล
    ย้อนหลังฟรีสำหรับสองค่านี้ — ราคา/funding/breaker/sizing/gates อื่นๆทำงานเหมือนของจริงทุกอย่าง
  - มี max_ai_cost_usd กันเผลอรันจนเสียเงินเกินตั้งใจ — เช็คยอดใช้จริงสะสมทุกวันที่จำลอง หยุดทันทีถ้าเกิน
    แล้วรายงานผลบางส่วนที่ทำได้พร้อมบอกเหตุผลที่หยุด
  - journal_dir ของ backtest จะถูกล้างใหม่ทุกครั้งที่รัน (ไม่ทับ/ไม่ปนกับ state/ ของจริงที่ live ใช้เด็ดขาด)

รันบน GitHub Actions เท่านั้น (ต้องมี network จริง) — ดู .github/workflows/backtest.yml
"""
from __future__ import annotations

import datetime as dt
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agents.llm import LLMClient  # noqa: E402
from src.agents.registry import load_model_registry  # noqa: E402
from src.data.hl_market import HyperliquidClient  # noqa: E402
from src.execution.broker_paper import PaperBroker  # noqa: E402
from src.main import run_daily_pipeline  # noqa: E402
from src.settings import CONFIG_DIR, load_settings  # noqa: E402
from src.util.io import load_jsonl, save_json  # noqa: E402

DEFAULT_COINS = ["BTC", "PAXG"]
INDICATOR_WARMUP_DAYS = 60  # ต้องมีแท่งเทียนก่อนวันแรกที่จำลองพอคำนวณ EMA200/ADX/ATR ฯลฯ (build_price_features ต้องการ >= 30)


class HistoricalHyperliquidClient:
    """แทน HyperliquidClient จริงตอน backtest — ให้ get_candles()/get_universe_snapshot() คืนแค่ข้อมูล
    ที่ "มีอยู่จริง" ณ as_of_ms ที่ตั้งไว้ล่าสุดเท่านั้น (เรียก set_as_of() ก่อนทุกวันที่จำลอง) กัน lookahead
    bias — ไม่งั้น backtest จะดูเก่งเกินจริงเพราะ agent เห็นอนาคตที่ยังไม่เกิดขึ้น
    """

    def __init__(self, candles_by_coin: dict[str, list[dict]], funding_by_coin: dict[str, list[dict]]):
        self._candles_by_coin = candles_by_coin
        self._funding_by_coin = funding_by_coin
        self.as_of_ms: int | None = None

    def set_as_of(self, as_of_ms: int) -> None:
        self.as_of_ms = as_of_ms

    def _visible_candles(self, coin: str) -> list[dict]:
        if self.as_of_ms is None:
            raise ValueError("ต้องเรียก set_as_of() ก่อนใช้งาน HistoricalHyperliquidClient")
        candles = self._candles_by_coin.get(coin, [])
        # ใช้ "T" (เวลาปิดแท่ง) <= as_of_ms — แท่งที่ยังไม่ปิดจริง ณ วันที่จำลอง ต้องไม่เห็น
        return [c for c in candles if c.get("T", 0) <= self.as_of_ms]

    def get_candles(self, coin: str, interval: str = "1d", lookback_days: int = 400) -> list[dict]:
        visible = self._visible_candles(coin)
        return visible[-lookback_days:] if lookback_days else visible

    def _latest_funding_rate(self, coin: str) -> float:
        history = self._funding_by_coin.get(coin, [])
        visible = [f for f in history if int(f.get("time", f.get("coinFundingTime", 0)) or 0) <= self.as_of_ms]
        if not visible:
            return 0.0
        return float(visible[-1].get("fundingRate", 0.0))

    def get_universe_snapshot(self) -> list[dict]:
        snapshot = []
        for coin in self._candles_by_coin:
            visible = self._visible_candles(coin)
            if len(visible) < 2:
                continue  # ยังไม่มีข้อมูลพอ (เช่นวันแรกๆของ backtest ก่อนพ้น warm-up)
            last, prev = visible[-1], visible[-2]
            last_close = float(last["c"])
            snapshot.append(
                {
                    "coin": coin,
                    "funding": self._latest_funding_rate(coin),
                    "open_interest_usd": 0.0,  # ไม่มีข้อมูลย้อนหลังฟรี — ดู docstring บนสุดของไฟล์
                    "day_volume_usd": float(last.get("v", 0.0)) * last_close,
                    "mark_px": last_close,
                    "prev_day_px": float(prev["c"]),
                }
            )
        return snapshot


def fetch_backtest_history(coins: list[str], lookback_days: int) -> tuple[dict, dict]:
    """ดึงข้อมูลจริงจาก Hyperliquid ครั้งเดียวก่อนเริ่ม backtest — เป็น network call ฟรีเหมือน live
    pipeline ใช้ทุกวัน ไม่ใช่ AI cost (AI cost เกิดตอนวน run_daily_pipeline เท่านั้น)
    """
    client = HyperliquidClient()
    candles_by_coin: dict[str, list[dict]] = {}
    funding_by_coin: dict[str, list[dict]] = {}
    for coin in coins:
        candles_by_coin[coin] = client.get_candles(coin, interval="1d", lookback_days=lookback_days)
        funding_by_coin[coin] = client.get_funding_history(coin, lookback_days=lookback_days)
    return candles_by_coin, funding_by_coin


def date_to_ms(date_str: str) -> int:
    return int(dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def date_range(start_date: str, end_date: str) -> list[str]:
    start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    end = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        return []
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return days


def run_backtest(
    settings,
    llm_client: LLMClient,
    model_registry: dict,
    hist_client: HistoricalHyperliquidClient,
    start_date: str,
    end_date: str,
    backtest_journal_dir: Path,
    starting_equity_usd: float = 28.0,
    max_ai_cost_usd: float | None = None,
) -> dict:
    """วน run_daily_pipeline วันละครั้งตามช่วงวันที่กำหนด ใช้ broker/journal_dir เดียวกันตลอดรอบ (equity
    สะสมข้ามวันเหมือนของจริง) — ล้าง journal เก่าก่อนเริ่มเสมอ (backtest ต้องเริ่มจากศูนย์ทุกครั้ง ไม่ใช่
    สะสมทับจากรอบก่อน) คืน summary dict พร้อมเหตุผลถ้าหยุดก่อนกำหนด (เช่น ชนเพดานค่าใช้จ่าย AI)
    """
    if backtest_journal_dir.exists():
        shutil.rmtree(backtest_journal_dir)
    backtest_journal_dir.mkdir(parents=True, exist_ok=True)
    kill_path = backtest_journal_dir / "KILL"
    last_run_path = backtest_journal_dir / "last_run.json"

    broker = PaperBroker(
        starting_equity_usd=starting_equity_usd,
        taker_fee_pct=settings.risk.costs.taker_fee_pct,
        slippage_pct=settings.risk.costs.assumed_slippage_pct,
    )

    days_simulated = 0
    stopped_early_reason: str | None = None

    for date_str in date_range(start_date, end_date):
        llm_cost_records = load_jsonl(backtest_journal_dir / "llm_cost.jsonl")

        if max_ai_cost_usd is not None:
            spent_so_far = sum(r.get("cost_usd", 0.0) for r in llm_cost_records)
            if spent_so_far >= max_ai_cost_usd:
                stopped_early_reason = (
                    f"หยุดก่อนกำหนดที่ {date_str}: ใช้ AI ไปแล้ว {spent_so_far:.4f} USD "
                    f"ถึงเพดานที่ตั้งไว้ {max_ai_cost_usd:.4f} USD"
                )
                break

        hist_client.set_as_of(date_to_ms(date_str))
        now_ts = date_to_ms(date_str) / 1000.0 + 3600  # เวลาสมมติของวันนั้น (ใช้เทียบ holding period/pause)

        run_daily_pipeline(
            settings=settings,
            hl_client=hist_client,
            llm_client=llm_client,
            broker=broker,
            model_registry=model_registry,
            today_date=date_str,
            now_ts=now_ts,
            journal_dir=backtest_journal_dir,
            kill_path=kill_path,
            last_run_path=last_run_path,
            starting_equity_usd=starting_equity_usd,
            llm_cost_records=llm_cost_records,
        )
        days_simulated += 1

        if kill_path.exists():
            stopped_early_reason = f"หยุดก่อนกำหนดที่ {date_str}: breaker สั่ง KILL (max drawdown ถูกชนใน backtest)"
            break

    trades = load_jsonl(backtest_journal_dir / "trades.jsonl")
    llm_cost_records = load_jsonl(backtest_journal_dir / "llm_cost.jsonl")
    total_ai_cost = sum(r.get("cost_usd", 0.0) for r in llm_cost_records)
    final_equity = broker.get_account_equity()
    wins = [t for t in trades if (t.get("pnl_usd") or 0.0) > 0]

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days_simulated": days_simulated,
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "starting_equity_usd": starting_equity_usd,
        "final_equity_usd": round(final_equity, 2),
        "total_pnl_usd": round(final_equity - starting_equity_usd, 2),
        "total_ai_cost_usd": round(total_ai_cost, 4),
        "stopped_early_reason": stopped_early_reason,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="รัน backtest ย้อนอดีตด้วย AI จริง (เสียค่า AI จริงตามจำนวนวัน)")
    parser.add_argument("--coins", default=",".join(DEFAULT_COINS))
    parser.add_argument("--days", type=int, default=60, help="จำลองกี่วันย้อนหลังจากเมื่อวาน")
    parser.add_argument("--max-ai-cost-usd", type=float, default=3.0)
    parser.add_argument("--starting-equity-usd", type=float, default=28.0)
    args = parser.parse_args()

    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    settings = load_settings()

    # ปิด filter volume/OI ของ screening เพราะไม่มีข้อมูลย้อนหลังฟรีสองค่านี้ (ดู docstring บนสุดของไฟล์)
    mode_defaults = settings.risk.mode_defaults.model_copy(
        update={"min_24h_volume_usd": 0.0, "min_open_interest_usd": 0.0}
    )
    risk_for_backtest = settings.risk.model_copy(update={"mode_defaults": mode_defaults})
    settings = settings.model_copy(update={"risk": risk_for_backtest})

    model_registry = load_model_registry(CONFIG_DIR / "models.yaml")
    llm_client = LLMClient(
        base_url=settings.secrets.litellm_base_url,
        api_keys=[k for k in [settings.secrets.litellm_key_1, settings.secrets.litellm_key_2] if k],
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
    )

    lookback_needed = args.days + INDICATOR_WARMUP_DAYS
    print(f"กำลังดึงข้อมูลราคา/funding ย้อนหลัง {lookback_needed} วันสำหรับ {coins} ...")
    candles_by_coin, funding_by_coin = fetch_backtest_history(coins, lookback_needed)
    hist_client = HistoricalHyperliquidClient(candles_by_coin, funding_by_coin)

    end = dt.date.today() - dt.timedelta(days=1)  # เมื่อวาน (วันนี้แท่งเทียนยังไม่ปิด)
    start = end - dt.timedelta(days=args.days - 1)

    backtest_journal_dir = REPO_ROOT / "state" / "backtest" / "journal"
    print(f"เริ่มจำลอง {args.days} วัน ({start.isoformat()} ถึง {end.isoformat()}) เพดานค่าใช้จ่าย AI = ${args.max_ai_cost_usd:.2f}\n")

    summary = run_backtest(
        settings=settings,
        llm_client=llm_client,
        model_registry=model_registry,
        hist_client=hist_client,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        backtest_journal_dir=backtest_journal_dir,
        starting_equity_usd=args.starting_equity_usd,
        max_ai_cost_usd=args.max_ai_cost_usd,
    )

    print("=== สรุปผล Backtest ===")
    for key, value in summary.items():
        print(f"{key}: {value}")

    save_json(REPO_ROOT / "state" / "backtest" / "summary.json", summary)
    print(f"\nเขียนสรุปผลลง state/backtest/summary.json (รายละเอียดเต็มอยู่ใน {backtest_journal_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

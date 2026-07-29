"""
เทียบพารามิเตอร์ risk/exit หลายแบบ (reward:risk ratio, max_holding_days, atr_multiple/stop_cap) กับ
ช่วงเวลาย้อนอดีต**เดียวกัน**เป๊ะๆ — ตอบคำถามที่เจอจาก backtest 180 วันแรก (P5.8): ไม่มีไม้ไหนโดน
take-profit เลย เพราะเป้ากำไร (stop x reward_risk_ratio) ไกลกว่าที่ราคาขยับได้จริงในเวลาที่ถือ

จุดสำคัญที่ทำให้เทียบได้แบบ "ยุติธรรม" และ**ประหยัดเงิน**: stop/take-profit/max_holding_days เป็น
พารามิเตอร์ของ risk engine ล้วนๆ (src/risk/sizing.py, src/risk/exit_rules.py) — **ไม่มีค่าไหนถูกส่งเข้า
prompt ของ analyst/redteam/judge เลย** (ดู src/data/features.py render_feature_table) ดังนั้น AI จะเห็น
prompt เดียวกันทุกตัวอักษรไม่ว่าจะทดสอบพารามิเตอร์ไหน — CachedCompletionFn ด้านล่างจึง cache คำตอบจริง
ของ AI ไว้ (คีย์ด้วย model+messages) แล้ว "เล่นซ้ำ" คำตอบเดิมให้ทุก variant ถัดไปได้ฟรี ไม่ต้องยิง AI ใหม่
เลย (variant แรกเท่านั้นที่เสียเงินจริง ตัวถัดไปเกือบ 0 บาท) — เป็นการทดลอง "ถ้าใช้พารามิเตอร์ risk ต่างกัน
กับการตัดสินใจของ AI ชุดเดียวกัน" ซึ่งตรงกับสิ่งที่อยากรู้จริงๆ (ผลจากพารามิเตอร์ ไม่ใช่ความสุ่มของ AI)

รันบน GitHub Actions เท่านั้น (ต้องมี network จริง) — ดู .github/workflows/backtest_compare.yml
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))  # ให้ `import backtest` (scripts/backtest.py) resolve ได้ตรงๆ

from src.agents.llm import LLMClient  # noqa: E402
from src.agents.registry import load_model_registry  # noqa: E402
from src.settings import CONFIG_DIR, load_settings  # noqa: E402
from src.util.io import load_json, save_json  # noqa: E402

from backtest import (  # noqa: E402
    DEFAULT_COINS,
    INDICATOR_WARMUP_DAYS,
    HistoricalHyperliquidClient,
    fetch_backtest_history,
    run_backtest,
)

# แต่ละ variant คือ override เฉพาะฟิลด์ของ StopsConfig (risk.yaml -> stops:) ที่ต่างจาก baseline
# ตั้งชื่อให้อ่านง่ายว่ากำลังทดสอบสมมติฐานอะไร (ดู docstring บนสุดของไฟล์ ที่มาของสมมติฐานเหล่านี้)
VARIANTS: dict[str, dict] = {
    "baseline_ปกติ": {},
    "rr_1.5_เป้าใกล้ขึ้น": {"reward_risk_ratio": 1.5},
    "rr_1.0_เป้าเท่า_stop": {"reward_risk_ratio": 1.0},
    "ถือยาวขึ้น_8วัน": {"max_holding_days": 8},
    "stop_แน่นขึ้น_atr1.0_cap4pct": {"atr_multiple": 1.0, "stop_cap_pct": 4.0},
}


class CachedCompletionFn:
    """ครอบ completion_fn จริงด้วย cache ลงดิสก์ คีย์ด้วย (model, messages) — cache hit คืนคำตอบเดิมทันที
    ไม่ยิง network ใหม่เลย ทำให้เทียบหลาย variant ของพารามิเตอร์ risk (ที่ไม่เคยอยู่ใน prompt) แทบไม่เสีย
    เงินเพิ่มหลังจากรอบแรก — ดู docstring บนสุดของไฟล์
    """

    def __init__(self, real_completion_fn, cache_path: Path):
        self.real_completion_fn = real_completion_fn
        self.cache_path = cache_path
        self.cache: dict[str, str] = load_json(cache_path, default={})
        self.hits = 0
        self.misses = 0

    def _key(self, model, messages) -> str:
        raw = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def __call__(self, **kwargs):
        key = self._key(kwargs.get("model"), kwargs.get("messages"))
        if key in self.cache:
            self.hits += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.cache[key]))],
                _from_cache=True,
            )
        self.misses += 1
        response = self.real_completion_fn(**kwargs)
        self.cache[key] = response.choices[0].message.content
        save_json(self.cache_path, self.cache)
        return response


def make_cached_cost_fn(real_cost_fn):
    """cache hit = ไม่ได้เรียก API จริง = ต้นทุนเพิ่ม 0.00 USD จริงๆ (ไม่ใช่การปัดตัวเลข)"""

    def _cost_fn(completion_response):
        if getattr(completion_response, "_from_cache", False):
            return 0.0
        return real_cost_fn(completion_response=completion_response)

    return _cost_fn


def apply_stops_override(settings, overrides: dict):
    stops = settings.risk.stops.model_copy(update=overrides)
    risk = settings.risk.model_copy(update={"stops": stops})
    return settings.model_copy(update={"risk": risk})


def run_comparison(
    base_settings,
    cached_llm_client: LLMClient,
    model_registry: dict,
    hist_client: HistoricalHyperliquidClient,
    start_date: str,
    end_date: str,
    starting_equity_usd: float,
    compare_root_dir: Path,
    variants: dict[str, dict] = VARIANTS,
    max_ai_cost_usd: float | None = None,
) -> dict:
    results = {}
    for name, overrides in variants.items():
        variant_settings = apply_stops_override(base_settings, overrides)
        journal_dir = compare_root_dir / name
        print(f"\n--- variant: {name} (override={overrides}) ---")
        summary = run_backtest(
            settings=variant_settings,
            llm_client=cached_llm_client,
            model_registry=model_registry,
            hist_client=hist_client,
            start_date=start_date,
            end_date=end_date,
            backtest_journal_dir=journal_dir,
            starting_equity_usd=starting_equity_usd,
            max_ai_cost_usd=max_ai_cost_usd,
        )
        summary["stops_override"] = overrides
        results[name] = summary
        print(f"  trades={summary['trades']} win_rate={summary['win_rate_pct']}% "
              f"pnl={summary['total_pnl_usd']} ai_cost={summary['total_ai_cost_usd']}")

    return results


def print_comparison_table(results: dict) -> None:
    print("\n=== เทียบผลแต่ละ variant (ช่วงเวลาเดียวกันเป๊ะๆ) ===")
    header = f"{'variant':<30} {'trades':<8} {'win%':<8} {'pnl(USD)':<10} {'final_eq':<10} {'ai_cost':<10}"
    print(header)
    print("-" * len(header))
    for name, r in sorted(results.items(), key=lambda kv: -kv[1]["total_pnl_usd"]):
        print(
            f"{name:<30} {r['trades']:<8} {r['win_rate_pct']:<8} {r['total_pnl_usd']:<10} "
            f"{r['final_equity_usd']:<10} {r['total_ai_cost_usd']:<10}"
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="เทียบพารามิเตอร์ risk/exit หลายแบบกับช่วงเวลาย้อนอดีตเดียวกัน")
    parser.add_argument("--coins", default=",".join(DEFAULT_COINS))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--max-ai-cost-usd", type=float, default=5.0, help="เพดานนี้ใช้ตรวจแค่ variant แรก (ตัวถัดไปแทบไม่เสียเงินเพิ่ม)")
    parser.add_argument("--starting-equity-usd", type=float, default=28.0)
    args = parser.parse_args()

    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    settings = load_settings()
    mode_defaults = settings.risk.mode_defaults.model_copy(
        update={"min_24h_volume_usd": 0.0, "min_open_interest_usd": 0.0}
    )
    risk_for_backtest = settings.risk.model_copy(update={"mode_defaults": mode_defaults})
    settings = settings.model_copy(update={"risk": risk_for_backtest})

    model_registry = load_model_registry(CONFIG_DIR / "models.yaml")

    compare_root_dir = REPO_ROOT / "state" / "backtest_compare"
    compare_root_dir.mkdir(parents=True, exist_ok=True)
    cache_path = compare_root_dir / "llm_response_cache.json"

    base_llm_client = LLMClient(
        base_url=settings.secrets.litellm_base_url,
        api_keys=[k for k in [settings.secrets.litellm_key_1, settings.secrets.litellm_key_2] if k],
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
    )
    cached_completion = CachedCompletionFn(base_llm_client._get_completion_fn(), cache_path)
    cached_cost = make_cached_cost_fn(base_llm_client._get_cost_fn())
    cached_llm_client = LLMClient(
        base_url=settings.secrets.litellm_base_url,
        api_keys=[k for k in [settings.secrets.litellm_key_1, settings.secrets.litellm_key_2] if k],
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
        completion_fn=cached_completion,
        cost_fn=cached_cost,
    )

    import datetime as dt

    lookback_needed = args.days + INDICATOR_WARMUP_DAYS
    print(f"กำลังดึงข้อมูลราคา/funding ย้อนหลัง {lookback_needed} วันสำหรับ {coins} ...")
    candles_by_coin, funding_by_coin = fetch_backtest_history(coins, lookback_needed)
    hist_client = HistoricalHyperliquidClient(candles_by_coin, funding_by_coin)

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)

    print(f"เริ่มเทียบ {len(VARIANTS)} variants บนช่วง {start.isoformat()} ถึง {end.isoformat()}\n")

    results = run_comparison(
        base_settings=settings,
        cached_llm_client=cached_llm_client,
        model_registry=model_registry,
        hist_client=hist_client,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        starting_equity_usd=args.starting_equity_usd,
        compare_root_dir=compare_root_dir,
        max_ai_cost_usd=args.max_ai_cost_usd,
    )

    print_comparison_table(results)
    print(f"\ncache: hit={cached_completion.hits} miss={cached_completion.misses} "
          f"(hit สูง = variant หลังๆแทบไม่เสียเงินเพิ่มจริงตามที่ตั้งใจ)")

    save_json(compare_root_dir / "comparison_summary.json", results)
    print(f"เขียนสรุปเทียบผลลง {compare_root_dir / 'comparison_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Head-to-head bake-off — ยิง prompt จริงของระบบใส่โมเดลที่เข้าชิงหลายตัว ด้วยข้อมูลตลาดชุดเดียวกัน
แล้วให้คะแนนว่าใครเหมาะกับ "งานของเรา" จริงๆ

ทำไมต้องมีไฟล์นี้: ลีดเดอร์บอร์ดข้างนอกวัด "คุยเก่งทั่วไป" ซึ่งไม่ตรงกับสิ่งที่เราต้องการจริง คือ
  1. ตอบเป็น JSON ตรง schema ได้ทุกครั้ง (ถ้าพลาด = agent นั้น abstain = เสียเปล่าทั้ง call)
  2. หาข้อค้านได้คมจริง / ให้เหตุผลตัดสินใจได้ดี
  3. ราคาต่อ call อยู่ในงบ
ไฟล์นี้วัด 3 ข้อนั้นตรงๆ จากงานจริง ไม่ใช่จากคะแนนสอบของคนอื่น

รันบน GitHub Actions เท่านั้น (ต้องมี network จริง) — ดู .github/workflows/bakeoff.yml
ผลลัพธ์เขียนลง state/bakeoff.json + พิมพ์ตารางสรุปให้อ่านใน log
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agents.llm import LLMClient  # noqa: E402
from src.agents.prompt_builder import render_prompt  # noqa: E402
from src.agents.schemas import AnalystOutput, JudgeOutput  # noqa: E402
from src.settings import STATE_DIR, load_settings  # noqa: E402

# ผู้เข้าชิงแต่ละตำแหน่ง — แก้รายชื่อตรงนี้เวลาอยากเทียบตัวใหม่
#
# หมายเหตุ 2026-07-28: "Grok 4.1 Fast Reasoning" ผ่านบาก-ออฟรอบก่อน 100% แต่พอเอาไปใช้จริงตอน backtest
# กลับเจอ error "team not allowed to access model" — แปลว่าผ่านบาก-ออฟไม่ได้แปลว่าใช้งานได้จริงเสมอไป ถ้า
# สิทธิ์บัญชี LiteLLM เปลี่ยน ตัดออกจากลิสต์แล้ว แทนด้วยตัวที่ยืนยันจาก error message ว่าทีมนี้เข้าถึงได้จริง
CANDIDATES = {
    "redteam": [
        "GPT 5.2",
        "Claude Sonnet 4.6",
        "Claude Haiku 4.5",
        "Gemini 3.0 pro",
        "glm-5.2",
    ],
    "judge": [
        "claude-opus-4-7",
        "Claude Opus 4.5",
        "gpt-5.5",
        "GPT 5.2",
        "Claude Sonnet 4.6",
    ],
}

# ข้อมูลตลาดจำลองที่ "มีกับดัก" ตั้งใจให้ยาก: เทรนด์ขึ้นแรงน่าเข้า long แต่ funding สูงผิดปกติ
# และ OI พุ่ง = สัญญาณ squeeze risk ที่โมเดลเก่งควรจับได้และเตือน ส่วนโมเดลอ่อนจะเชียร์ long อย่างเดียว
SAMPLE_FEATURE_TABLE = """
**มหภาค:** DXY=99.20 (-0.35%) | XAUUSD=4180.50 (+0.80%) | SPX=6120.30 (-0.15%)
**Fear & Greed:** 78 (Extreme Greed), เปลี่ยนจาก 7 วันก่อน: +22

## ผู้เข้าชิงที่ต้องวิเคราะห์ลึก

### BTC (composite score: 0.812)
- ราคาล่าสุด: 96500.00, regime: trend_up_vol_high
- EMA gap%: 9=2.10, 21=5.40, 50=11.80
- ADX: 38.50, ATR%: 4.20, RSI: 78.30
- Return 1d/7d/30d: 4.80% / 18.20% / 41.50%
- ระยะจากจุดสูงสุด 30 วัน: -0.30% | จุดต่ำสุด 30 วัน: +43.10%
- funding ปัจจุบัน: 0.0180% ต่อ 8 ชม. (annualized ~19.7%), เฉลี่ย 7 วัน: 0.0165%
- Open interest: 2.10e9 USD (เปลี่ยน 24 ชม.: +14.2%)

### PAXG (composite score: 0.402)
- ราคาล่าสุด: 4185.00, regime: trend_up_vol_low
- EMA gap%: 9=0.30, 21=0.85, 50=1.60
- ADX: 21.00, ATR%: 0.90, RSI: 58.10
- Return 1d/7d/30d: 0.75% / 2.10% / 5.40%
- funding ปัจจุบัน: 0.0010% ต่อ 8 ชม., Open interest: 4.2e7 USD (เปลี่ยน 24 ชม.: +0.8%)

### ETH (composite score: 0.388)
- ราคาล่าสุด: 3120.00, regime: chop_vol_mid
- ADX: 18.20, ATR%: 3.10, RSI: 52.00
- Return 1d/7d/30d: 0.40% / 1.80% / 6.20%
- funding ปัจจุบัน: 0.0035% ต่อ 8 ชม., Open interest: 8.9e8 USD (เปลี่ยน 24 ชม.: +1.1%)
""".strip()

SAMPLE_ANALYST_OUTPUTS = """
### analyst_trend (model: qwen3.7-max, provider: alibaba)
- **BTC**: long (confidence 82) — ADX 38.5 ยืนยันเทรนด์แข็งแรง ราคาเหนือ EMA ทุกเส้น breakout ต่อเนื่อง [invalidation: หลุด EMA21]
- **PAXG**: flat (confidence 40) — เทรนด์อ่อน ADX ต่ำกว่า 25 [invalidation: -]
- **ETH**: flat (confidence 35) — chop ไม่มีทิศทาง [invalidation: -]

### analyst_positioning (model: claude-sonnet-5, provider: anthropic)
- **BTC**: flat (confidence 55) — RSI 78 overbought มาก funding annualized ~20% แพงผิดปกติ OI +14% ใน 24 ชม. [invalidation: funding กลับสู่ปกติ]
- **PAXG**: long (confidence 50) — positioning สะอาด funding แทบเป็นศูนย์ [invalidation: -]
- **ETH**: flat (confidence 45) — ไม่มีสัญญาณสุดโต่ง [invalidation: -]

### analyst_macro (model: deepseek-v4-pro, provider: deepseek)
- **BTC**: long (confidence 68) — DXY อ่อน ทองขึ้น risk-on ชัดเจน [invalidation: DXY กลับมาแข็ง]
- **PAXG**: long (confidence 62) — ทองสปอตขึ้น 0.8% DXY อ่อน [invalidation: -]
- **ETH**: flat (confidence 40) — ไม่มีตัวเร่งเฉพาะตัว [invalidation: -]
""".strip()

USER_PROMPT_REDTEAM = "กรุณาหาเหตุผลค้าน consensus ของ analysts ข้างต้นให้แรงที่สุด แล้วตอบเป็น JSON ตาม schema เท่านั้น"
USER_PROMPT_JUDGE = "กรุณาตัดสินใจแล้วตอบเป็น JSON ตาม schema เท่านั้น ห้ามเลือก asset นอกรายชื่อที่อนุญาตเด็ดขาด"

ALLOWED_ASSETS = ["BTC", "PAXG", "ETH"]
RUNS_PER_MODEL = 2  # ยิงซ้ำเพื่อดูความเสถียร (ตอบตรง schema ทุกครั้งไหม) ไม่ใช่ฟลุกครั้งเดียว


def build_redteam_prompt() -> str:
    return render_prompt(
        "redteam",
        feature_table=SAMPLE_FEATURE_TABLE,
        lessons="(ยังไม่มี lesson)",
        analyst_outputs=SAMPLE_ANALYST_OUTPUTS,
    )


def build_judge_prompt() -> str:
    return render_prompt(
        "judge",
        feature_table=SAMPLE_FEATURE_TABLE,
        lessons="(ยังไม่มี lesson)",
        allowed_assets=", ".join(ALLOWED_ASSETS),
        analyst_hit_rate_table="(ยังไม่มีข้อมูล hit rate พอ)",
        analyst_outputs=SAMPLE_ANALYST_OUTPUTS,
        redteam_output="### redteam\n- **BTC**: flat (confidence 60) — funding แพงและ OI พุ่งพร้อมกัน เสี่ยง long squeeze",
    )


def score_redteam_output(output: AnalystOutput) -> dict:
    """ให้คะแนนเชิงวัตถุวิสัยเท่าที่วัดด้วยโค้ดได้ (ไม่ตัดสินคุณภาพภาษาเอง — คนอ่านเองจาก log)

    เกณฑ์ที่วัดได้:
      - ครอบคลุมผู้เข้าชิงครบทุกตัวไหม
      - จับ "กับดัก" ของชุดข้อมูลนี้ได้ไหม: BTC funding แพง + OI พุ่ง + RSI 78 = ไม่ควรเชียร์ long
        โมเดลที่ตอบ BTC=long ด้วย confidence สูง = พลาดกับดัก
      - ระบุ invalidation ครบทุกตัวไหม (ถ้าไม่ระบุ = คิดไม่ครบ)
    """
    assets = {c.asset for c in output.candidates}
    coverage = len(assets & set(ALLOWED_ASSETS)) / len(ALLOWED_ASSETS)

    btc = next((c for c in output.candidates if c.asset == "BTC"), None)
    caught_trap = bool(btc and (btc.direction != "long" or btc.confidence < 50))

    with_invalidation = sum(1 for c in output.candidates if c.invalidation.strip())
    invalidation_rate = with_invalidation / max(len(output.candidates), 1)

    avg_thesis_len = sum(len(c.thesis) for c in output.candidates) / max(len(output.candidates), 1)

    return {
        "coverage": round(coverage, 2),
        "caught_funding_trap": caught_trap,
        "invalidation_rate": round(invalidation_rate, 2),
        "avg_thesis_chars": round(avg_thesis_len),
        "btc_call": f"{btc.direction}@{btc.confidence:.0f}" if btc else "ไม่ได้พูดถึง BTC",
    }


def score_judge_output(output: JudgeOutput) -> dict:
    return {
        "action": output.action,
        "asset": output.asset,
        "asset_in_allowed_list": output.asset is None or output.asset in ALLOWED_ASSETS,
        "confidence": output.confidence,
        "answered_redteam": bool(output.redteam_response.strip()),
        "explained_why_this_over_others": bool(output.why_this_over_others.strip()),
        "reasoning_chars": len(output.reasoning),
    }


def run_role_bakeoff(llm_client: LLMClient, role: str, models: list[str], system_prompt: str, user_prompt: str) -> list[dict]:
    schema = AnalystOutput if role == "redteam" else JudgeOutput
    scorer = score_redteam_output if role == "redteam" else score_judge_output
    results = []

    for model in models:
        runs = []
        for attempt in range(RUNS_PER_MODEL):
            start = time.time()
            result = llm_client.call_structured(model, system_prompt, user_prompt, schema)
            elapsed_ms = (time.time() - start) * 1000

            run = {
                "valid_json": not result.abstained and result.parsed is not None,
                "cost_usd": result.cost_usd,
                "latency_ms": round(elapsed_ms),
                "attempts_needed": result.attempts,
                "error": result.error,
            }
            if result.parsed is not None:
                run["score"] = scorer(result.parsed)
                run["raw_preview"] = (result.raw_text or "")[:400]
            runs.append(run)

        valid_count = sum(1 for r in runs if r["valid_json"])
        results.append(
            {
                "model": model,
                "schema_success_rate": valid_count / RUNS_PER_MODEL,
                "avg_cost_usd": round(sum(r["cost_usd"] for r in runs) / RUNS_PER_MODEL, 5),
                "avg_latency_ms": round(sum(r["latency_ms"] for r in runs) / RUNS_PER_MODEL),
                "runs": runs,
            }
        )
        print(f"  [{role}] {model}: schema ผ่าน {valid_count}/{RUNS_PER_MODEL}, "
              f"เฉลี่ย ${results[-1]['avg_cost_usd']:.5f}, {results[-1]['avg_latency_ms']}ms")

    return results


def print_summary_table(role: str, results: list[dict]) -> None:
    print(f"\n=== สรุป {role} ===")
    header = f"{'model':<28} {'schema':<8} {'$/call':<10} {'latency':<10} {'หมายเหตุ'}"
    print(header)
    print("-" * len(header))
    for r in sorted(results, key=lambda x: (-x["schema_success_rate"], x["avg_cost_usd"])):
        note = ""
        good_runs = [run for run in r["runs"] if run.get("score")]
        if good_runs:
            score = good_runs[0]["score"]
            if role == "redteam":
                note = f"จับกับดัก={score['caught_funding_trap']} BTC={score['btc_call']}"
            else:
                note = f"{score['action']} {score['asset']} conf={score['confidence']:.0f}"
        elif r["runs"] and r["runs"][0].get("error"):
            note = f"ล้ม: {r['runs'][0]['error'][:60]}"
        print(f"{r['model']:<28} {r['schema_success_rate']:<8.0%} ${r['avg_cost_usd']:<9.5f} {r['avg_latency_ms']:<10} {note}")


def main() -> int:
    settings = load_settings()
    llm_client = LLMClient(
        base_url=settings.secrets.litellm_base_url,
        api_keys=[k for k in [settings.secrets.litellm_key_1, settings.secrets.litellm_key_2] if k],
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
    )

    print("กำลังทดสอบเทียบโมเดลด้วย prompt จริงของระบบ + ข้อมูลตลาดชุดเดียวกัน")
    print(f"ยิงซ้ำตัวละ {RUNS_PER_MODEL} ครั้งเพื่อดูความเสถียร\n")

    all_results = {}
    for role, models in CANDIDATES.items():
        system_prompt = build_redteam_prompt() if role == "redteam" else build_judge_prompt()
        user_prompt = USER_PROMPT_REDTEAM if role == "redteam" else USER_PROMPT_JUDGE
        print(f"--- {role} ({len(models)} ตัว) ---")
        all_results[role] = run_role_bakeoff(llm_client, role, models, system_prompt, user_prompt)

    for role, results in all_results.items():
        print_summary_table(role, results)

    total_cost = sum(r["avg_cost_usd"] * RUNS_PER_MODEL for results in all_results.values() for r in results)
    print(f"\nค่าใช้จ่ายรวมของการทดสอบครั้งนี้: ${total_cost:.4f}")

    out_path = STATE_DIR / "bakeoff.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"ran_at": time.time(), "runs_per_model": RUNS_PER_MODEL, "results": all_results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"เขียนผลละเอียดลง {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

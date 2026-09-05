"""
สคริปต์รายสัปดาห์เรียกจาก .github/workflows/weekly_reflect.yml (ทุกวันจันทร์) — อ่าน journal 7 วันล่าสุด
แล้วให้ reflector เขียน state/lessons.md ฉบับใหม่ (BUILD-SPEC.md §7.2)

**ไม่ push ตรง** — workflow เป็นคนเปิด PR จาก diff ของไฟล์นี้ให้มนุษย์รีวิวก่อน merge (8 สัปดาห์แรกตาม
BUILD-SPEC.md บังคับ human review; หลังจากนั้นค่อยพิจารณาผ่อนเป็น auto-merge ได้ถ้าผลงานดีต่อเนื่อง)
สคริปต์นี้แก้ได้แค่ state/lessons.md ไฟล์เดียว ไม่แตะไฟล์อื่นเลย (ขอบเขตอำนาจของ reflector)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agents.llm import LLMClient  # noqa: E402
from src.agents.reflector import run_reflector  # noqa: E402
from src.agents.registry import load_model_registry  # noqa: E402
from src.settings import CONFIG_DIR, STATE_DIR, load_settings  # noqa: E402
from src.util.io import load_jsonl  # noqa: E402


def filter_last_n_days(records: list[dict], now_ts: float, days: int = 7) -> list[dict]:
    cutoff = now_ts - days * 86400
    return [r for r in records if r.get("ts", 0) >= cutoff]


def render_journal_markdown(decision_records: list[dict]) -> str:
    if not decision_records:
        return "(ไม่มี decision บันทึกไว้ใน 7 วันที่ผ่านมา)"
    lines = []
    for r in decision_records:
        source = r.get("source", "?")
        if source == "llm":
            judge_output = r.get("judge_output") or {}
            lines.append(
                f"- {r.get('date')}: source=llm action={judge_output.get('action', '?')} "
                f"asset={judge_output.get('asset', '-')} abstained={r.get('judge_abstained')} "
                f"degrade_level={r.get('degrade_level')}"
            )
        else:
            baseline = r.get("baseline_decision") or {}
            lines.append(
                f"- {r.get('date')}: source=baseline action={baseline.get('action', '?')} "
                f"asset={baseline.get('asset', '-')} degrade_level={r.get('degrade_level')}"
            )
    return "\n".join(lines)


def render_trades_markdown(trade_records: list[dict]) -> str:
    if not trade_records:
        return "(ไม่มีไม้ที่ปิดใน 7 วันที่ผ่านมา)"
    lines = []
    for r in trade_records:
        lines.append(
            f"- {r.get('asset')} {r.get('side')} pnl={r.get('pnl_usd'):.2f} USD "
            f"เหตุผลปิด={r.get('exit_reason')} fee={r.get('fee_usd'):.2f} USD"
        )
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    model_registry = load_model_registry(CONFIG_DIR / "models.yaml")
    llm_client = LLMClient(
        base_url=settings.secrets.llm_base_url(),
        api_keys=settings.secrets.llm_api_keys(),
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
    )

    now_ts = time.time()
    journal_dir = STATE_DIR / "journal"
    decisions = filter_last_n_days(load_jsonl(journal_dir / "decisions.jsonl"), now_ts, days=7)
    trades = filter_last_n_days(load_jsonl(journal_dir / "trades.jsonl"), now_ts, days=7)

    lessons_path = STATE_DIR / "lessons.md"
    current_lessons = lessons_path.read_text(encoding="utf-8") if lessons_path.exists() else "(ยังไม่มี lesson ใดๆ)"

    result = run_reflector(
        llm_client,
        model_registry,
        weekly_journal_markdown=render_journal_markdown(decisions),
        closed_trades_markdown=render_trades_markdown(trades),
        current_lessons_text=current_lessons,
    )

    if result.abstained or not result.lessons_markdown:
        print(f"[weekly_reflect] reflector abstain ({result.error}) — ไม่แก้ lessons.md, จบแบบไม่มี error code "
              "เพราะไม่ใช่ความล้มเหลวที่ต้องแจ้งเตือนฉุกเฉิน (แค่ไม่มีอะไรให้ commit สัปดาห์นี้)")
        return 0

    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    lessons_path.write_text(result.lessons_markdown, encoding="utf-8")
    print(f"[weekly_reflect] อัปเดต {lessons_path} สำเร็จ (cost=${result.cost_usd:.4f}, model={result.model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

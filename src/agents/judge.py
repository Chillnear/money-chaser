"""
Judge — ตัดสินใจสุดท้ายคนเดียว จากความเห็นของ 3 analysts + redteam (BUILD-SPEC.md §4.1, §4.3, §7.1)

Non-negotiable ที่บังคับในนี้ (ไม่ใช่แค่ในตัว prompt เอง เพราะ prompt สั่งได้แต่โมเดลอาจไม่ทำตาม):
  - judge เลือก asset ได้เฉพาะใน allowed_assets เท่านั้น — ถ้าเลือกนอกรายชื่อ ถือเป็น schema fail (abstain)
    แม้ pydantic เองจะ validate ผ่านก็ตาม (pydantic ไม่รู้ context ว่าวันนี้อนุญาต asset ไหนบ้าง)
  - LLM ไม่เคยสั่ง order ตรง — output แค่ action/asset/confidence/stop_pct/take_profit_pct เป็น "ความเห็น"
    risk engine (src/risk/*) เป็นคนตัดสินใจขนาดตำแหน่งจริงและ veto ได้เสมอ
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.analysts import AgentRunResult
from src.agents.llm import LLMClient
from src.agents.prompt_builder import render_prompt
from src.agents.redteam import format_analyst_outputs_markdown
from src.agents.registry import get_role_model
from src.agents.schemas import JudgeOutput


@dataclass
class JudgeRunResult:
    model: str
    provider: str
    output: JudgeOutput | None
    abstained: bool
    error: str | None
    cost_usd: float
    latency_ms: float
    tokens_in: int
    tokens_out: int
    attempts: int


def format_hit_rate_table_markdown(hit_rate_by_role: dict[str, dict] | None) -> str:
    """ตาม BUILD-SPEC.md §7.1: ส่งตาราง hit rate ล่าสุดของแต่ละ analyst ให้ judge เห็น
    hit_rate_by_role: {role: {"hit_rate": float 0-1, "n": int, "weight": float}}
    ยังไม่มีข้อมูลพอ (< 15 การทำนายตาม §7.1) ให้บอกตรงๆ ว่ายังใช้น้ำหนักเท่ากันหมด
    """
    if not hit_rate_by_role:
        return "(ยังไม่มีข้อมูล hit rate พอ — ยังไม่ครบ 15 การทำนายตาม BUILD-SPEC.md §7.1 ใช้น้ำหนักเท่ากันหมด)"
    lines = ["| Analyst | Hit rate | จำนวนตัวอย่าง | น้ำหนัก |", "|---|---|---|---|"]
    for role, stats in hit_rate_by_role.items():
        lines.append(
            f"| {role} | {stats.get('hit_rate', 0.0):.0%} | {stats.get('n', 0)} | {stats.get('weight', 1.0):.2f} |"
        )
    return "\n".join(lines)


def format_allowed_assets_markdown(allowed_assets: list[str]) -> str:
    return ", ".join(allowed_assets) if allowed_assets else "(ไม่มีผู้เข้าชิงวันนี้ — ต้องตอบ action: flat เท่านั้น)"


def run_judge(
    llm_client: LLMClient,
    registry: dict,
    feature_table_markdown: str,
    allowed_assets: list[str],
    analyst_results: list[AgentRunResult],
    redteam_result: AgentRunResult,
    lessons_text: str = "",
    hit_rate_by_role: dict[str, dict] | None = None,
) -> JudgeRunResult:
    role_model = get_role_model(registry, "judge")

    system_prompt = render_prompt(
        "judge",
        feature_table=feature_table_markdown,
        lessons=lessons_text,
        allowed_assets=format_allowed_assets_markdown(allowed_assets),
        analyst_hit_rate_table=format_hit_rate_table_markdown(hit_rate_by_role),
        analyst_outputs=format_analyst_outputs_markdown(analyst_results),
        redteam_output=format_analyst_outputs_markdown([redteam_result]),
    )
    user_prompt = (
        "กรุณาตัดสินใจแล้วตอบเป็น JSON ตาม schema เท่านั้น ห้ามเลือก asset นอกรายชื่อที่อนุญาตเด็ดขาด"
    )

    result = llm_client.call_structured(
        model=role_model.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=JudgeOutput,
        is_groq=(role_model.source == "groq"),
    )

    judge_output = result.parsed
    error = result.error
    abstained = result.abstained

    # กันเคส judge เลือก asset นอกรายชื่อที่อนุญาต แม้ pydantic validate ผ่าน (pydantic ไม่รู้ context นี้)
    if judge_output is not None and judge_output.asset is not None and judge_output.asset not in allowed_assets:
        rejected_asset = judge_output.asset
        abstained = True
        judge_output = None
        error = (
            f"judge เลือก asset '{rejected_asset}' ที่ไม่อยู่ในรายชื่อที่อนุญาตวันนี้ {allowed_assets} — "
            "ถือเป็น schema fail ตาม BUILD-SPEC.md §4.3 (abstain เฉพาะ judge ไม่ทำให้ทั้ง pipeline ล้ม)"
        )

    return JudgeRunResult(
        model=role_model.model,
        provider=role_model.provider,
        output=judge_output,
        abstained=abstained,
        error=error,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        attempts=result.attempts,
    )

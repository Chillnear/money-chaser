"""
Red Team — อ่านความเห็นของ 3 analysts แล้วหาเหตุผลค้าน consensus ให้แรงที่สุด (BUILD-SPEC.md §4.1, §4.3)
ใช้ schema เดียวกับ analyst (AnalystOutput) และ semantics เดียวกัน (abstain ได้ ไม่ทำให้ pipeline ล้ม)
"""
from __future__ import annotations

from src.agents.analysts import AgentRunResult, _result_from_llm_call
from src.agents.llm import LLMClient
from src.agents.prompt_builder import render_prompt
from src.agents.registry import get_role_model
from src.agents.schemas import AnalystOutput


def format_analyst_outputs_markdown(analyst_results: list[AgentRunResult]) -> str:
    """แปลงผลลัพธ์ analyst หลายตัวเป็น markdown ให้ redteam/judge อ่าน — ตัวที่ abstain ให้ระบุชัดว่า
    abstain (ไม่ใช่เงียบๆตัดออก) เพราะ redteam/judge ควรรู้ว่าขาดมุมมองไหนไปบ้างในวันนั้น
    """
    lines: list[str] = []
    for r in analyst_results:
        lines.append(f"### {r.role} (model: {r.model}, provider: {r.provider})")
        if r.abstained or r.output is None:
            lines.append(f"- **abstain** — {r.error or 'ไม่มีรายละเอียด'}")
            continue
        for c in r.output.candidates:
            lines.append(
                f"- **{c.asset}**: {c.direction} (confidence {c.confidence:.0f}) — {c.thesis} "
                f"[invalidation: {c.invalidation or '-'}]"
            )
    return "\n".join(lines) if lines else "(ไม่มีความเห็นจาก analyst ใดเลย — ทุกตัว abstain วันนี้)"


def run_redteam(
    llm_client: LLMClient,
    registry: dict,
    feature_table_markdown: str,
    analyst_results: list[AgentRunResult],
    lessons_text: str = "",
) -> AgentRunResult:
    role_model = get_role_model(registry, "redteam")
    analyst_outputs_markdown = format_analyst_outputs_markdown(analyst_results)

    system_prompt = render_prompt(
        "redteam",
        feature_table=feature_table_markdown,
        lessons=lessons_text,
        analyst_outputs=analyst_outputs_markdown,
    )
    user_prompt = (
        "กรุณาหาเหตุผลค้าน consensus ของ analysts ข้างต้นให้แรงที่สุด แล้วตอบเป็น JSON ตาม schema เท่านั้น"
    )

    result = llm_client.call_structured(
        model=role_model.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AnalystOutput,
        is_groq=(role_model.source == "groq"),
    )
    return _result_from_llm_call("redteam", role_model, result)

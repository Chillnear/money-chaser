"""
เรียก 3 analysts (trend / positioning / macro) — ตาม BUILD-SPEC.md §4.1, §4.3

แต่ละ analyst เห็นผู้เข้าชิงทุกตัวที่ screening.py คัดมาแล้วในการเรียกครั้งเดียว (ไม่แยกเรียกทีละ asset
เพื่อกันค่าใช้จ่ายบวม) แต่ละตัวใช้โมเดลจาก provider ต่างกันตาม config/models.yaml (บังคับ diversity
ตรวจแล้วที่ registry.assert_provider_diversity() ตอน startup ของ main.py — ที่นี่ไม่ตรวจซ้ำ)

ถ้า analyst ตัวใด abstain (parse/validate ไม่ผ่านหลัง retry) — ไม่ raise ทำให้ทั้ง pipeline ล้ม ตัวอื่น
ยังทำงานต่อได้ตามปกติ (semantics นี้มาจาก LLMClient.call_structured เอง)
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.llm import LLMClient, LLMCallResult
from src.agents.prompt_builder import render_prompt
from src.agents.registry import RoleModel, get_role_model
from src.agents.schemas import AnalystOutput

ANALYST_ROLES = ["analyst_trend", "analyst_positioning", "analyst_macro"]


@dataclass
class AgentRunResult:
    """ผลลัพธ์ของ 1 agent call (analyst/redteam) — ใช้ร่วมกันทั้ง analysts.py และ redteam.py
    เพราะทั้งสองใช้ schema เดียวกัน (AnalystOutput) และ semantics เดียวกัน (abstain ได้)
    """

    role: str
    model: str
    provider: str
    output: AnalystOutput | None
    abstained: bool
    error: str | None
    cost_usd: float
    latency_ms: float
    tokens_in: int
    tokens_out: int
    attempts: int


def _result_from_llm_call(role: str, role_model: RoleModel, result: LLMCallResult) -> AgentRunResult:
    return AgentRunResult(
        role=role,
        model=role_model.model,
        provider=role_model.provider,
        output=result.parsed,
        abstained=result.abstained,
        error=result.error,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        attempts=result.attempts,
    )


def run_analyst(
    role: str,
    llm_client: LLMClient,
    registry: dict,
    feature_table_markdown: str,
    lessons_text: str = "",
    regime_tags_markdown: str = "",
    funding_table_markdown: str = "",
    macro_snapshot_markdown: str = "",
    news_headlines_markdown: str = "",
    fear_greed_markdown: str = "",
) -> AgentRunResult:
    """เรียก analyst 1 ตัวตาม role — placeholder ที่ไม่เกี่ยวกับ role นั้นจะถูกใส่แค่เผื่อไว้ (render_prompt
    เติมเฉพาะ {{...}} ที่มีอยู่จริงในไฟล์ template ของ role นั้น ตัวที่เหลือจะถูก render_prompt เมิน)
    """
    if role not in ANALYST_ROLES:
        raise ValueError(f"role ต้องเป็นหนึ่งใน {ANALYST_ROLES} ได้รับ: {role!r}")

    role_model = get_role_model(registry, role)

    system_prompt = render_prompt(
        role,
        feature_table=feature_table_markdown,
        lessons=lessons_text,
        regime_tags=regime_tags_markdown,
        funding_table=funding_table_markdown,
        macro_snapshot=macro_snapshot_markdown,
        news_headlines=news_headlines_markdown,
        fear_greed=fear_greed_markdown,
    )
    user_prompt = (
        "กรุณาวิเคราะห์ผู้เข้าชิงทุกตัวข้างต้นแล้วตอบเป็น JSON ตาม schema เท่านั้น ห้ามมีข้อความอื่นนอก JSON"
    )

    result = llm_client.call_structured(
        model=role_model.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AnalystOutput,
        is_groq=(role_model.source == "groq"),
    )
    return _result_from_llm_call(role, role_model, result)


def run_all_analysts(
    llm_client: LLMClient,
    registry: dict,
    feature_table_markdown: str,
    lessons_text: str = "",
    **shared_placeholders: str,
) -> list[AgentRunResult]:
    """เรียกทั้ง 3 analyst ทีละตัว (ไม่มี async ใน dev sandbox แต่แต่ละ call อิสระจากกันจริง — เรียก
    async/thread ได้ในอนาคตถ้าต้องการลด latency รวม) ตัวใด abstain ไม่กระทบตัวอื่น
    """
    return [
        run_analyst(role, llm_client, registry, feature_table_markdown, lessons_text, **shared_placeholders)
        for role in ANALYST_ROLES
    ]

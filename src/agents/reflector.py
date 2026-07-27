"""
Reflector — สรุปบทเรียนรายสัปดาห์ (BUILD-SPEC.md §7.2, §4.1) เขียน/แก้ state/lessons.md เท่านั้น

ต่างจาก analyst/redteam/judge: reflector ตอบเป็น markdown อิสระ ไม่ใช่ JSON ตาม schema (ดู
prompts/reflector.md — ต้องส่งเนื้อหา lessons.md ทั้งไฟล์กลับมา) จึงใช้ LLMClient.call_freeform()
แทน call_structured() ไม่มี pydantic validate

ขอบเขตอำนาจ (ตาม BUILD-SPEC.md §7.2, บังคับด้วยโค้ดที่นี่ ไม่ใช่พึ่ง prompt อย่างเดียว):
  - เขียนได้แค่ state/lessons.md — โค้ดในไฟล์นี้ไม่มีทางเขียนไฟล์อื่นเลยเพราะไม่รับ path อื่นเป็น parameter
  - workflow ระดับ GitHub Actions (weekly_reflect.yml) ต้องเปิดเป็น PR ให้มนุษย์ review ไม่ push ตรง
    (8 สัปดาห์แรกตาม BUILD-SPEC.md — การบังคับ "ไม่ push ตรง" เป็นหน้าที่ของ workflow ไม่ใช่ไฟล์นี้)
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agents.llm import LLMClient
from src.agents.prompt_builder import render_prompt
from src.agents.registry import get_role_model


@dataclass
class ReflectorRunResult:
    model: str
    provider: str
    lessons_markdown: str | None
    abstained: bool
    error: str | None
    cost_usd: float
    latency_ms: float
    tokens_in: int
    tokens_out: int
    attempts: int


def run_reflector(
    llm_client: LLMClient,
    registry: dict,
    weekly_journal_markdown: str,
    closed_trades_markdown: str,
    current_lessons_text: str,
) -> ReflectorRunResult:
    role_model = get_role_model(registry, "reflector")

    system_prompt = render_prompt(
        "reflector",
        weekly_journal=weekly_journal_markdown,
        closed_trades=closed_trades_markdown,
        current_lessons=current_lessons_text,
    )
    user_prompt = (
        "กรุณาสรุปบทเรียนของสัปดาห์นี้แล้วส่งเนื้อหา state/lessons.md ฉบับใหม่ทั้งไฟล์กลับมาตามรูปแบบที่กำหนด "
        "ห้ามแก้ไฟล์อื่นหรือแนะนำให้แก้ risk.yaml/config.yaml/โค้ดเด็ดขาด"
    )

    result = llm_client.call_freeform(
        model=role_model.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        is_groq=(role_model.source == "groq"),
    )

    return ReflectorRunResult(
        model=role_model.model,
        provider=role_model.provider,
        lessons_markdown=result.raw_text,
        abstained=result.abstained,
        error=result.error,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        attempts=result.attempts,
    )

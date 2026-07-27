"""
Pydantic schema สำหรับ output ของแต่ละ role ตาม BUILD-SPEC.md ข้อ 4.3 — บังคับ validate ทุกครั้ง
ก่อนใช้ผลลัพธ์จาก LLM ต่อ (non-negotiable ข้อ 1: LLM ผลิตได้แค่ JSON ที่ผ่าน schema เท่านั้น)
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

Direction = str  # "long" | "short" | "flat" (validate ด้วยมือ ไม่ใช้ Literal เพื่อ error message ที่อ่านง่ายกว่า)


class CandidateAssessment(BaseModel):
    asset: str
    direction: str
    confidence: float = Field(ge=0, le=100)
    thesis: str = Field(max_length=400)
    key_evidence: list[str] = Field(default_factory=list)
    invalidation: str = ""
    expected_move_pct: float = 0.0
    horizon_days: int = 1

    @field_validator("direction")
    @classmethod
    def _validate_direction(cls, v: str) -> str:
        if v not in ("long", "short", "flat"):
            raise ValueError(f"direction ต้องเป็น long|short|flat เท่านั้น ได้รับ: {v!r}")
        return v


class AnalystOutput(BaseModel):
    """ผลลัพธ์ของ analyst/redteam — วิเคราะห์ทุกผู้เข้าชิงในเรียกครั้งเดียว (ประหยัด token ตาม BUILD-SPEC ข้อ 4.3)"""

    candidates: list[CandidateAssessment] = Field(min_length=1)


class JudgeOutput(BaseModel):
    action: str
    asset: str | None = None
    confidence: float = Field(ge=0, le=100)
    stop_pct: float = Field(ge=0)
    take_profit_pct: float = Field(ge=0)
    reasoning: str = Field(max_length=800)
    why_this_over_others: str = ""
    agreement_summary: str = ""
    redteam_response: str = ""
    lessons_applied: list[str] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        if v not in ("long", "short", "flat"):
            raise ValueError(f"action ต้องเป็น long|short|flat เท่านั้น ได้รับ: {v!r}")
        return v

    @model_validator(mode="after")
    def _asset_required_unless_flat(self) -> "JudgeOutput":
        # ตรวจแบบเบื้องต้นในนี้ก่อน — เช็คว่า asset อยู่ใน shortlist จริงเป็นหน้าที่ของ risk/rules.py อีกชั้น
        # ใช้ model_validator (ไม่ใช่ field_validator) เพราะ field_validator ไม่รันกับค่า default (asset=None)
        if self.action in ("long", "short") and not self.asset:
            raise ValueError("action เป็น long/short ต้องระบุ asset เสมอ")
        return self

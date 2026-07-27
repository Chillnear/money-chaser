"""
LiteLLM wrapper — จุดเดียวที่ทุก agent เรียก LLM ผ่าน (BUILD-SPEC.md ข้อ 4.1-4.2)

ทำหน้าที่:
  - เรียก LiteLLM proxy / Groq ตรง พร้อม retry ถ้า schema validate ไม่ผ่าน (ส่ง error กลับไปให้โมเดลแก้)
  - หมุนใช้ 2 keys (round-robin + fallback อัตโนมัติเมื่อเจอ rate limit)
  - วัด/บันทึกต้นทุนทุกครั้ง (cost meter) และมี degradation ladder เมื่อใช้งบเกิน
  - เช็ค token cap ก่อนยิงจริง (กัน prompt บวมโดยไม่รู้ตัว)

**ไม่ได้ยิงทดสอบกับ LiteLLM/Groq จริงในสภาพแวดล้อมนี้** (sandbox บล็อก network เหมือนไฟล์อื่นในชุดนี้)
unit test ทั้งหมด mock ฟังก์ชัน completion เข้าไปแทน — ต้อง smoke test อีกรอบบน GitHub Actions ก่อนใช้จริง
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.data.features import estimate_tokens

T = TypeVar("T", bound=BaseModel)

# ---- Degradation ladder levels (ยิ่งเลขสูง = ตัด agent ออกมากขึ้น เพื่อคุมงบ) ----
DEGRADE_FULL = 0  # ทุก role ทำงานปกติ
DEGRADE_DROP_REDTEAM = 1
DEGRADE_TWO_ANALYSTS_ONLY = 2
DEGRADE_JUDGE_ONLY = 3
DEGRADE_LLM_OFF = 4  # ปิด LLM ทั้งหมด ใช้ baseline.py ล้วน (ระบบยังเทรดต่อได้)


@dataclass
class LLMCallResult:
    parsed: BaseModel | None
    raw_text: str | None
    cost_usd: float
    latency_ms: float
    tokens_in: int
    tokens_out: int
    attempts: int
    error: str | None = None
    abstained: bool = False


def parse_json_from_text(text: str) -> dict:
    """ตัด code fence (```json ... ```) ออกถ้ามี แล้ว json.loads — โมเดลบางตัวชอบห่อ JSON ด้วย markdown"""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


class KeyRotator:
    """หมุนใช้ key แบบ round-robin ทีละครั้งที่เรียก, ลองทุก key ครบ 1 รอบถ้าเจอ rate-limit ก่อนยอมแพ้"""

    def __init__(self, api_keys: list[str]):
        if not api_keys:
            raise ValueError("ต้องมี API key อย่างน้อย 1 ตัว")
        self.api_keys = api_keys
        self._idx = 0

    def next_key(self) -> str:
        key = self.api_keys[self._idx % len(self.api_keys)]
        self._idx += 1
        return key

    def all_keys_in_rotation_order(self) -> list[str]:
        return [self.api_keys[(self._idx + i) % len(self.api_keys)] for i in range(len(self.api_keys))]


# ---- Cost governor: pure functions ที่อ่านจาก records (list of dict จาก llm_cost.jsonl) ----


def compute_spend(records: list[dict], since_ts: float, until_ts: float) -> float:
    return sum(r.get("cost_usd", 0.0) for r in records if since_ts <= r.get("ts", 0) < until_ts)


def get_degradation_level(
    daily_spend_usd: float,
    monthly_spend_usd: float,
    daily_soft_cap_usd: float,
    monthly_hard_stop_usd: float,
) -> int:
    """คำนวณระดับ degradation จากงบที่ใช้ไปแล้ว (ดูคำอธิบาย ladder ด้านบนไฟล์)
    เกณฑ์: ใช้สัดส่วนของ monthly_hard_stop_usd เป็นตัวขยับระดับ + daily_soft_cap แยกสำหรับ level 1
    """
    if monthly_spend_usd >= monthly_hard_stop_usd:
        return DEGRADE_LLM_OFF
    if monthly_spend_usd >= monthly_hard_stop_usd * 0.9:
        return DEGRADE_JUDGE_ONLY
    if monthly_spend_usd >= monthly_hard_stop_usd * 0.8:
        return DEGRADE_TWO_ANALYSTS_ONLY
    if daily_spend_usd >= daily_soft_cap_usd:
        return DEGRADE_DROP_REDTEAM
    return DEGRADE_FULL


def roles_active_at_level(level: int) -> list[str]:
    all_roles = ["analyst_trend", "analyst_positioning", "analyst_macro", "redteam", "judge"]
    if level >= DEGRADE_LLM_OFF:
        return []
    if level >= DEGRADE_JUDGE_ONLY:
        return ["judge"]
    if level >= DEGRADE_TWO_ANALYSTS_ONLY:
        return ["analyst_trend", "analyst_positioning", "judge"]
    if level >= DEGRADE_DROP_REDTEAM:
        return ["analyst_trend", "analyst_positioning", "analyst_macro", "judge"]
    return all_roles


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_keys: list[str],
        input_token_cap: int = 8000,
        output_token_cap: int = 1500,
        timeout_sec: float = 60.0,
        max_validation_retries: int = 2,
        completion_fn: Callable | None = None,
        cost_fn: Callable | None = None,
    ):
        self.base_url = base_url
        self.rotator = KeyRotator(api_keys)
        self.input_token_cap = input_token_cap
        self.output_token_cap = output_token_cap
        self.timeout_sec = timeout_sec
        self.max_validation_retries = max_validation_retries

        # inject ได้เพื่อ mock ตอนเทส — default จะ import litellm ตอนเรียกจริงเท่านั้น (lazy) กันปัญหา
        # ตอน import module นี้ในสภาพแวดล้อมที่ไม่มี litellm ติดตั้ง
        self._completion_fn = completion_fn
        self._cost_fn = cost_fn

    def _get_completion_fn(self) -> Callable:
        if self._completion_fn is not None:
            return self._completion_fn
        import litellm

        return litellm.completion

    def _get_cost_fn(self) -> Callable:
        if self._cost_fn is not None:
            return self._cost_fn
        import litellm

        return litellm.completion_cost

    def call_structured(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: Type[T],
        is_groq: bool = False,
    ) -> LLMCallResult:
        """เรียก LLM แล้ว validate ผลลัพธ์ด้วย schema — retry พร้อมส่ง error กลับไปถ้า parse/validate ไม่ผ่าน
        parse ไม่ผ่านแม้ retry ครบแล้ว = ถือว่า agent นี้ abstain (ไม่ raise ทำให้ทั้ง pipeline ล้ม)
        """
        input_tokens_est = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
        if input_tokens_est > self.input_token_cap:
            return LLMCallResult(
                parsed=None,
                raw_text=None,
                cost_usd=0.0,
                latency_ms=0.0,
                tokens_in=input_tokens_est,
                tokens_out=0,
                attempts=0,
                error=f"prompt เกิน token cap ({input_tokens_est} > {self.input_token_cap}) — ไม่ยิงเรียก",
                abstained=True,
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error: str | None = None
        total_cost = 0.0
        attempts = 0
        raw_text: str | None = None
        candidate_keys = self.rotator.all_keys_in_rotation_order()

        for attempt in range(self.max_validation_retries + 1):
            attempts += 1
            response = None
            call_succeeded = False

            for key in candidate_keys:
                try:
                    start = time.time()
                    kwargs = {
                        "model": model,
                        "messages": messages,
                        "timeout": self.timeout_sec,
                        "max_tokens": self.output_token_cap,
                        "api_key": key,
                    }
                    if not is_groq:
                        kwargs["api_base"] = self.base_url
                    response = self._get_completion_fn()(**kwargs)
                    latency_ms = (time.time() - start) * 1000
                    call_succeeded = True
                    break
                except Exception as exc:  # noqa: BLE001 - ลอง key ถัดไปถ้าเจอ rate limit/error อื่น
                    last_error = str(exc)
                    continue

            if not call_succeeded:
                return LLMCallResult(
                    parsed=None,
                    raw_text=None,
                    cost_usd=total_cost,
                    latency_ms=0.0,
                    tokens_in=input_tokens_est,
                    tokens_out=0,
                    attempts=attempts,
                    error=f"เรียกไม่ผ่านทุก key: {last_error}",
                    abstained=True,
                )

            try:
                cost = self._get_cost_fn()(completion_response=response)
            except Exception:  # noqa: BLE001 - cost tracking เป็น best-effort ไม่ critical ต่อความถูกต้อง
                cost = 0.0
            total_cost += cost or 0.0

            raw_text = response.choices[0].message.content
            tokens_out = estimate_tokens(raw_text)

            try:
                data = parse_json_from_text(raw_text)
                parsed = schema.model_validate(data)
                return LLMCallResult(
                    parsed=parsed,
                    raw_text=raw_text,
                    cost_usd=total_cost,
                    latency_ms=latency_ms,
                    tokens_in=input_tokens_est,
                    tokens_out=tokens_out,
                    attempts=attempts,
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)
                messages.append({"role": "assistant", "content": raw_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"คำตอบก่อนหน้าผิดรูปแบบ: {last_error}\n"
                            "กรุณาตอบใหม่เป็น JSON ที่ตรง schema เท่านั้น ห้ามมีข้อความอื่นปน"
                        ),
                    }
                )
                continue

        return LLMCallResult(
            parsed=None,
            raw_text=raw_text,
            cost_usd=total_cost,
            latency_ms=0.0,
            tokens_in=input_tokens_est,
            tokens_out=0,
            attempts=attempts,
            error=f"parse/validate ไม่ผ่านหลัง retry {self.max_validation_retries} ครั้ง: {last_error}",
            abstained=True,
        )

    def call_freeform(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        is_groq: bool = False,
    ) -> LLMCallResult:
        """เหมือน call_structured แต่ไม่มี schema ให้ validate — ใช้กับ role ที่ตอบเป็นข้อความอิสระ
        (เช่น reflector ที่ต้องส่งเนื้อหา state/lessons.md ทั้งไฟล์เป็น markdown ไม่ใช่ JSON ตาม schema)
        ไม่มี retry เพราะไม่มีอะไรให้ validate ผิด — abstain ได้แค่กรณีเดียวคือทุก key เรียกไม่ผ่าน หรือ
        prompt เกิน token cap (เหมือน call_structured)
        """
        input_tokens_est = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
        if input_tokens_est > self.input_token_cap:
            return LLMCallResult(
                parsed=None,
                raw_text=None,
                cost_usd=0.0,
                latency_ms=0.0,
                tokens_in=input_tokens_est,
                tokens_out=0,
                attempts=0,
                error=f"prompt เกิน token cap ({input_tokens_est} > {self.input_token_cap}) — ไม่ยิงเรียก",
                abstained=True,
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        candidate_keys = self.rotator.all_keys_in_rotation_order()
        last_error: str | None = None

        for key in candidate_keys:
            try:
                start = time.time()
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "timeout": self.timeout_sec,
                    "max_tokens": self.output_token_cap,
                    "api_key": key,
                }
                if not is_groq:
                    kwargs["api_base"] = self.base_url
                response = self._get_completion_fn()(**kwargs)
                latency_ms = (time.time() - start) * 1000
            except Exception as exc:  # noqa: BLE001 - ลอง key ถัดไปถ้าเจอ rate limit/error อื่น
                last_error = str(exc)
                continue

            try:
                cost = self._get_cost_fn()(completion_response=response)
            except Exception:  # noqa: BLE001 - cost tracking เป็น best-effort ไม่ critical
                cost = 0.0

            raw_text = response.choices[0].message.content
            tokens_out = estimate_tokens(raw_text)
            return LLMCallResult(
                parsed=None,
                raw_text=raw_text,
                cost_usd=cost or 0.0,
                latency_ms=latency_ms,
                tokens_in=input_tokens_est,
                tokens_out=tokens_out,
                attempts=1,
            )

        return LLMCallResult(
            parsed=None,
            raw_text=None,
            cost_usd=0.0,
            latency_ms=0.0,
            tokens_in=input_tokens_est,
            tokens_out=0,
            attempts=1,
            error=f"เรียกไม่ผ่านทุก key: {last_error}",
            abstained=True,
        )

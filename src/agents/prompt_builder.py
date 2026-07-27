"""
โหลด prompt template จาก src/agents/prompts/*.md แล้วแทนค่า placeholder จริงก่อนส่งเข้า LLMClient

Template แต่ละไฟล์มี 2 ส่วน: (1) HTML comment header เก็บ version/role/placeholder list — ไม่ส่งเข้า LLM
(2) เนื้อ prompt จริง (เริ่มที่ "# System Prompt") ซึ่งมี {{placeholder}} ที่ main.py ต้องแทนด้วยข้อมูลจริง
"""
from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_HEADER_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)


def load_prompt_template(role: str) -> str:
    """โหลด raw template ทั้งไฟล์ (รวม header comment) — ใช้เวลาเทสหรือ inspect ก็ได้"""
    path = PROMPTS_DIR / f"{role}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"ไม่พบ prompt template สำหรับ role '{role}' ที่ {path} — เช็คชื่อไฟล์ให้ตรงกับ role"
        )
    return path.read_text(encoding="utf-8")


def strip_header_comment(raw_template: str) -> str:
    """ตัด HTML comment header ที่อยู่บนสุดของไฟล์ออก เหลือแค่เนื้อ prompt ที่จะส่งเข้า LLM จริง"""
    return _HEADER_COMMENT_RE.sub("", raw_template, count=1)


def render_prompt(role: str, **placeholders: str) -> str:
    """โหลด template ของ role, ตัด header comment ออก, แล้วแทน {{key}} ทุกตัวด้วยค่าจาก placeholders

    ถ้า placeholder ไหนไม่ได้ส่งมาหรือเป็นค่าว่าง จะแทนด้วยข้อความ "(ไม่มีข้อมูล)" แทนการทิ้ง {{...}} ค้างไว้
    (ค้างไว้แล้วส่งเข้า LLM ตรงๆ จะทำให้โมเดลสับสนว่าเป็น syntax อะไร)
    """
    text = strip_header_comment(load_prompt_template(role))
    for key, value in placeholders.items():
        token = "{{" + key + "}}"
        text = text.replace(token, value if value else "(ไม่มีข้อมูล)")
    return text


def find_unfilled_placeholders(rendered_text: str) -> list[str]:
    """เช็คว่ายังมี {{...}} หลงเหลือหลัง render หรือไม่ — ใช้ป้องกัน typo ของชื่อ placeholder ใน main.py"""
    return re.findall(r"\{\{[a-zA-Z0-9_]+\}\}", rendered_text)

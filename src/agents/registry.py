"""
โหลด config/models.yaml (เขียนโดย scripts/probe_models.py เท่านั้น — ห้ามแก้มือ) แล้วจัดให้ agent
ทุกตัวเรียกโมเดลที่ถูก role ได้ + assert provider diversity ตอน startup

ตาม BUILD-SPEC.md ข้อ 4.1: "4 บทบาทแรกต้องใช้โมเดลจาก อย่างน้อย 3 ค่ายที่ต่างกัน (registry.py ต้อง
assert เรื่องนี้ตอน startup)" — ถ้าไม่ผ่าน raise ทันที (fail-closed) ไม่ปล่อยให้ระบบรันด้วย diversity
ที่ไม่พอ เพราะเท่ากับ agent คุยกับตัวเองซ้ำๆ ไม่ใช่การถกเถียงจริง
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DIVERSITY_REQUIRED_ROLES = ["analyst_trend", "analyst_positioning", "analyst_macro", "redteam"]
MIN_PROVIDER_DIVERSITY = 3


class RegistryError(Exception):
    """ใช้แยกจาก exception อื่น เพื่อให้ main.py รู้ว่าปัญหาอยู่ที่ model registry ไม่ใช่ network/logic อื่น"""


@dataclass
class RoleModel:
    role: str
    model: str
    provider: str
    tier: str
    source: str  # "litellm" | "groq" — ใช้บอก LLMClient ว่าต้องส่ง api_base หรือไม่


def load_model_registry(path: Path) -> dict:
    if not path.exists():
        raise RegistryError(
            f"ไม่พบ {path} — ต้องรัน scripts/probe_models.py ก่อนเพื่อสร้างไฟล์นี้จากผลจริงของ LiteLLM/Groq"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "roles" not in data:
        raise RegistryError(f"{path} ไม่มีคีย์ 'roles' — ไฟล์อาจยังไม่ถูก probe หรือถูกแก้ผิดรูป")
    return data


def get_role_model(registry: dict, role: str) -> RoleModel:
    roles = registry.get("roles", {})
    if role not in roles:
        raise RegistryError(f"ไม่พบ role '{role}' ใน model registry (มี role: {list(roles.keys())})")
    entry = roles[role]
    return RoleModel(
        role=role,
        model=entry["model"],
        provider=entry["provider"],
        tier=entry.get("tier", "mid"),
        source=entry.get("source", "litellm"),
    )


def assert_provider_diversity(
    registry: dict,
    roles: list[str] = DIVERSITY_REQUIRED_ROLES,
    min_providers: int = MIN_PROVIDER_DIVERSITY,
) -> None:
    """เรียกตอน startup — raise RegistryError ทันทีถ้า diversity ไม่พอ (fail-closed ตาม non-negotiable ข้อ 4.1)"""
    providers = set()
    missing_roles = []
    for role in roles:
        try:
            role_model = get_role_model(registry, role)
            providers.add(role_model.provider)
        except RegistryError:
            missing_roles.append(role)

    if missing_roles:
        raise RegistryError(f"ขาด role ที่จำเป็นใน model registry: {missing_roles}")

    if len(providers) < min_providers:
        raise RegistryError(
            f"provider diversity ไม่พอ: {roles} ใช้ค่ายซ้ำกันจนเหลือแค่ {len(providers)} ค่าย "
            f"({sorted(providers)}) ต้องการอย่างน้อย {min_providers} ค่าย — "
            "ต้องแก้ config/models.yaml (รัน probe_models.py ใหม่ ถ้ามีโมเดลค่ายอื่นเพิ่มใน LiteLLM/Groq)"
        )

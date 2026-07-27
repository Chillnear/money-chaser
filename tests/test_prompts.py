"""
Smoke test สำหรับ prompt template ทั้ง 6 role — เช็คว่าไฟล์มีอยู่จริง, มี version header,
และมี placeholder ที่ main.py ต้องแทนค่าจริงครบ (กัน typo ตอนแก้ prompt ในอนาคต)
"""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "prompts"

REQUIRED_PLACEHOLDERS = {
    "analyst_trend.md": ["{{feature_table}}", "{{lessons}}", "{{regime_tags}}"],
    "analyst_positioning.md": ["{{feature_table}}", "{{lessons}}", "{{regime_tags}}", "{{funding_table}}"],
    "analyst_macro.md": [
        "{{feature_table}}",
        "{{lessons}}",
        "{{macro_snapshot}}",
        "{{news_headlines}}",
        "{{fear_greed}}",
    ],
    "redteam.md": ["{{feature_table}}", "{{lessons}}", "{{analyst_outputs}}"],
    "judge.md": [
        "{{feature_table}}",
        "{{lessons}}",
        "{{analyst_outputs}}",
        "{{redteam_output}}",
        "{{allowed_assets}}",
        "{{analyst_hit_rate_table}}",
    ],
    "reflector.md": ["{{weekly_journal}}", "{{closed_trades}}", "{{current_lessons}}"],
}


def test_all_six_role_prompt_files_exist():
    for filename in REQUIRED_PLACEHOLDERS:
        assert (PROMPTS_DIR / filename).exists(), f"ไม่พบไฟล์ prompt: {filename}"


def test_no_extra_or_missing_prompt_files():
    actual = {p.name for p in PROMPTS_DIR.glob("*.md")}
    assert actual == set(REQUIRED_PLACEHOLDERS.keys())


def test_each_prompt_has_version_header():
    for filename in REQUIRED_PLACEHOLDERS:
        text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
        assert "version:" in text
        assert "role:" in text


def test_each_prompt_contains_all_required_placeholders():
    for filename, placeholders in REQUIRED_PLACEHOLDERS.items():
        text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
        for placeholder in placeholders:
            assert placeholder in text, f"{filename} ขาด placeholder {placeholder}"


def test_analyst_and_redteam_prompts_reference_json_schema_with_candidates():
    for filename in ["analyst_trend.md", "analyst_positioning.md", "analyst_macro.md", "redteam.md"]:
        text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
        assert '"candidates"' in text
        assert "direction" in text and "confidence" in text


def test_judge_prompt_forbids_asset_outside_allowed_list():
    text = (PROMPTS_DIR / "judge.md").read_text(encoding="utf-8")
    assert "allowed_assets" in text
    assert '"action"' in text and '"asset"' in text


def test_reflector_prompt_restricts_scope_to_lessons_file_only():
    text = (PROMPTS_DIR / "reflector.md").read_text(encoding="utf-8")
    assert "lessons.md" in text
    assert "risk.yaml" in text  # ต้องพูดถึงว่าห้ามแตะ risk.yaml

from __future__ import annotations

import pytest

from src.agents.prompt_builder import (
    find_unfilled_placeholders,
    load_prompt_template,
    render_prompt,
    strip_header_comment,
)


def test_load_prompt_template_missing_role_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt_template("not_a_real_role")


def test_load_prompt_template_loads_real_file():
    text = load_prompt_template("judge")
    assert "System Prompt" in text
    assert "<!--" in text  # header comment ยังอยู่เพราะยังไม่ strip


def test_strip_header_comment_removes_leading_html_comment():
    raw = "<!--\nversion: 1\nrole: x\n-->\n# System Prompt\nสวัสดี"
    stripped = strip_header_comment(raw)
    assert "<!--" not in stripped
    assert stripped.strip().startswith("# System Prompt")


def test_strip_header_comment_noop_when_no_comment():
    raw = "# System Prompt\nไม่มี comment"
    assert strip_header_comment(raw) == raw


def test_render_prompt_fills_known_placeholders_for_judge():
    rendered = render_prompt(
        "judge",
        feature_table="TABLE",
        lessons="LESSONS",
        allowed_assets="BTC, ETH",
        analyst_hit_rate_table="HITRATE",
        analyst_outputs="ANALYST_OUT",
        redteam_output="REDTEAM_OUT",
    )
    assert "<!--" not in rendered
    assert "TABLE" in rendered
    assert "LESSONS" in rendered
    assert "BTC, ETH" in rendered
    assert "HITRATE" in rendered
    assert "ANALYST_OUT" in rendered
    assert "REDTEAM_OUT" in rendered
    assert find_unfilled_placeholders(rendered) == []


def test_render_prompt_empty_placeholder_becomes_no_data_marker():
    rendered = render_prompt("analyst_trend", feature_table="", lessons="", regime_tags="")
    assert "(ไม่มีข้อมูล)" in rendered
    assert find_unfilled_placeholders(rendered) == []


def test_render_prompt_missing_placeholder_left_unfilled_and_detected():
    # ไม่ส่ง lessons/regime_tags เลย -> ต้องมี {{...}} หลงเหลือ ให้ find_unfilled_placeholders จับได้
    rendered = render_prompt("analyst_trend", feature_table="TABLE")
    unfilled = find_unfilled_placeholders(rendered)
    assert "{{lessons}}" in unfilled
    assert "{{regime_tags}}" in unfilled


def test_render_prompt_all_six_roles_load_without_error():
    for role in [
        "analyst_trend",
        "analyst_positioning",
        "analyst_macro",
        "redteam",
        "judge",
        "reflector",
    ]:
        rendered = render_prompt(role)
        assert "<!--" not in rendered
        assert len(rendered) > 0

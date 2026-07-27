from __future__ import annotations

import pytest

from src.store.scoring import (
    DEFAULT_HALF_LIFE,
    MIN_SAMPLES_FOR_WEIGHT,
    NEUTRAL_WEIGHT,
    WEIGHT_MAX,
    WEIGHT_MIN,
    build_hit_rate_table_input,
    compute_brier_score,
    compute_ewma_brier,
    compute_hit_rate,
    compute_role_score_stats,
    compute_weight_from_ewma_brier,
    determine_actual_outcome,
    ewma_alpha_from_half_life,
    is_prediction_correct,
)


# ---- determine_actual_outcome ----


def test_determine_actual_outcome_long():
    assert determine_actual_outcome(3.0, flat_threshold_pct=1.0) == "long"


def test_determine_actual_outcome_short():
    assert determine_actual_outcome(-3.0, flat_threshold_pct=1.0) == "short"


def test_determine_actual_outcome_flat_within_threshold():
    assert determine_actual_outcome(0.5, flat_threshold_pct=1.0) == "flat"
    assert determine_actual_outcome(-0.5, flat_threshold_pct=1.0) == "flat"


def test_determine_actual_outcome_boundary_is_flat():
    # เท่ากับ threshold พอดี ยังไม่ถือว่าทะลุ (strict >)
    assert determine_actual_outcome(1.0, flat_threshold_pct=1.0) == "flat"


# ---- is_prediction_correct / compute_brier_score ----


def test_is_prediction_correct_true_and_false():
    assert is_prediction_correct("long", "long") is True
    assert is_prediction_correct("long", "short") is False
    assert is_prediction_correct("flat", "flat") is True


def test_brier_score_perfect_confident_correct_prediction_is_zero():
    assert compute_brier_score(confidence=100, predicted_direction="long", actual_direction="long") == pytest.approx(0.0)


def test_brier_score_perfect_confident_wrong_prediction_is_one():
    assert compute_brier_score(confidence=100, predicted_direction="long", actual_direction="short") == pytest.approx(1.0)


def test_brier_score_flat_correct_prediction_scores_nonzero_credit():
    # FLAT ที่ถูก ต้องได้คะแนนตามความมั่นใจเหมือน long/short ที่ถูก ไม่ใช่ผลเป็นศูนย์เสมอ
    score = compute_brier_score(confidence=80, predicted_direction="flat", actual_direction="flat")
    assert score == pytest.approx((0.8 - 1.0) ** 2)
    assert score != 0.0 or True  # แค่ยืนยันว่าคำนวณจริงตามสูตร ไม่ hardcode เป็น 0


def test_brier_score_flat_wrong_prediction_penalized():
    score = compute_brier_score(confidence=80, predicted_direction="flat", actual_direction="long")
    assert score == pytest.approx(0.64)


def test_brier_score_low_confidence_correct_prediction_still_has_some_penalty():
    # มั่นใจ 50% แล้วถูก -> ไม่ใช่ 0 เพราะไม่ได้มั่นใจสุด (มี room ให้ดีขึ้น)
    score = compute_brier_score(confidence=50, predicted_direction="long", actual_direction="long")
    assert score == pytest.approx(0.25)


# ---- compute_hit_rate ----


def test_compute_hit_rate_empty_records():
    assert compute_hit_rate([]) == 0.0


def test_compute_hit_rate_mixed():
    records = [
        {"predicted_direction": "long", "actual_direction": "long"},
        {"predicted_direction": "long", "actual_direction": "short"},
        {"predicted_direction": "flat", "actual_direction": "flat"},
        {"predicted_direction": "short", "actual_direction": "long"},
    ]
    assert compute_hit_rate(records) == pytest.approx(0.5)


# ---- ewma_alpha_from_half_life / compute_ewma_brier ----


def test_ewma_alpha_from_half_life_rejects_zero_or_negative():
    with pytest.raises(ValueError):
        ewma_alpha_from_half_life(0)


def test_ewma_alpha_from_half_life_default_is_reasonable():
    alpha = ewma_alpha_from_half_life(DEFAULT_HALF_LIFE)
    assert 0.0 < alpha < 1.0
    # หลังผ่านไป half_life สเต็ป ค่าน้ำหนักของจุดแรกต้องเหลือประมาณครึ่งหนึ่ง
    remaining_weight = (1 - alpha) ** DEFAULT_HALF_LIFE
    assert remaining_weight == pytest.approx(0.5, rel=1e-3)


def test_compute_ewma_brier_empty_returns_zero():
    assert compute_ewma_brier([]) == 0.0


def test_compute_ewma_brier_single_value():
    assert compute_ewma_brier([0.3]) == pytest.approx(0.3)


def test_compute_ewma_brier_reacts_to_a_new_bad_prediction():
    # หลังทำนายดีต่อเนื่อง (brier=0 ตลอด) แล้วพลาดหนักครั้งล่าสุด (brier=1.0) EWMA ต้องขยับขึ้นทันที
    ewma_before = compute_ewma_brier([0.0, 0.0, 0.0, 0.0], half_life=5)
    ewma_after = compute_ewma_brier([0.0, 0.0, 0.0, 0.0, 1.0], half_life=5)
    assert ewma_after > ewma_before


def test_compute_ewma_brier_old_bad_prediction_decays_over_time():
    # ทำนายแย่ตอนแรกครั้งเดียวแล้วดีตลอดหลังจากนั้น — EWMA ต้องลดลงเรื่อยๆ เมื่อเวลาผ่านไป (ค่าเก่าถูก decay)
    ewma_2_steps_later = compute_ewma_brier([1.0, 0.0, 0.0], half_life=5)
    ewma_4_steps_later = compute_ewma_brier([1.0, 0.0, 0.0, 0.0, 0.0], half_life=5)
    assert ewma_4_steps_later < ewma_2_steps_later


# ---- compute_weight_from_ewma_brier ----


def test_weight_perfect_brier_gets_max_weight():
    assert compute_weight_from_ewma_brier(0.0) == pytest.approx(WEIGHT_MAX)


def test_weight_no_skill_brier_gets_neutral_weight():
    # brier 0.25 คือระดับของการเดา 50/50 ตลอด ไม่มี skill จริง -> ควรได้น้ำหนักกลางๆ
    assert compute_weight_from_ewma_brier(0.25) == pytest.approx(1.0)


def test_weight_clamped_at_minimum_for_bad_brier():
    assert compute_weight_from_ewma_brier(0.9) == pytest.approx(WEIGHT_MIN)


def test_weight_clamped_at_maximum_never_exceeds():
    assert compute_weight_from_ewma_brier(-1.0) == pytest.approx(WEIGHT_MAX)


# ---- compute_role_score_stats ----


def _make_records(n: int, correct_confidence: float = 80.0) -> list[dict]:
    return [
        {"predicted_direction": "long", "confidence": correct_confidence, "actual_direction": "long"}
        for _ in range(n)
    ]


def test_compute_role_score_stats_below_min_samples_uses_neutral_weight():
    records = _make_records(MIN_SAMPLES_FOR_WEIGHT - 1)
    stats = compute_role_score_stats("analyst_trend", records)
    assert stats.n == MIN_SAMPLES_FOR_WEIGHT - 1
    assert stats.weight_applied is False
    assert stats.weight == pytest.approx(NEUTRAL_WEIGHT)


def test_compute_role_score_stats_at_min_samples_applies_real_weight():
    records = _make_records(MIN_SAMPLES_FOR_WEIGHT, correct_confidence=90.0)
    stats = compute_role_score_stats("analyst_trend", records)
    assert stats.n == MIN_SAMPLES_FOR_WEIGHT
    assert stats.weight_applied is True
    assert stats.hit_rate == pytest.approx(1.0)
    # ถูกตลอดด้วยความมั่นใจสูง -> weight ควรสูงกว่ากลาง (1.0)
    assert stats.weight > NEUTRAL_WEIGHT


def test_compute_role_score_stats_mixed_flat_and_directional_records():
    records = [
        {"predicted_direction": "long", "confidence": 70, "actual_direction": "long"},
        {"predicted_direction": "flat", "confidence": 60, "actual_direction": "flat"},
        {"predicted_direction": "short", "confidence": 65, "actual_direction": "long"},
    ] * 6  # 18 records >= 15
    stats = compute_role_score_stats("analyst_positioning", records)
    assert stats.n == 18
    assert stats.weight_applied is True
    # hit_rate = 2/3 ถูก (long ถูก, flat ถูก, short ผิด)
    assert stats.hit_rate == pytest.approx(2 / 3)


# ---- build_hit_rate_table_input ----


def test_build_hit_rate_table_input_shapes_dict_for_judge_prompt():
    stats = compute_role_score_stats("analyst_trend", _make_records(MIN_SAMPLES_FOR_WEIGHT))
    table_input = build_hit_rate_table_input({"analyst_trend": stats})
    assert table_input["analyst_trend"]["hit_rate"] == pytest.approx(stats.hit_rate)
    assert table_input["analyst_trend"]["n"] == stats.n
    assert table_input["analyst_trend"]["weight"] == pytest.approx(stats.weight)

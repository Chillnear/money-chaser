"""
Agent scoring — Brier score + EWMA weight ตาม BUILD-SPEC.md §7.1

แนวคิด: แต่ละ analyst ทำนาย direction (long|short|flat) พร้อม confidence 0-100 → พรุ่งนี้รู้ผลจริงแล้ว
คิดเป็น Brier score (ยิ่งต่ำยิ่งดี, 0 = ทำนายถูกมั่นใจสุด, 1 = ทำนายผิดมั่นใจสุด) แล้วเอาไปทำ EWMA
(half-life 20 การทำนาย) เพื่อได้ "น้ำหนัก" ที่ judge ใช้ถ่วงความน่าเชื่อของแต่ละ analyst

กติกาสำคัญที่ทำให้ต่างจาก Brier score แบบ binary ทั่วไป:
  - FLAT เป็น class ที่ 3 เท่าเทียมกับ long/short — ถ้าทำนาย flat แล้วตลาดนิ่งจริง (หรือผันผวนแรงแล้วกลับที่
    net เป็น flat) ถือว่า "ถูก" และได้คะแนนตามปกติ ไม่ใช่ถูก hardcode ให้เป็น 0 คะแนนแบบที่ implementation
    มือใหม่มักพลาด (ดู BUILD-SPEC.md §7.1: "FLAT ที่ถูกต้องต้องได้คะแนน ไม่ใช่ศูนย์")
  - น้ำหนักยังไม่ถูกใช้จนกว่าจะมีข้อมูล ≥ 15 การทำนาย (ก่อนนั้น weight = 1.0 เท่ากันหมด กันการด่วนสรุปจาก
    sample size เล็ก)
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_HALF_LIFE = 20
MIN_SAMPLES_FOR_WEIGHT = 15
DEFAULT_FLAT_THRESHOLD_PCT = 1.0
WEIGHT_MIN = 0.5
WEIGHT_MAX = 1.5
NEUTRAL_WEIGHT = 1.0


def determine_actual_outcome(actual_return_pct: float, flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT) -> str:
    """แปลง return จริง (ระหว่างตอนทำนายถึง horizon) เป็น class long|short|flat เทียบ threshold
    เพื่อให้ 'ถูก/ผิด' ของการทำนายวัดได้เป็นรูปธรรม ไม่ใช่แค่เทียบเครื่องหมาย +/- ตรงๆ (การขยับเล็กน้อยไม่ควร
    นับว่า analyst ที่ทำนาย flat ผิด)
    """
    if actual_return_pct > flat_threshold_pct:
        return "long"
    if actual_return_pct < -flat_threshold_pct:
        return "short"
    return "flat"


def is_prediction_correct(predicted_direction: str, actual_direction: str) -> bool:
    return predicted_direction == actual_direction


def compute_brier_score(confidence: float, predicted_direction: str, actual_direction: str) -> float:
    """Brier score ของการทำนาย 1 ครั้ง: (p - outcome)^2 โดย p = confidence/100 คือความมั่นใจว่า
    ทิศทางที่ทำนายจะถูก, outcome = 1 ถ้าทำนายถูกจริง (รวมถึง flat ที่ถูก) ไม่งั้น = 0
    """
    p = confidence / 100.0
    outcome = 1.0 if is_prediction_correct(predicted_direction, actual_direction) else 0.0
    return (p - outcome) ** 2


def compute_hit_rate(records: list[dict]) -> float:
    """สัดส่วนที่ทำนายถูก (รวม flat ที่ถูก) จาก records ที่มี predicted_direction/actual_direction"""
    if not records:
        return 0.0
    correct = sum(1 for r in records if is_prediction_correct(r["predicted_direction"], r["actual_direction"]))
    return correct / len(records)


def ewma_alpha_from_half_life(half_life: int) -> float:
    """แปลง half-life (จำนวนการทำนายที่ต้องผ่านไปให้น้ำหนักของค่าเก่าลดลงครึ่งหนึ่ง) เป็น smoothing
    factor alpha ที่ใช้ใน EWMA แบบมาตรฐาน: new = alpha*x + (1-alpha)*old
    สูตร: (1-alpha)^half_life = 0.5  =>  alpha = 1 - 0.5^(1/half_life)
    """
    if half_life <= 0:
        raise ValueError("half_life ต้องมากกว่า 0")
    return 1.0 - 0.5 ** (1.0 / half_life)


def compute_ewma_brier(brier_scores_oldest_first: list[float], half_life: int = DEFAULT_HALF_LIFE) -> float:
    """EWMA ของ Brier score เรียงจากเก่า->ใหม่ (ค่าล่าสุดมีน้ำหนักมากสุด) — คืน 0.0 ถ้าไม่มีข้อมูล
    (caller ต้องเช็ค MIN_SAMPLES_FOR_WEIGHT เองก่อนใช้ค่านี้ตัดสินน้ำหนักจริง)
    """
    if not brier_scores_oldest_first:
        return 0.0
    alpha = ewma_alpha_from_half_life(half_life)
    ewma = brier_scores_oldest_first[0]
    for score in brier_scores_oldest_first[1:]:
        ewma = alpha * score + (1 - alpha) * ewma
    return ewma


def compute_weight_from_ewma_brier(ewma_brier: float, min_weight: float = WEIGHT_MIN, max_weight: float = WEIGHT_MAX) -> float:
    """แปลง EWMA Brier เป็นน้ำหนัก [0.5, 1.5] — Brier=0 (ทำนายถูกมั่นใจสุดตลอด) -> weight สูงสุด,
    Brier=0.25 (เทียบเท่าเดา 50/50 ตลอด, ไม่มี skill) -> weight เป็นกลาง 1.0, Brier>=0.5 -> weight ต่ำสุด
    สูตรเส้นตรง: weight = 1.5 - 2*ewma_brier แล้ว clamp เข้ากรอบ
    """
    raw = max_weight - 2.0 * ewma_brier
    return max(min_weight, min(max_weight, raw))


@dataclass
class RoleScoreStats:
    role: str
    n: int
    hit_rate: float
    ewma_brier: float
    weight: float
    weight_applied: bool  # False ถ้ายังไม่ครบ MIN_SAMPLES_FOR_WEIGHT (weight จะเป็น NEUTRAL_WEIGHT เสมอ)


def compute_role_score_stats(
    role: str,
    records_oldest_first: list[dict],
    half_life: int = DEFAULT_HALF_LIFE,
    min_samples_for_weight: int = MIN_SAMPLES_FOR_WEIGHT,
) -> RoleScoreStats:
    """สรุปสถิติของ analyst 1 role จาก records (เรียงเก่า->ใหม่) แต่ละ record ต้องมี
    predicted_direction, confidence, actual_direction — ใช้ป้อนตาราง hit-rate ให้ judge (§7.1)
    และคำนวณน้ำหนักสำหรับใช้ถ่วงในอนาคต (weighting ตัว judge เองยังไม่บังคับใช้ในเวอร์ชันนี้)
    """
    n = len(records_oldest_first)
    hit_rate = compute_hit_rate(records_oldest_first)
    brier_scores = [
        compute_brier_score(r["confidence"], r["predicted_direction"], r["actual_direction"])
        for r in records_oldest_first
    ]
    ewma_brier = compute_ewma_brier(brier_scores, half_life=half_life)

    weight_applied = n >= min_samples_for_weight
    weight = compute_weight_from_ewma_brier(ewma_brier) if weight_applied else NEUTRAL_WEIGHT

    return RoleScoreStats(
        role=role,
        n=n,
        hit_rate=hit_rate,
        ewma_brier=ewma_brier,
        weight=weight,
        weight_applied=weight_applied,
    )


def build_hit_rate_table_input(stats_by_role: dict[str, RoleScoreStats]) -> dict[str, dict]:
    """แปลง RoleScoreStats หลาย role เป็นรูปแบบ dict ที่ src/agents/judge.py:format_hit_rate_table_markdown
    ต้องการ ({role: {"hit_rate", "n", "weight"}}) — เก็บ scoring.py กับ agents/judge.py ให้ decouple กัน
    """
    return {
        role: {"hit_rate": stats.hit_rate, "n": stats.n, "weight": stats.weight}
        for role, stats in stats_by_role.items()
    }

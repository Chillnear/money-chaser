from __future__ import annotations

import math

import pytest

from src.risk.sizing import compute_position_size

# ค่าเริ่มต้นตาม config/risk.yaml (ข้อ 5 ของ BUILD-SPEC.md)
DEFAULTS = dict(
    risk_per_trade_pct=2.0,
    min_notional_usd=10.0,
    max_notional_usd=40.0,
    max_notional_pct_of_equity=120.0,
    min_notional_override_max_risk_pct=4.0,
    atr_multiple=1.5,
    stop_floor_pct=2.0,
    stop_cap_pct=6.0,
    reward_risk_ratio=2.0,
    max_leverage=3.0,
)


def test_normal_case_equity_28_atr_2_5():
    # stop=3.75%, notional=14.93, leverage=0.53, risk=2% ตามที่คำนวณด้วยมือไว้ในแผน
    result = compute_position_size(equity_usd=28.0, atr_pct=2.5, **DEFAULTS)

    assert result.decision == "OK"
    assert result.stop_pct == pytest.approx(3.75)
    assert result.notional_usd == pytest.approx(14.933, abs=0.01)
    assert result.leverage == pytest.approx(0.5333, abs=0.001)
    assert result.implied_risk_pct == pytest.approx(2.0, abs=0.01)


def test_low_atr_notional_hits_equity_not_min():
    # atr=1.0 -> stop floor 2%, raw_notional=28 อยู่ในช่วงปกติ ไม่โดน floor ที่ min_notional
    result = compute_position_size(equity_usd=28.0, atr_pct=1.0, **DEFAULTS)

    assert result.decision == "OK"
    assert result.stop_pct == 2.0
    assert result.notional_usd == pytest.approx(28.0, abs=0.01)
    assert result.implied_risk_pct == pytest.approx(2.0, abs=0.01)


def test_high_atr_floored_to_min_notional_but_still_ok():
    # atr=8 -> stop cap 6%, raw_notional=9.33 ต่ำกว่า min -> floor เป็น 10, risk=2.14% ยังไม่เกิน 4% -> OK
    result = compute_position_size(equity_usd=28.0, atr_pct=8.0, **DEFAULTS)

    assert result.decision == "OK"
    assert result.stop_pct == 6.0
    assert result.notional_usd == 10.0
    assert result.implied_risk_pct == pytest.approx(2.14, abs=0.01)


def test_small_equity_floored_notional_forces_flat():
    # equity=10, atr สูงพอให้ stop=6% -> raw_notional=3.33 -> floor เป็น 10 -> implied_risk=6% > 4% cap -> FLAT
    result = compute_position_size(equity_usd=10.0, atr_pct=10.0, **DEFAULTS)

    assert result.decision == "FLAT"
    assert "เสี่ยงเกินไป" in result.reason or "override" in result.reason


def test_tiny_equity_hard_cap_below_min_notional_forces_flat():
    # equity เล็กมากจนแม้ leverage/สัดส่วนทุนเต็มที่ก็ยังไปไม่ถึง min_notional
    result = compute_position_size(equity_usd=3.0, atr_pct=2.5, **DEFAULTS)

    assert result.decision == "FLAT"
    assert "ทุนเล็กเกินไป" in result.reason


def test_leverage_cap_binds_when_pct_cap_is_looser():
    # ตั้ง max_notional_pct_of_equity สูงมาก (500%) เพื่อให้ leverage cap (3x) เป็นตัวจำกัดจริง
    cfg = dict(DEFAULTS)
    cfg["max_notional_pct_of_equity"] = 500.0
    cfg["max_notional_usd"] = 100_000.0  # ปลดเพดาน USD ตายตัวออกไปด้วย
    cfg["risk_per_trade_pct"] = 50.0  # บีบให้ raw_notional สูงมากจนต้องโดน cap

    result = compute_position_size(equity_usd=28.0, atr_pct=2.0, **cfg)

    assert result.decision == "OK"
    assert result.leverage == pytest.approx(3.0, abs=0.001)
    assert result.notional_usd == pytest.approx(28.0 * 3.0, abs=0.01)


def test_stop_pct_clamped_to_floor_and_cap():
    tiny_atr = compute_position_size(equity_usd=28.0, atr_pct=0.01, **DEFAULTS)
    assert tiny_atr.stop_pct == 2.0  # floor

    huge_atr = compute_position_size(equity_usd=28.0, atr_pct=100.0, **DEFAULTS)
    assert huge_atr.stop_pct == 6.0  # cap


def test_take_profit_uses_reward_risk_ratio():
    result = compute_position_size(equity_usd=28.0, atr_pct=2.5, **DEFAULTS)
    assert result.take_profit_pct == pytest.approx(result.stop_pct * 2.0)


def test_nan_atr_is_flat():
    result = compute_position_size(equity_usd=28.0, atr_pct=float("nan"), **DEFAULTS)
    assert result.decision == "FLAT"


def test_zero_equity_is_flat():
    result = compute_position_size(equity_usd=0.0, atr_pct=2.5, **DEFAULTS)
    assert result.decision == "FLAT"


def test_negative_equity_is_flat():
    result = compute_position_size(equity_usd=-5.0, atr_pct=2.5, **DEFAULTS)
    assert result.decision == "FLAT"

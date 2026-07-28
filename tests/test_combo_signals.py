from __future__ import annotations

from src.data.combo_signals import (
    HEALTHY_TREND_CONTINUATION,
    LIQUIDATION_CASCADE_PROXY,
    LONG_SQUEEZE_RISK,
    NONE_PATTERN,
    SHORT_SQUEEZE_RISK,
    classify_combination_pattern,
)


def test_returns_none_pattern_with_inputs_missing_flag_when_oi_change_unavailable():
    result = classify_combination_pattern(
        price_return_24h_pct=3.0, oi_change_24h_pct=None, funding_annualized_signed_pct=5.0, volume_spike_ratio=1.5
    )
    assert result.pattern == NONE_PATTERN
    assert result.inputs_missing is True


def test_detects_liquidation_cascade_proxy():
    result = classify_combination_pattern(
        price_return_24h_pct=-7.0, oi_change_24h_pct=-10.0, funding_annualized_signed_pct=2.0, volume_spike_ratio=3.0
    )
    assert result.pattern == LIQUIDATION_CASCADE_PROXY
    assert result.inputs_missing is False


def test_detects_long_squeeze_risk():
    result = classify_combination_pattern(
        price_return_24h_pct=4.0, oi_change_24h_pct=12.0, funding_annualized_signed_pct=20.0, volume_spike_ratio=1.2
    )
    assert result.pattern == LONG_SQUEEZE_RISK


def test_detects_short_squeeze_risk():
    result = classify_combination_pattern(
        price_return_24h_pct=-4.0, oi_change_24h_pct=12.0, funding_annualized_signed_pct=-20.0, volume_spike_ratio=1.2
    )
    assert result.pattern == SHORT_SQUEEZE_RISK


def test_detects_healthy_trend_continuation():
    result = classify_combination_pattern(
        price_return_24h_pct=3.0, oi_change_24h_pct=4.0, funding_annualized_signed_pct=5.0, volume_spike_ratio=1.1
    )
    assert result.pattern == HEALTHY_TREND_CONTINUATION


def test_defaults_to_none_pattern_when_nothing_matches():
    result = classify_combination_pattern(
        price_return_24h_pct=0.5, oi_change_24h_pct=1.0, funding_annualized_signed_pct=1.0, volume_spike_ratio=1.0
    )
    assert result.pattern == NONE_PATTERN
    assert result.inputs_missing is False


def test_missing_volume_spike_ratio_defaults_to_neutral_and_does_not_crash():
    # ไม่มี volume_spike_ratio (เช่นแท่งเทียนไม่พอคำนวณ) -> ใช้ 1.0 แทน ไม่ raise
    result = classify_combination_pattern(
        price_return_24h_pct=-7.0, oi_change_24h_pct=-10.0, funding_annualized_signed_pct=2.0, volume_spike_ratio=None
    )
    # volume_spike_ratio เป็น 1.0 (neutral) ต่ำกว่าเกณฑ์ liquidation cascade proxy (>=2.0) -> ไม่ trigger
    assert result.pattern == NONE_PATTERN


def test_long_squeeze_takes_priority_check_order_does_not_double_trigger_cascade():
    # ราคาขึ้นแรงมาก + OI พุ่ง (ไม่ยุบ) + funding แพงฝั่ง long -> ต้องเป็น long_squeeze_risk ไม่ใช่ cascade
    # (cascade ต้องการ oi_change ติดลบมาก ซึ่งเคสนี้ oi_change เป็นบวก ไม่เข้าเงื่อนไข cascade อยู่แล้ว)
    result = classify_combination_pattern(
        price_return_24h_pct=6.0, oi_change_24h_pct=15.0, funding_annualized_signed_pct=25.0, volume_spike_ratio=3.0
    )
    assert result.pattern == LONG_SQUEEZE_RISK

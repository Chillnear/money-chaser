from __future__ import annotations

import math

from src.data.regime import classify_regime, classify_trend, classify_vol


def test_classify_trend_up_when_strong_adx_and_positive_gap():
    assert classify_trend(30.0, {50: 5.0}) == "trend_up"


def test_classify_trend_down_when_strong_adx_and_negative_gap():
    assert classify_trend(30.0, {50: -5.0}) == "trend_down"


def test_classify_trend_chop_when_weak_adx():
    assert classify_trend(10.0, {50: 5.0}) == "chop"


def test_classify_trend_chop_when_adx_missing():
    assert classify_trend(None, {50: 5.0}) == "chop"


def test_classify_trend_chop_when_adx_nan():
    assert classify_trend(float("nan"), {50: 5.0}) == "chop"


def test_classify_trend_falls_back_to_shorter_ema_gap():
    assert classify_trend(30.0, {21: -2.0}) == "trend_down"
    assert classify_trend(30.0, {9: 1.0}) == "trend_up"


def test_classify_trend_chop_when_no_gap_available():
    assert classify_trend(30.0, {}) == "chop"


def test_classify_vol_low():
    assert classify_vol(0.1) == "vol_low"


def test_classify_vol_mid():
    assert classify_vol(0.5) == "vol_mid"


def test_classify_vol_high():
    assert classify_vol(0.9) == "vol_high"


def test_classify_vol_boundaries():
    assert classify_vol(0.33) == "vol_mid"  # ไม่ < 0.33 พอดี
    assert classify_vol(0.66) == "vol_mid"  # ไม่ > 0.66 พอดี


def test_classify_vol_unknown_defaults_to_mid():
    assert classify_vol(None) == "vol_mid"
    assert classify_vol(float("nan")) == "vol_mid"


def test_classify_regime_full_combo():
    features = {"adx": 30.0, "ema_gap_pct": {50: 3.0}, "vol_percentile_1y": 0.9}
    result = classify_regime(features)
    assert result == {"trend": "trend_up", "vol": "vol_high", "tag": "trend_up_vol_high"}


def test_classify_regime_chop_low_vol():
    features = {"adx": 5.0, "ema_gap_pct": {50: 1.0}, "vol_percentile_1y": 0.05}
    result = classify_regime(features)
    assert result == {"trend": "chop", "vol": "vol_low", "tag": "chop_vol_low"}


def test_classify_regime_missing_fields_defaults_safely():
    result = classify_regime({})
    assert result["trend"] == "chop"
    assert result["vol"] == "vol_mid"

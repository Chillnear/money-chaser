"""
Golden tests สำหรับ src/data/features.py — ใช้ sequence ที่รู้คำตอบล่วงหน้า (constant, monotonic)
เพื่อตรึงค่าไว้กันสูตรเพี้ยนเงียบๆ ตามที่ BUILD-SPEC.md ข้อ 3 กำหนด
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data import features as F


def _make_candles(closes: list[float], spread: float = 2.0) -> list[dict]:
    """สร้าง candle สังเคราะห์: high = close+spread/2, low = close-spread/2, open = close ก่อนหน้า"""
    candles = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        candles.append(
            {
                "t": 1_600_000_000_000 + i * 86_400_000,
                "T": 1_600_000_000_000 + (i + 1) * 86_400_000,
                "o": o,
                "h": c + spread / 2,
                "l": c - spread / 2,
                "c": c,
                "v": 1000.0,
            }
        )
    return candles


def test_to_ohlcv_df_sorts_and_types():
    candles = _make_candles([100, 101, 102])
    df = F.to_ohlcv_df(candles)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [100, 101, 102]
    assert df["close"].dtype == float


def test_to_ohlcv_df_empty():
    df = F.to_ohlcv_df([])
    assert df.empty


def test_ema_of_constant_series_equals_constant():
    close = pd.Series([50.0] * 40)
    result = F.ema(close, 21)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_rsi_monotonic_increasing_approaches_100():
    close = pd.Series([100 + i for i in range(40)], dtype=float)
    result = F.rsi(close, 14)
    assert result.iloc[-1] > 99.0


def test_rsi_monotonic_decreasing_approaches_0():
    close = pd.Series([200 - i for i in range(40)], dtype=float)
    result = F.rsi(close, 14)
    assert result.iloc[-1] < 1.0


def test_atr_converges_to_constant_true_range():
    # spread คงที่ 2.0 ทุกแท่ง ไม่มี gap -> true range = high-low = 2.0 เสมอหลัง warmup
    candles = _make_candles([100.0] * 40, spread=2.0)
    df = F.to_ohlcv_df(candles)
    result = F.atr(df, 14)
    assert result.iloc[-1] == pytest.approx(2.0, abs=0.01)


def test_donchian_position_monotonic_increasing_is_near_one():
    # closes 110..129 ในหน้าต่าง 20 แท่งสุดท้าย, high=c+0.5, low=c-0.5
    # highest=129.5, lowest=109.5, close ล่าสุด=129 -> (129-109.5)/(129.5-109.5)=0.975
    candles = _make_candles([100 + i for i in range(30)], spread=1.0)
    df = F.to_ohlcv_df(candles)
    pos = F.donchian_position(df, 20)
    assert pos == pytest.approx(0.975, abs=0.01)


def test_donchian_position_flat_series_is_half():
    candles = _make_candles([100.0] * 25, spread=0.0)
    df = F.to_ohlcv_df(candles)
    pos = F.donchian_position(df, 20)
    assert pos == 0.5


def test_bollinger_bandwidth_zero_for_constant_series():
    close = pd.Series([100.0] * 25)
    bw = F.bollinger_bandwidth(close, 20)
    assert bw == pytest.approx(0.0, abs=1e-9)


def test_zscore_zero_for_constant_series():
    close = pd.Series([100.0] * 25)
    z = F.zscore(close, 20)
    assert z == 0.0


def test_zscore_positive_when_latest_above_mean():
    close = pd.Series([100.0] * 19 + [110.0])
    z = F.zscore(close, 20)
    assert z > 0


def test_consecutive_direction_count_up_streak():
    close = pd.Series([100, 101, 102, 103, 104], dtype=float)
    assert F.consecutive_direction_count(close) == 4


def test_consecutive_direction_count_down_streak():
    close = pd.Series([104, 103, 102], dtype=float)
    assert F.consecutive_direction_count(close) == -2


def test_consecutive_direction_count_breaks_on_reversal():
    close = pd.Series([100, 101, 102, 101], dtype=float)
    assert F.consecutive_direction_count(close) == -1


def test_return_pct_known_value():
    close = pd.Series([100.0] * 6 + [110.0])
    r = F.return_pct(close, 1)
    assert r == pytest.approx(10.0)


def test_return_pct_nan_when_not_enough_history():
    close = pd.Series([100.0, 101.0])
    assert pd.isna(F.return_pct(close, 30))


def test_distance_from_extreme():
    candles = _make_candles([100 + i for i in range(30)], spread=1.0)
    df = F.to_ohlcv_df(candles)
    dist = F.distance_from_extreme(df, 30)
    assert dist["pct_from_high"] == pytest.approx(0.0, abs=1.0)
    assert dist["pct_from_low"] > 0


def test_funding_features_basic():
    history = [{"time": i, "fundingRate": str(0.0001 * i)} for i in range(1, 10)]
    result = F.funding_features(history)
    assert result["current"] == pytest.approx(0.0009)
    assert result["avg_7d"] is not None
    assert 0.0 <= result["percentile"] <= 1.0


def test_funding_features_empty():
    result = F.funding_features([])
    assert result["current"] is None


def test_build_price_features_ok_with_enough_candles():
    candles = _make_candles([100 + i * 0.5 for i in range(60)], spread=1.5)
    config = {
        "ema_periods": [9, 21, 50],
        "adx_period": 14,
        "atr_period": 14,
        "rsi_period": 14,
        "donchian_period": 20,
        "return_windows_days": [1, 7, 30],
        "vol_lookback_days": 30,
        "vol_percentile_lookback_days": 365,
    }
    result = F.build_price_features(candles, config)
    assert result["ok"] is True
    assert "ema" in result and 9 in result["ema"]
    assert isinstance(result["rsi"], float)
    assert 0.0 <= result["donchian_position"] <= 1.0


def test_build_price_features_includes_volume_spike_ratio():
    candles = _make_candles([100 + i * 0.5 for i in range(60)], spread=1.5)
    config = {"return_windows_days": [1, 7, 30]}
    result = F.build_price_features(candles, config)
    assert result["ok"] is True
    assert "volume_spike_ratio" in result
    # ทุกแท่งมี volume คงที่ (1000.0) -> spike ratio ต้อง = 1.0 (ไม่มีความผิดปกติ)
    assert result["volume_spike_ratio"] == pytest.approx(1.0)


def _make_candles_with_volume(closes: list[float], volumes: list[float], spread: float = 2.0) -> list[dict]:
    candles = []
    for i, (c, v) in enumerate(zip(closes, volumes)):
        o = closes[i - 1] if i > 0 else c
        candles.append(
            {
                "t": 1_600_000_000_000 + i * 86_400_000,
                "T": 1_600_000_000_000 + (i + 1) * 86_400_000,
                "o": o, "h": c + spread / 2, "l": c - spread / 2, "c": c, "v": v,
            }
        )
    return candles


def test_volume_spike_ratio_detects_last_candle_spike():
    closes = [100.0] * 21
    volumes = [1000.0] * 20 + [5000.0]  # แท่งล่าสุดวอลุ่มพุ่ง 5x เทียบเฉลี่ยก่อนหน้า
    df = F.to_ohlcv_df(_make_candles_with_volume(closes, volumes))
    ratio = F.volume_spike_ratio(df, window=20)
    assert ratio == pytest.approx(5.0)


def test_volume_spike_ratio_nan_when_not_enough_candles():
    df = F.to_ohlcv_df(_make_candles_with_volume([100.0], [1000.0]))
    assert pd.isna(F.volume_spike_ratio(df))


def test_build_price_features_fails_gracefully_with_too_few_candles():
    candles = _make_candles([100, 101, 102])
    config = {}
    result = F.build_price_features(candles, config)
    assert result["ok"] is False
    assert "error" in result

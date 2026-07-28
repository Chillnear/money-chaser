from __future__ import annotations

from src.data.features import estimate_tokens, render_feature_table


def _pf(last_price=100.0):
    return {
        "ok": True,
        "last_price": last_price,
        "ema_gap_pct": {9: 1.2, 21: 2.5, 50: 4.0},
        "adx": 30.0,
        "atr_pct": 2.5,
        "rsi": 60.0,
        "returns_pct": {1: 0.5, 7: 5.0, 30: 15.0},
        "distance_from_30d_extreme": {"pct_from_high": -2.0, "pct_from_low": 20.0},
        "vol_percentile_1y": 0.6,
    }


def _shortlist_result(n_rest=0):
    shortlist = [
        {"coin": "BTC", "composite": 0.82, "trend_score": 0.9, "momentum_score": 0.8, "funding_score": 0.5, "vol_fit_score": 0.7},
        {"coin": "ETH", "composite": 0.65, "trend_score": 0.6, "momentum_score": 0.7, "funding_score": 0.5, "vol_fit_score": 0.6},
        {"coin": "SOL", "composite": 0.50, "trend_score": 0.4, "momentum_score": 0.5, "funding_score": 0.5, "vol_fit_score": 0.5},
    ]
    pinned_extra = {"coin": "PAXG", "composite": 0.30, "trend_score": 0.2, "momentum_score": 0.1, "funding_score": 0.5, "vol_fit_score": 0.6}
    rest_summary = [{"coin": f"ALT{i}", "composite": 0.1 * i} for i in range(n_rest)]
    return {"shortlist": shortlist, "pinned_extra": pinned_extra, "rest_summary": rest_summary, "pool_size": 4 + n_rest}


def test_render_feature_table_includes_all_candidates():
    result = render_feature_table(
        _shortlist_result(),
        price_features_by_coin={"BTC": _pf(), "ETH": _pf(), "SOL": _pf(), "PAXG": _pf(50.0)},
        regime_by_coin={"BTC": {"tag": "trend_up_vol_mid"}},
    )
    md = result["markdown"]
    assert "BTC" in md and "ETH" in md and "SOL" in md and "PAXG" in md
    assert "trend_up_vol_mid" in md


def test_render_feature_table_includes_macro_and_sentiment():
    result = render_feature_table(
        _shortlist_result(),
        price_features_by_coin={"BTC": _pf(), "ETH": _pf(), "SOL": _pf(), "PAXG": _pf()},
        regime_by_coin={},
        sentiment={"ok": True, "value": 70, "classification": "Greed", "delta_7d": 5},
        macro_snapshot={"dxy": {"ok": True, "last_close": 104.2, "change_1d_pct": 0.3}},
        news_headline_titles=["BTC breaks resistance", "Fed signals rate cut"],
    )
    md = result["markdown"]
    assert "Fear & Greed" in md
    assert "DXY=104.20" in md
    assert "BTC breaks resistance" in md


def test_render_feature_table_includes_oi_change_and_combination_read():
    pf = _pf()
    pf["oi_change_24h_pct"] = 12.5
    pf["oi_change_7d_pct"] = 3.0
    pf["volume_spike_ratio"] = 1.8
    pf["combination_pattern_label"] = "long_squeeze_risk — ราคาขึ้น+OI พุ่ง+funding แพงฝั่ง long พร้อมกัน"

    result = render_feature_table(
        _shortlist_result(),
        price_features_by_coin={"BTC": pf, "ETH": _pf(), "SOL": _pf(), "PAXG": _pf()},
        regime_by_coin={},
    )
    md = result["markdown"]
    assert "OI change 24h/7d" in md
    assert "12.50" in md
    assert "Combination read" in md
    assert "long_squeeze_risk" in md


def test_render_feature_table_handles_missing_combination_read_gracefully():
    result = render_feature_table(
        _shortlist_result(),
        price_features_by_coin={"BTC": _pf(), "ETH": _pf(), "SOL": _pf(), "PAXG": _pf()},
        regime_by_coin={},
    )
    md = result["markdown"]
    assert "ไม่มีข้อมูล" in md  # pf ไม่มี combination_pattern_label -> ต้อง fallback ไม่ crash


def test_render_feature_table_rest_summary_is_compact_one_liner():
    result = render_feature_table(
        _shortlist_result(n_rest=15),
        price_features_by_coin={"BTC": _pf(), "ETH": _pf(), "SOL": _pf(), "PAXG": _pf()},
        regime_by_coin={},
    )
    md = result["markdown"]
    assert "ALT0" in md and "ALT14" in md
    # ตลาดที่เหลือต้องไม่มี "### " (heading เต็ม) ต่อรายการ มีแค่บรรทัดเดียวรวม
    rest_section = md.split("ตลาดอื่นในพูล")[1]
    assert rest_section.count("###") == 0


def test_render_feature_table_stays_within_token_budget_for_realistic_pool():
    result = render_feature_table(
        _shortlist_result(n_rest=20),
        price_features_by_coin={"BTC": _pf(), "ETH": _pf(), "SOL": _pf(), "PAXG": _pf()},
        regime_by_coin={"BTC": {"tag": "trend_up_vol_mid"}, "ETH": {"tag": "chop_vol_low"}},
        sentiment={"ok": True, "value": 55, "classification": "Neutral", "delta_7d": -3},
        macro_snapshot={
            "dxy": {"ok": True, "last_close": 104.2, "change_1d_pct": 0.3},
            "xauusd": {"ok": True, "last_close": 2400.0, "change_1d_pct": -0.5},
        },
        news_headline_titles=["Headline " + str(i) for i in range(10)],
        token_cap=3000,
    )
    assert result["within_budget"] is True
    assert result["estimated_tokens"] < 3000


def test_estimate_tokens_roughly_scales_with_length():
    short = estimate_tokens("hello world")
    long = estimate_tokens("hello world " * 100)
    assert long > short * 50

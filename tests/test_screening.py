from __future__ import annotations

from src.data.screening import build_shortlist, compute_composite_scores, filter_universe


def _snapshot(coin, day_volume_usd, open_interest_usd, funding=0.0001, mark_px=100.0):
    return {
        "coin": coin,
        "funding": funding,
        "open_interest_usd": open_interest_usd,
        "day_volume_usd": day_volume_usd,
        "mark_px": mark_px,
        "prev_day_px": mark_px,
    }


def _pf(ok=True, ema21=105, ema50=100, adx=30.0, return_7d=5.0, vol_percentile=0.5):
    return {
        "ok": ok,
        "ema": {21: ema21, 50: ema50},
        "adx": adx,
        "returns_pct": {1: 1.0, 7: return_7d, 30: 10.0},
        "vol_percentile_1y": vol_percentile,
    }


def test_filter_universe_drops_thin_markets():
    snapshot = [
        _snapshot("BTC", day_volume_usd=100_000_000, open_interest_usd=50_000_000),
        _snapshot("SHITCOIN", day_volume_usd=1_000, open_interest_usd=500),
    ]
    filtered = filter_universe(snapshot, min_24h_volume_usd=20_000_000, min_open_interest_usd=5_000_000, always_include=[])
    coins = {s["coin"] for s in filtered}
    assert coins == {"BTC"}


def test_filter_universe_pins_always_include_even_if_below_threshold():
    snapshot = [
        _snapshot("BTC", day_volume_usd=100_000_000, open_interest_usd=50_000_000),
        _snapshot("PAXG", day_volume_usd=500_000, open_interest_usd=200_000),  # ต่ำกว่า threshold มาก
    ]
    filtered = filter_universe(snapshot, min_24h_volume_usd=20_000_000, min_open_interest_usd=5_000_000, always_include=["PAXG"])
    coins = {s["coin"] for s in filtered}
    assert coins == {"BTC", "PAXG"}


def test_filter_universe_missing_always_include_coin_does_not_crash():
    snapshot = [_snapshot("BTC", day_volume_usd=100_000_000, open_interest_usd=50_000_000)]
    filtered = filter_universe(snapshot, min_24h_volume_usd=20_000_000, min_open_interest_usd=5_000_000, always_include=["PAXG"])
    assert {s["coin"] for s in filtered} == {"BTC"}


def test_compute_composite_scores_ranks_strong_trend_higher():
    features = {
        "STRONG": _pf(ema21=120, ema50=100, adx=40.0, return_7d=20.0, vol_percentile=0.5),
        "WEAK": _pf(ema21=101, ema50=100, adx=5.0, return_7d=0.5, vol_percentile=0.5),
    }
    funding = {"STRONG": 0.0001, "WEAK": 0.0001}
    scores = compute_composite_scores(features, funding)
    assert scores["STRONG"]["composite"] > scores["WEAK"]["composite"]


def test_compute_composite_scores_excludes_not_ok_features():
    features = {
        "GOOD": _pf(),
        "BAD": _pf(ok=False),
    }
    scores = compute_composite_scores(features, {"GOOD": 0.0001, "BAD": 0.0001})
    assert "BAD" not in scores
    assert "GOOD" in scores


def test_compute_composite_scores_handles_missing_vol_percentile():
    features = {"BTC": _pf(vol_percentile=None)}
    scores = compute_composite_scores(features, {"BTC": 0.0001})
    assert scores["BTC"]["vol_fit_score"] == 0.5


def test_build_shortlist_returns_top_n_and_pins_paxg_as_extra():
    universe_snapshot = [
        _snapshot("BTC", 100_000_000, 50_000_000),
        _snapshot("ETH", 90_000_000, 40_000_000),
        _snapshot("SOL", 80_000_000, 30_000_000),
        _snapshot("AVAX", 70_000_000, 20_000_000),
        _snapshot("PAXG", 500_000, 200_000),  # ต่ำกว่า threshold แต่ pin ไว้
    ]
    price_features = {
        "BTC": _pf(ema21=130, ema50=100, adx=40, return_7d=25),
        "ETH": _pf(ema21=110, ema50=100, adx=20, return_7d=8),
        "SOL": _pf(ema21=105, ema50=100, adx=15, return_7d=3),
        "AVAX": _pf(ema21=101, ema50=100, adx=5, return_7d=0.2),
        "PAXG": _pf(ema21=100.5, ema50=100, adx=8, return_7d=0.5),
    }
    config = {
        "min_24h_volume_usd": 20_000_000,
        "min_open_interest_usd": 5_000_000,
        "always_include": ["PAXG"],
        "screening_shortlist_size": 3,
    }

    result = build_shortlist(universe_snapshot, price_features, config)

    assert len(result["shortlist"]) == 3
    assert result["shortlist"][0]["coin"] == "BTC"  # เทรนด์แรงสุด ควรมาอันดับ 1
    assert result["pinned_extra"] is not None
    assert result["pinned_extra"]["coin"] == "PAXG"
    assert result["pool_size"] == 5

    rest_coins = {r["coin"] for r in result["rest_summary"]}
    assert "PAXG" not in rest_coins  # ไม่ควรซ้ำกับ pinned_extra
    assert "BTC" not in rest_coins  # อยู่ใน shortlist แล้ว


def test_build_shortlist_paxg_in_top3_has_no_duplicate_pinned_extra():
    universe_snapshot = [
        _snapshot("BTC", 100_000_000, 50_000_000),
        _snapshot("PAXG", 30_000_000, 10_000_000),  # ผ่าน threshold เอง คราวนี้
    ]
    price_features = {
        "BTC": _pf(ema21=101, ema50=100, adx=5, return_7d=0.1),
        "PAXG": _pf(ema21=130, ema50=100, adx=40, return_7d=20),  # แรงกว่า BTC มาก
    }
    config = {
        "min_24h_volume_usd": 20_000_000,
        "min_open_interest_usd": 5_000_000,
        "always_include": ["PAXG"],
        "screening_shortlist_size": 3,
    }

    result = build_shortlist(universe_snapshot, price_features, config)

    shortlist_coins = {s["coin"] for s in result["shortlist"]}
    assert "PAXG" in shortlist_coins
    assert result["pinned_extra"] is None  # ติด shortlist หลักแล้ว ไม่ต้องมี pinned_extra ซ้ำ

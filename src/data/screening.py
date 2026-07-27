"""
Universe pool + composite score + top-3 shortlist ตาม BUILD-SPEC.md ข้อ 3b

แนวคิด: สแกนตลาดทั้งหมดที่ liquid พอด้วยโค้ด (ถูก เร็ว) แล้วคัดเหลือ top 3 ให้ analyst คิดลึก
(แพงกว่าเพราะเป็น LLM call แต่คุ้มเพราะคิดแค่ตัวที่ผ่านกรองแล้ว) — ไม่ส่งทั้ง 15-20 ตลาดเข้า prompt ตรงๆ
เพราะต้นทุน token จะพุ่งและ analyst จะคิดตื้นลงทุกตัวแทนที่จะคิดลึกเฉพาะตัวที่มีโอกาสจริง

หมายเหตุการ implement: สูตรใน BUILD-SPEC.md เขียน trend_score เป็นค่าตรง (%gap x ADX_normalized)
ซึ่งหน่วยไม่ตรงกับ momentum_score/funding_score ที่เป็น percentile (0-1) — ในนี้ทำให้สอดคล้องกันโดย
percentile-rank ค่า trend_score ดิบภายในพูลก่อนนำไปรวมถ่วงน้ำหนัก (แนวคิดเดียวกัน แค่ปรับสเกลให้ยุติธรรม
ระหว่าง sub-score ไม่ให้ตัวใดตัวหนึ่ง dominate เพราะหน่วยต่างกัน)
"""
from __future__ import annotations

import math

import pandas as pd

WEIGHTS = {"trend": 0.35, "momentum": 0.25, "funding": 0.20, "vol_fit": 0.20}
ADX_NORMALIZE_CAP = 50.0


def filter_universe(
    universe_snapshot: list[dict],
    min_24h_volume_usd: float,
    min_open_interest_usd: float,
    always_include: list[str],
) -> list[dict]:
    """กรองตลาดที่ liquid พอ (กัน HIP-3/ตลาดบางที่เสี่ยง flash crash) + pin always_include เสมอ
    (เช่น PAXG ทองดิจิทัลที่ผู้ใช้ต้องการเห็นแน่นอน แม้ volume จะต่ำกว่า threshold เล็กน้อย)
    """
    filtered = [
        s
        for s in universe_snapshot
        if s.get("day_volume_usd", 0) >= min_24h_volume_usd
        and s.get("open_interest_usd", 0) >= min_open_interest_usd
    ]
    filtered_coins = {s["coin"] for s in filtered}

    for coin in always_include:
        if coin in filtered_coins:
            continue
        entry = next((s for s in universe_snapshot if s["coin"] == coin), None)
        if entry is not None:
            filtered.append(entry)
            filtered_coins.add(coin)

    return filtered


def _percentile_rank_map(values: dict[str, float]) -> dict[str, float]:
    """percentile rank 0-1 ของแต่ละค่าเทียบเพื่อนในพูลเดียวกัน (NaN ถูกตัดออกจากการจัดอันดับ ได้ 0.5 แทน)"""
    clean = {k: v for k, v in values.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
    if not clean:
        return {k: 0.5 for k in values}
    series = pd.Series(clean)
    ranks = series.rank(pct=True)
    result = ranks.to_dict()
    for k in values:
        if k not in result:
            result[k] = 0.5  # ข้อมูลขาด -> ให้คะแนนกลางๆ ไม่เอนเอียง
    return result


def compute_composite_scores(price_features_by_coin: dict[str, dict], current_funding_by_coin: dict[str, float]) -> dict[str, dict]:
    """คืน {coin: {trend_score, momentum_score, funding_score, vol_fit_score, composite}}
    เฉพาะ coin ที่มี price_features['ok'] เป็น True เท่านั้น (ตัดที่ข้อมูลไม่พอออกก่อน)
    """
    valid_coins = [c for c, f in price_features_by_coin.items() if f.get("ok")]

    raw_trend = {}
    raw_momentum = {}
    for coin in valid_coins:
        pf = price_features_by_coin[coin]
        ema = pf.get("ema", {})
        adx = pf.get("adx", 0.0) or 0.0
        if 21 in ema and 50 in ema and ema[50]:
            ema_gap_pct = abs((ema[21] - ema[50]) / ema[50] * 100)
        else:
            ema_gap_pct = 0.0
        adx_normalized = min(adx / ADX_NORMALIZE_CAP, 1.0) if adx and not math.isnan(adx) else 0.0
        raw_trend[coin] = ema_gap_pct * adx_normalized

        return_7d = pf.get("returns_pct", {}).get(7)
        raw_momentum[coin] = abs(return_7d) if return_7d is not None and not math.isnan(return_7d) else 0.0

    trend_pct = _percentile_rank_map(raw_trend)
    momentum_pct = _percentile_rank_map(raw_momentum)
    funding_pct = _percentile_rank_map({c: current_funding_by_coin.get(c, 0.0) for c in valid_coins})

    scores = {}
    for coin in valid_coins:
        pf = price_features_by_coin[coin]
        vol_percentile = pf.get("vol_percentile_1y")
        if vol_percentile is None or (isinstance(vol_percentile, float) and math.isnan(vol_percentile)):
            vol_fit_score = 0.5
        else:
            vol_fit_score = 1.0 - abs(vol_percentile - 0.5) * 2.0

        trend_score = trend_pct.get(coin, 0.5)
        momentum_score = momentum_pct.get(coin, 0.5)
        funding_score = funding_pct.get(coin, 0.5)

        composite = (
            WEIGHTS["trend"] * trend_score
            + WEIGHTS["momentum"] * momentum_score
            + WEIGHTS["funding"] * funding_score
            + WEIGHTS["vol_fit"] * vol_fit_score
        )

        scores[coin] = {
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "funding_score": funding_score,
            "vol_fit_score": vol_fit_score,
            "composite": composite,
        }

    return scores


def build_shortlist(
    universe_snapshot: list[dict],
    price_features_by_coin: dict[str, dict],
    config: dict,
) -> dict:
    """ผลลัพธ์หลักของ screening — เรียกจาก main.py ขั้น 5b ตาม BUILD-SPEC.md

    คืน:
      shortlist: list ของ dict {coin, composite, ...} เรียงคะแนนมาก->น้อย ความยาว = screening_shortlist_size
      pinned_extra: coin ที่อยู่ใน always_include แต่ไม่ติด shortlist หลัก (ส่งเป็นตัวเลือกที่ 4 แบบย่อ)
      rest_summary: list ของ {coin, composite} สำหรับตลาดที่เหลือ (โชว์แค่ชื่อ+คะแนน ไม่ส่ง feature เต็ม)
    """
    always_include = config.get("always_include", [])
    filtered = filter_universe(
        universe_snapshot,
        config.get("min_24h_volume_usd", 20_000_000),
        config.get("min_open_interest_usd", 5_000_000),
        always_include,
    )
    filtered_coins = {s["coin"] for s in filtered}

    current_funding_by_coin = {s["coin"]: s.get("funding", 0.0) for s in universe_snapshot if s["coin"] in filtered_coins}
    relevant_features = {c: f for c, f in price_features_by_coin.items() if c in filtered_coins}

    scores = compute_composite_scores(relevant_features, current_funding_by_coin)

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["composite"], reverse=True)
    shortlist_size = config.get("screening_shortlist_size", 3)
    shortlist = [{"coin": coin, **score} for coin, score in ranked[:shortlist_size]]
    shortlist_coins = {item["coin"] for item in shortlist}

    pinned_extra = None
    for coin in always_include:
        if coin not in shortlist_coins and coin in scores:
            pinned_extra = {"coin": coin, **scores[coin]}
            break

    rest_summary = [
        {"coin": coin, "composite": score["composite"]}
        for coin, score in ranked[shortlist_size:]
        if coin != (pinned_extra["coin"] if pinned_extra else None)
    ]

    return {
        "shortlist": shortlist,
        "pinned_extra": pinned_extra,
        "rest_summary": rest_summary,
        "pool_size": len(filtered),
    }

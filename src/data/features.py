"""
คำนวณ indicator ทั้งหมดตาม BUILD-SPEC.md ข้อ 3 — pure pandas ล้วนๆ ห้าม LLM แตะเลขในนี้เด็ดขาด
(non-negotiable ข้อ 2: LLM ไม่คำนวณเลข ได้รับแค่ตัวเลขสำเร็จรูปจากไฟล์นี้ไปตีความ)

ทุกฟังก์ชันคำนวณเดี่ยว (ema, rsi, atr, adx, ...) เป็น pure function รับ/คืน pandas Series หรือ float
เพื่อให้ unit test ตรึงค่าไว้ได้ตรงๆ (golden test) — ดู tests/test_features.py
"""
from __future__ import annotations

import pandas as pd


def to_ohlcv_df(candles: list[dict]) -> pd.DataFrame:
    """แปลง candle ดิบจาก hl_market.get_candles() เป็น DataFrame เรียงเวลาเก่า->ใหม่
    คอลัมน์: open, high, low, close, volume (float) index = timestamp เปิดแท่ง (ms)
    """
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(candles)
    df = df.rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.sort_values("ts").set_index("ts")
    return df[["open", "high", "low", "close", "volume"]]


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, 1e-12)
    return 100 - (100 / (1 + rs))


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return atr(df, period) / df["close"] * 100


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — วัดความแรงของเทรนด์ (ไม่บอกทิศทาง)"""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]

    tr = true_range(df)
    atr_smoothed = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_smoothed.replace(0.0, 1e-12)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_smoothed.replace(0.0, 1e-12)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def donchian_position(df: pd.DataFrame, period: int = 20) -> float:
    """ตำแหน่งราคาปัจจุบันในกรอบ Donchian ล่าสุด (0 = ชนขอบล่าง, 1 = ชนขอบบน)"""
    window = df.tail(period)
    highest = window["high"].max()
    lowest = window["low"].min()
    close = df["close"].iloc[-1]
    if highest == lowest:
        return 0.5
    return float((close - lowest) / (highest - lowest))


def realized_vol(close: pd.Series, window: int, annualize_days: int = 365) -> float:
    """realized volatility (annualized %) จาก log return ย้อนหลัง window วัน"""
    log_returns = (close / close.shift(1)).apply(lambda x: pd.NA if x <= 0 else x)
    log_returns = log_returns.dropna().apply(lambda x: __import__("math").log(x))
    recent = log_returns.tail(window)
    if len(recent) < 2:
        return float("nan")
    return float(recent.std() * (annualize_days**0.5) * 100)


def vol_percentile(close: pd.Series, window: int, lookback_days: int) -> float:
    """เปอร์เซ็นไทล์ของ realized vol ปัจจุบันเทียบกับช่วง lookback_days ที่ผ่านมา (0-1)"""
    vols = []
    for i in range(window, len(close)):
        vols.append(realized_vol(close.iloc[max(0, i - lookback_days) : i + 1], window))
    if not vols:
        return float("nan")
    current = vols[-1]
    series = pd.Series(vols).dropna()
    if series.empty or pd.isna(current):
        return float("nan")
    return float((series <= current).mean())


def bollinger_bandwidth(close: pd.Series, period: int = 20, num_std: float = 2.0) -> float:
    window = close.tail(period)
    mean = window.mean()
    std = window.std()
    if mean == 0 or pd.isna(std):
        return float("nan")
    upper = mean + num_std * std
    lower = mean - num_std * std
    return float((upper - lower) / mean * 100)


def return_pct(close: pd.Series, days: int) -> float:
    if len(close) <= days:
        return float("nan")
    past = close.iloc[-(days + 1)]
    now = close.iloc[-1]
    if past == 0:
        return float("nan")
    return float((now - past) / past * 100)


def distance_from_extreme(df: pd.DataFrame, window: int = 30) -> dict:
    recent = df.tail(window)
    close = df["close"].iloc[-1]
    high = recent["high"].max()
    low = recent["low"].min()
    return {
        "pct_from_high": float((close - high) / high * 100) if high else float("nan"),
        "pct_from_low": float((close - low) / low * 100) if low else float("nan"),
    }


def volume_spike_ratio(df: pd.DataFrame, window: int = 20) -> float:
    """ปริมาณเทรดของแท่งล่าสุด เทียบค่าเฉลี่ย window แท่งก่อนหน้า (ไม่รวมแท่งล่าสุดเอง) — ยิ่งสูงกว่า 1
    มาก ยิ่งแสดงว่าวันนี้มีการซื้อขายผิดปกติ ใช้เป็น 1 ใน 3 สัญญาณของ liquidation cascade proxy
    (P5.3 — ดู src/data/combo_signals.py) คืน NaN ถ้าข้อมูลไม่พอคำนวณ (fail-safe ไม่ใช่ 0 หรือ 1)
    """
    if len(df) < 2:
        return float("nan")
    recent_window = df["volume"].iloc[-(window + 1) : -1]
    avg_volume = recent_window.mean() if len(recent_window) > 0 else float("nan")
    last_volume = df["volume"].iloc[-1]
    if not avg_volume or pd.isna(avg_volume) or avg_volume == 0:
        return float("nan")
    return float(last_volume / avg_volume)


def zscore(series: pd.Series, window: int = 20) -> float:
    recent = series.tail(window)
    std = recent.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float((series.iloc[-1] - recent.mean()) / std)


def consecutive_direction_count(close: pd.Series) -> int:
    """จำนวนแท่งติดกันล่าสุดที่ไปทางเดียว (บวก = ขึ้นติดกัน, ลบ = ลงติดกัน)"""
    diffs = close.diff().dropna()
    if diffs.empty:
        return 0
    direction = 1 if diffs.iloc[-1] > 0 else (-1 if diffs.iloc[-1] < 0 else 0)
    if direction == 0:
        return 0
    count = 0
    for d in reversed(diffs.tolist()):
        d_dir = 1 if d > 0 else (-1 if d < 0 else 0)
        if d_dir == direction:
            count += 1
        else:
            break
    return count * direction


def funding_features(funding_history: list[dict]) -> dict:
    """current, average 7d, percentile ของ funding rate — funding_history มาจาก
    hl_market.get_funding_history() เรียงเวลาเก่า->ใหม่ (ตามที่ Hyperliquid ส่งกลับ)
    """
    if not funding_history:
        return {"current": None, "avg_7d": None, "percentile": None}

    rates = [float(f["fundingRate"]) for f in funding_history]
    current = rates[-1]
    last_7d = rates[-7 * 3:] if len(rates) >= 3 else rates  # funding เก็บทุก ~8 ชม. บน Hyperliquid ~3 ครั้ง/วัน
    avg_7d = sum(last_7d) / len(last_7d)
    series = pd.Series(rates)
    percentile = float((series <= current).mean())
    return {"current": current, "avg_7d": avg_7d, "percentile": percentile}


def build_price_features(candles: list[dict], config: dict) -> dict:
    """รวม indicator ราคา/เทรนด์/ผันผวน/mean-reversion ทั้งหมดสำหรับ 1 ตลาด เป็น dict แบน (flat)
    พร้อม serialize ลง journal และแปลงเป็นตาราง prompt ต่อได้ (render_feature_table ใน features.py เช่นกัน)
    """
    df = to_ohlcv_df(candles)
    if len(df) < 30:
        return {"ok": False, "error": f"มีแท่งเทียนแค่ {len(df)} แท่ง ไม่พอคำนวณ indicator (ต้องการอย่างน้อย 30)"}

    close = df["close"]
    ema_periods = config.get("ema_periods", [9, 21, 50, 200])
    ema_values = {p: float(ema(close, p).iloc[-1]) for p in ema_periods if len(close) >= p}
    last_price = float(close.iloc[-1])

    result = {
        "ok": True,
        "last_price": last_price,
        "ema": ema_values,
        "ema_gap_pct": {
            p: (last_price - v) / v * 100 if v else float("nan") for p, v in ema_values.items()
        },
        "adx": float(adx(df, config.get("adx_period", 14)).iloc[-1]),
        "atr_pct": float(atr_pct(df, config.get("atr_period", 14)).iloc[-1]),
        "rsi": float(rsi(close, config.get("rsi_period", 14)).iloc[-1]),
        "donchian_position": donchian_position(df, config.get("donchian_period", 20)),
        "zscore_vs_ema20": zscore(close, 20) if len(close) >= 20 else float("nan"),
        "consecutive_direction": consecutive_direction_count(close),
        "bollinger_bandwidth_pct": bollinger_bandwidth(close, 20),
        "returns_pct": {d: return_pct(close, d) for d in config.get("return_windows_days", [1, 7, 30])},
        "distance_from_30d_extreme": distance_from_extreme(df, 30),
        "realized_vol_pct": {
            "7d": realized_vol(close, 7),
            "30d": realized_vol(close, config.get("vol_lookback_days", 30)),
        },
        "vol_percentile_1y": vol_percentile(
            close, config.get("vol_lookback_days", 30), config.get("vol_percentile_lookback_days", 365)
        ),
        "volume_spike_ratio": volume_spike_ratio(df, config.get("volume_spike_window", 20)),
    }
    return result


def estimate_tokens(text: str) -> int:
    """ประมาณจำนวน token แบบหยาบๆ (~4 ตัวอักษรต่อ 1 token สำหรับข้อความผสมไทย/อังกฤษ)
    ใช้เช็ค token budget ก่อนส่งเข้า prompt จริง (ไม่ใช่ tokenizer ที่แม่นยำ 100% แต่พอเพียงสำหรับเช็คเพดาน)
    """
    return max(1, len(text) // 4)


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float) and (value != value):  # NaN check ไม่ import math ซ้ำ
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_feature_table(
    shortlist_result: dict,
    price_features_by_coin: dict,
    regime_by_coin: dict,
    sentiment: dict | None = None,
    macro_snapshot: dict | None = None,
    news_headline_titles: list[str] | None = None,
    token_cap: int = 3000,
) -> dict:
    """แปลงผลจาก screening.build_shortlist() + features ของแต่ละเหรียญ เป็นตาราง markdown compact
    สำหรับใส่ prompt ของ analyst/judge (ข้อ 3, 3b ของ BUILD-SPEC.md)

    ส่งเฉพาะ feature เต็มของ candidate ใน shortlist + pinned_extra เท่านั้น ตลาดที่เหลือ (rest_summary)
    โชว์แค่ 1 บรรทัด (ชื่อ + composite score) กันไม่ให้ token บวมเมื่อพูลมีตลาดเยอะ

    คืน {"markdown": str, "estimated_tokens": int, "within_budget": bool}
    """
    lines: list[str] = []

    if macro_snapshot:
        macro_bits = []
        for name in ("dxy", "xauusd", "spx", "us10y"):
            series = macro_snapshot.get(name, {})
            if series.get("ok"):
                macro_bits.append(f"{name.upper()}={_fmt(series['last_close'])} ({_fmt(series['change_1d_pct'])}%)")
        if macro_bits:
            lines.append(f"**มหภาค:** {' | '.join(macro_bits)}")

    if sentiment and sentiment.get("ok"):
        lines.append(
            f"**Fear & Greed:** {sentiment['value']} ({sentiment['classification']}), "
            f"เปลี่ยนจาก 7 วันก่อน: {_fmt(sentiment.get('delta_7d'), 0)}"
        )

    if news_headline_titles:
        top_news = news_headline_titles[:5]
        lines.append("**ข่าวล่าสุด 24 ชม.:** " + " / ".join(top_news))

    lines.append("")
    lines.append("## ผู้เข้าชิงที่ต้องวิเคราะห์ลึก")

    candidates = list(shortlist_result.get("shortlist", []))
    if shortlist_result.get("pinned_extra"):
        candidates.append(shortlist_result["pinned_extra"])

    for item in candidates:
        coin = item["coin"]
        pf = price_features_by_coin.get(coin, {})
        regime = regime_by_coin.get(coin, {})
        lines.append(f"\n### {coin} (composite score: {_fmt(item['composite'], 3)})")
        lines.append(f"- ราคาล่าสุด: {_fmt(pf.get('last_price'))}, regime: {regime.get('tag', 'unknown')}")
        lines.append(
            f"- EMA gap%: {', '.join(f'{p}={_fmt(g)}' for p, g in pf.get('ema_gap_pct', {}).items())}"
        )
        lines.append(f"- ADX: {_fmt(pf.get('adx'))}, ATR%: {_fmt(pf.get('atr_pct'))}, RSI: {_fmt(pf.get('rsi'))}")
        lines.append(
            f"- Return 1d/7d/30d: {_fmt(pf.get('returns_pct', {}).get(1))}% / "
            f"{_fmt(pf.get('returns_pct', {}).get(7))}% / {_fmt(pf.get('returns_pct', {}).get(30))}%"
        )
        dist = pf.get("distance_from_30d_extreme", {})
        lines.append(
            f"- ห่างจากจุดสูงสุด/ต่ำสุด 30 วัน: {_fmt(dist.get('pct_from_high'))}% / {_fmt(dist.get('pct_from_low'))}%"
        )
        lines.append(
            f"- Donchian position: {_fmt(item.get('trend_score'), 3)}, Vol percentile 1y: {_fmt(pf.get('vol_percentile_1y'), 3)}"
        )
        lines.append(
            f"- Sub-scores: trend={_fmt(item.get('trend_score'), 3)}, momentum={_fmt(item.get('momentum_score'), 3)}, "
            f"funding={_fmt(item.get('funding_score'), 3)}, vol_fit={_fmt(item.get('vol_fit_score'), 3)}"
        )
        lines.append(
            f"- OI change 24h/7d: {_fmt(pf.get('oi_change_24h_pct'))}% / {_fmt(pf.get('oi_change_7d_pct'))}%, "
            f"Volume spike ratio: {_fmt(pf.get('volume_spike_ratio'))}"
        )
        lines.append(f"- **Combination read:** {pf.get('combination_pattern_label', 'ไม่มีข้อมูล')}")

    rest_summary = shortlist_result.get("rest_summary", [])
    if rest_summary:
        lines.append("\n## ตลาดอื่นในพูล (ดูภาพรวมเฉยๆ ไม่ต้องวิเคราะห์ลึก)")
        rest_line = ", ".join(f"{r['coin']}={_fmt(r['composite'], 3)}" for r in rest_summary)
        lines.append(rest_line)

    markdown = "\n".join(lines)
    tokens = estimate_tokens(markdown)
    return {"markdown": markdown, "estimated_tokens": tokens, "within_budget": tokens <= token_cap}

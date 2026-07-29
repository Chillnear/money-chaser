"""
กลยุทธ์ deterministic เพิ่มเติม (ไม่มี AI) นอกจาก src/baseline.py (ซึ่งเป็น trend-following ล้วนๆ)

ที่มา: scripts/rule_backtest.py (P5.9) ทดสอบไอเดียเหล่านี้กับข้อมูลย้อนหลัง 2 ปี พบว่า funding_carry
ให้ผลบวกทั้งช่วง 2 ปีและ 180 วันล่าสุด ขณะที่ trend-following (ใกล้เคียงแนวที่ AI จริงเอนเอียงไปทาง) กลับ
ขาดทุนหนักช่วงหลัง — ดูรายละเอียดที่ scripts/rule_backtest.py และ src/shadow.py (ตัวที่เอา funding_carry
ไปรันคู่ AI จริงทุกวันแบบ shadow ไม่กระทบเงินจริง)

แยกไฟล์นี้จาก scripts/rule_backtest.py เพื่อให้ทั้ง scripts/rule_backtest.py (backtest ย้อนอดีต) และ
src/shadow.py (รันคู่ของจริงทุกวัน) import ฟังก์ชันเดียวกันได้ ไม่ต้องเขียนกฎซ้ำสองที่จนวันหนึ่งเผลอทำให้
สองที่ตัดสินใจไม่ตรงกัน
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    action: str  # "long" | "short" | "flat"
    asset: str | None
    reasoning: str


def decide_mean_reversion(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot) -> Decision:
    """สมมติฐานตรงข้ามกับ trend-following: ราคาชนขอบ Donchian สุดขั้ว + RSI สุดขั้ว มักจะเด้งกลับ
    มากกว่าจะวิ่งต่อ (fade breakout/breakdown แทนการไล่ตาม) — ตัวอย่างจาก rule_backtest ยังน้อย (12 ไม้ใน
    180 วันล่าสุด) จึงยังไม่เอามาทำ shadow tracker แบบ funding_carry จนกว่าจะมีข้อมูลมากกว่านี้
    """
    if not shortlist:
        return Decision("flat", None, "ไม่มีผู้เข้าชิงใน shortlist")

    top = max(shortlist, key=lambda item: item.get("composite", 0.0))
    coin = top["coin"]
    pf = price_features_by_coin.get(coin, {})
    donchian = pf.get("donchian_position")
    rsi = pf.get("rsi")

    if donchian is None or rsi is None or donchian != donchian or rsi != rsi:  # NaN check
        return Decision("flat", None, f"{coin} ข้อมูล Donchian/RSI ไม่พอคำนวณ")

    if donchian >= 0.9 and rsi >= 60:
        return Decision("short", coin, f"{coin} ชนขอบบน Donchian ({donchian:.2f}) + RSI {rsi:.1f} overbought -> fade ลง")
    if donchian <= 0.1 and rsi <= 40:
        return Decision("long", coin, f"{coin} ชนขอบล่าง Donchian ({donchian:.2f}) + RSI {rsi:.1f} oversold -> fade ขึ้น")
    return Decision("flat", None, f"{coin} ยังไม่สุดขั้วพอ (donchian={donchian:.2f}, rsi={rsi:.1f})")


def decide_funding_carry(shortlist, regime_by_coin, price_features_by_coin, universe_snapshot) -> Decision:
    """เก็บ funding แทนการเดาทิศทางราคา: เข้าฝั่งตรงข้ามกับ funding ที่สุดขั้วที่สุดในชอร์ตลิสต์
    (funding เป็นบวกมาก = long จ่าย short มาก -> เข้า short เก็บ funding, และกลับกัน)

    ผลจาก scripts/rule_backtest.py: 2 ปี = 139 ไม้ +1.53 USD, 180 วันล่าสุด = 32 ไม้ +2.67 USD ชนะ 62.5%
    """
    if not shortlist:
        return Decision("flat", None, "ไม่มีผู้เข้าชิงใน shortlist")

    top = max(shortlist, key=lambda item: abs(item.get("funding_score", 0.5) - 0.5))
    coin = top["coin"]
    funding_score = top.get("funding_score", 0.5)

    if abs(funding_score - 0.5) < 0.4:  # ต้องสุดขั้วจริงๆ (percentile ใกล้ 0 หรือ 1 ในพูล) ไม่ใช่กลางๆ
        return Decision("flat", None, f"{coin} funding percentile {funding_score:.2f} ยังไม่สุดขั้วพอ")

    current_funding = next((e.get("funding", 0.0) for e in universe_snapshot if e["coin"] == coin), 0.0)
    if current_funding > 0:
        return Decision("short", coin, f"{coin} funding={current_funding:.6f} บวกสุดขั้ว (percentile {funding_score:.2f}) -> short เก็บ funding")
    if current_funding < 0:
        return Decision("long", coin, f"{coin} funding={current_funding:.6f} ลบสุดขั้ว (percentile {funding_score:.2f}) -> long เก็บ funding")
    return Decision("flat", None, f"{coin} funding=0 ไม่มีอะไรให้เก็บ")

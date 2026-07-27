"""
Interface กลางของ broker — broker_paper.py และ broker_hl.py (P6) implement ตามนี้
ให้ main.py เรียกใช้แบบเดียวกันไม่ว่า MODE จะเป็น paper หรือ live (BUILD-SPEC.md ข้อ 6)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Position:
    asset: str
    side: str  # "long" | "short"
    notional_usd: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    opened_at_ts: float


@dataclass
class ClosedTrade:
    asset: str
    side: str
    notional_usd: float
    entry_price: float
    exit_price: float
    opened_at_ts: float
    closed_at_ts: float
    pnl_usd: float
    fee_usd: float
    exit_reason: str


class BrokerBase(ABC):
    @abstractmethod
    def open_position(
        self,
        asset: str,
        side: str,
        notional_usd: float,
        mid_price: float,
        stop_pct: float,
        take_profit_pct: float,
        now_ts: float,
    ) -> Position:
        """เปิดไม้ + ฝากออเดอร์ SL/TP จริง (broker_hl.py) หรือจำลอง (broker_paper.py)"""

    @abstractmethod
    def close_position(self, position: Position, exit_price: float, now_ts: float, reason: str) -> ClosedTrade:
        """ปิดไม้ที่ราคาที่กำหนด (จริงจาก exchange หรือจำลอง)"""

    @abstractmethod
    def get_account_equity(self) -> float:
        """equity ปัจจุบัน (paper: ตามบัญชีจำลอง, live: query จาก Hyperliquid clearinghouseState)"""

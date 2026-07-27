"""
Paper broker — จำลองการเทรดด้วยราคาจริง (จาก Hyperliquid) + fee/slippage ที่สมจริง
ตาม BUILD-SPEC.md ข้อ 6: "ทั้งสองโหมดใช้ราคาจริง feature จริง... ต่างกันแค่คลาส broker"
และ "จำลอง SL/TP เป็น trigger check ทุกวันด้วยราคา high/low ของแท่งนั้น" (TASKS.md ข้อ 2.4)

ห้ามตัดค่า fee/slippage ออกแม้จะดูเหมือนทำให้ผลลัพธ์แย่ลง — ถ้าไม่คิดสองอย่างนี้ paper mode จะ
หลอกตัวเองว่าระบบแม่นกว่าความจริง (ข้อ 7.3 ของ BUILD-SPEC.md: "Paper สวยเกินจริง" เป็นกับดักที่พบบ่อย)
"""
from __future__ import annotations

from src.execution.broker_base import BrokerBase, ClosedTrade, Position
from src.risk.exit_rules import ExitDecision, classify_exit


class PaperBroker(BrokerBase):
    def __init__(self, starting_equity_usd: float, taker_fee_pct: float, slippage_pct: float):
        self.equity = starting_equity_usd
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct

    def _entry_fill_price(self, side: str, mid_price: float) -> float:
        slip = self.slippage_pct / 100
        # long เปิดด้วยการซื้อ -> ราคาแย่กว่าคือสูงกว่า mid; short เปิดด้วยการขาย -> แย่กว่าคือต่ำกว่า mid
        return mid_price * (1 + slip) if side == "long" else mid_price * (1 - slip)

    def _exit_fill_price(self, side: str, target_price: float) -> float:
        slip = self.slippage_pct / 100
        # long ปิดด้วยการขาย -> แย่กว่าคือต่ำกว่าราคาที่ควรได้; short ปิดด้วยการซื้อ -> แย่กว่าคือสูงกว่า
        return target_price * (1 - slip) if side == "long" else target_price * (1 + slip)

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
        entry_price = self._entry_fill_price(side, mid_price)

        if side == "long":
            stop_price = entry_price * (1 - stop_pct / 100)
            take_profit_price = entry_price * (1 + take_profit_pct / 100)
        else:
            stop_price = entry_price * (1 + stop_pct / 100)
            take_profit_price = entry_price * (1 - take_profit_pct / 100)

        return Position(
            asset=asset,
            side=side,
            notional_usd=notional_usd,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            opened_at_ts=now_ts,
        )

    def check_candle_trigger(self, position: Position, candle_high: float, candle_low: float) -> tuple[bool, bool]:
        """คืน (sl_hit, tp_hit) จากราคา high/low ของแท่งเทียนวันนั้น (จำลองออเดอร์ trigger จริง)"""
        if position.side == "long":
            sl_hit = candle_low <= position.stop_price
            tp_hit = candle_high >= position.take_profit_price
        else:
            sl_hit = candle_high >= position.stop_price
            tp_hit = candle_low <= position.take_profit_price
        return sl_hit, tp_hit

    def evaluate_exit(
        self,
        position: Position,
        candle_high: float,
        candle_low: float,
        now_ts: float,
        max_holding_days: int,
        invalidation_triggered: bool = False,
    ) -> ExitDecision:
        sl_hit, tp_hit = self.check_candle_trigger(position, candle_high, candle_low)
        return classify_exit(
            sl_hit=sl_hit,
            tp_hit=tp_hit,
            opened_at_ts=position.opened_at_ts,
            now_ts=now_ts,
            max_holding_days=max_holding_days,
            invalidation_triggered=invalidation_triggered,
        )

    def close_position(self, position: Position, exit_price: float, now_ts: float, reason: str) -> ClosedTrade:
        exit_fill = self._exit_fill_price(position.side, exit_price)
        qty = position.notional_usd / position.entry_price

        if position.side == "long":
            gross_pnl = qty * (exit_fill - position.entry_price)
        else:
            gross_pnl = qty * (position.entry_price - exit_fill)

        entry_fee = position.notional_usd * self.taker_fee_pct / 100
        exit_notional = qty * exit_fill
        exit_fee = exit_notional * self.taker_fee_pct / 100
        total_fee = entry_fee + exit_fee

        pnl_usd = gross_pnl - total_fee
        self.equity += pnl_usd

        return ClosedTrade(
            asset=position.asset,
            side=position.side,
            notional_usd=position.notional_usd,
            entry_price=position.entry_price,
            exit_price=exit_fill,
            opened_at_ts=position.opened_at_ts,
            closed_at_ts=now_ts,
            pnl_usd=pnl_usd,
            fee_usd=total_fee,
            exit_reason=reason,
        )

    def get_account_equity(self) -> float:
        return self.equity

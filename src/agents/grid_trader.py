"""
Grid Trading Agent — วางออเดอร์ grid เพื่อกำไรจากผลกระเบิดของความผันแปร

ไม่ใช้ AI — ใช้ rules ล้วน:
- ถ้า volatility สูง (>1.5%) → เปิด grid
- ถ้า position ได้ profit >30% → ปิด (harvest)
- ถ้า volatility ตาย (<0.5%) → ปิด (ไม่มีเงิน)
- Else: ปล่อย grid จับกำไร

Grid ตัวอย่าง (BTC @ 45,000):
  Price range: 42,750 (down 5%) to 47,250 (up 5%)

  Buy orders:
    42,750 → buy $6
    43,500 → buy $6
    44,250 → buy $6
    45,000 → buy $6

  Sell orders:
    45,750 → sell $6
    46,500 → sell $6
    47,250 → sell $6

  ถ้าราคาขึ้นลง → orders fill → เก็บ spread = profit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

logger = logging.getLogger(__name__)

GridAction = Literal["open", "hold", "rebalance", "close"]


class GridOrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class GridOrder:
    """Single pending order in the grid"""

    price: float
    size_usd: float
    side: GridOrderSide
    filled: bool = False


@dataclass
class GridPosition:
    """Current active grid"""

    symbol: str
    center_price: float  # price when grid was created
    grid_width_pct: float  # e.g., 5.0 = ±5%
    num_levels: int  # e.g., 5 levels
    total_capital: float
    orders: list[GridOrder] = field(default_factory=list)
    filled_volume_usd: float = 0.0  # how much has been executed
    accumulated_pnl: float = 0.0  # profit from completed buy-sell cycles
    entry_time: str = ""  # ISO 8601
    quote_cash_usd: float = 0.0
    base_qty: float = 0.0
    last_price: float = 0.0
    last_processed_ts: int = 0


@dataclass
class GridDecision:
    action: GridAction
    symbol: str | None  # if opening
    grid_width_pct: float | None
    num_levels: int | None
    total_capital_usd: float | None
    reason: str
    volatility_24h: float | None


class GridTradingAgent:
    """
    Deterministic grid trading agent.

    Configuration:
    - MIN_VOLATILITY_OPEN: 1.5% (open if daily volatility > 1.5%)
    - MAX_VOLATILITY_CLOSE: 0.5% (close if volatility < 0.5% for 2+ days)
    - GRID_WIDTH_PCT: 5.0 (grid spans ±5% from center)
    - NUM_LEVELS: 5 (5 buy levels + 5 sell levels)
    - HARVEST_TARGET_PROFIT_PCT: 30 (close if made 30%)
    """

    def __init__(
        self,
        min_volatility_open: float = 1.5,
        max_volatility_close: float = 0.5,
        grid_width_pct: float = 5.0,
        num_levels: int = 5,
        harvest_target_profit_pct: float = 30.0,
    ):
        self.min_volatility_open = min_volatility_open
        self.max_volatility_close = max_volatility_close
        self.grid_width_pct = grid_width_pct
        self.num_levels = num_levels
        self.harvest_target_profit_pct = harvest_target_profit_pct
        self.low_volatility_days_count = 0

    def decide(
        self,
        current_position: GridPosition | None,
        current_price: float,
        volatility_24h: float,  # e.g., 3.5 = 3.5% daily volatility
        available_capital_usd: float = 0.0,
        risk_multiplier: float = 1.0,
    ) -> GridDecision:
        """
        Main decision logic for grid trading.

        Args:
            current_position: Current grid or None if no position
            current_price: Spot price (e.g., BTC price now)
            volatility_24h: Expected 24h volatility as percentage (e.g., 3.5)
            available_capital_usd: Capital available to open new grid
            risk_multiplier: 0-1, reduces grid size if account under stress

        Returns:
            GridDecision with action and parameters
        """

        # 1. Check if we should close current grid
        if current_position is not None:
            close_reason = self._should_close_grid(current_position, volatility_24h, current_price)
            if close_reason:
                return GridDecision(
                    action="close",
                    symbol=current_position.symbol,
                    grid_width_pct=None,
                    num_levels=None,
                    total_capital_usd=None,
                    reason=close_reason,
                    volatility_24h=volatility_24h,
                )

        # 2. If no position, check if we should open
        if current_position is None:
            open_decision = self._should_open_grid(
                current_price, volatility_24h, available_capital_usd, risk_multiplier
            )
            if open_decision:
                return open_decision

        # 3. Default: hold (do nothing)
        return GridDecision(
            action="hold",
            symbol=current_position.symbol if current_position else None,
            grid_width_pct=None,
            num_levels=None,
            total_capital_usd=None,
            reason=f"Volatility {volatility_24h:.2f}% insufficient or no position; waiting",
            volatility_24h=volatility_24h,
        )

    def _should_close_grid(
        self, pos: GridPosition, vol_24h: float, current_price: float
    ) -> str | None:
        """
        Returns reason if should close, None if should keep grid open.
        """

        # Check 1: Harvest target hit (30% profit collected)
        if pos.accumulated_pnl > 0:
            pnl_pct = (pos.accumulated_pnl / pos.total_capital) * 100
            if pnl_pct >= self.harvest_target_profit_pct:
                return f"Harvest target hit ({pnl_pct:.1f}% profit collected, capital can redeploy)"

        # Check 2: Volatility died (too boring to trade)
        if vol_24h < self.max_volatility_close:
            self.low_volatility_days_count += 1
            if self.low_volatility_days_count >= 2:
                return f"Volatility too low ({vol_24h:.2f}%) for 2+ days, grid inefficient, close"
        else:
            self.low_volatility_days_count = 0

        # Check 3: Grid drifted too far (price moved > 2x grid width, grid is useless)
        grid_lower = pos.center_price * (1 - pos.grid_width_pct / 100)
        grid_upper = pos.center_price * (1 + pos.grid_width_pct / 100)
        if current_price < grid_lower * 0.95 or current_price > grid_upper * 1.05:
            return f"Price drifted beyond grid (grid center {pos.center_price}, current {current_price}), inefficient, rebalance"

        return None

    def _should_open_grid(
        self, current_price: float, vol_24h: float, capital: float, risk_mult: float
    ) -> GridDecision | None:
        """
        Returns GridDecision if should open, None if should wait.
        """

        # Check 1: Volatility high enough
        if vol_24h < self.min_volatility_open:
            return None  # Not volatile enough

        # Check 2: Sufficient capital
        grid_capital = capital * 0.9 * risk_mult  # Use 90% of available
        if grid_capital < 10:  # Minimum $10 to grid
            return None

        return GridDecision(
            action="open",
            symbol="BTCUSDT",  # Hardcode for now, can parameterize later
            grid_width_pct=self.grid_width_pct,
            num_levels=self.num_levels,
            total_capital_usd=grid_capital,
            reason=f"Volatility {vol_24h:.2f}% > threshold {self.min_volatility_open}%. Grid width ±{self.grid_width_pct}%, {self.num_levels} levels, capital ${grid_capital:.0f}",
            volatility_24h=vol_24h,
        )


def initialize_grid_position(
    symbol: str,
    center_price: float,
    grid_width_pct: float,
    num_levels: int,
    total_capital_usd: float,
    entry_time: str,
    last_processed_ts: int = 0,
) -> GridPosition:
    """เปิด spot grid แบบ fully-funded: ครึ่งหนึ่งเป็น quote cash และอีกครึ่งเป็น base inventory."""
    if center_price <= 0 or total_capital_usd <= 0:
        raise ValueError("center_price และ total_capital_usd ต้องมากกว่า 0")
    orders = generate_grid_orders(center_price, grid_width_pct, num_levels, total_capital_usd)
    # กัน quote/base ไว้จ่าย execution cost ไม่ให้ fully-funded grid ติดเพราะ order สุดท้ายขาด fee ไม่กี่เซนต์
    for order in orders:
        order.size_usd *= 0.99
    return GridPosition(
        symbol=symbol,
        center_price=center_price,
        grid_width_pct=grid_width_pct,
        num_levels=num_levels,
        total_capital=total_capital_usd,
        orders=orders,
        entry_time=entry_time,
        quote_cash_usd=total_capital_usd / 2,
        base_qty=(total_capital_usd / 2) / center_price,
        last_price=center_price,
        last_processed_ts=last_processed_ts,
    )


def grid_position_from_dict(raw: dict) -> GridPosition:
    """Deserialize state โดยแปลง order dict กลับเป็น dataclass/enum ให้ครบ."""
    payload = dict(raw)
    payload["orders"] = [
        GridOrder(
            price=float(o["price"]),
            size_usd=float(o["size_usd"]),
            side=GridOrderSide(o["side"]),
            filled=bool(o.get("filled", False)),
        )
        for o in payload.get("orders", [])
    ]
    return GridPosition(**payload)


def mark_grid_to_market(position: GridPosition, price: float) -> float:
    return position.quote_cash_usd + position.base_qty * price


def close_grid_position(position: GridPosition, price: float, fee_per_leg_pct: float) -> float:
    """Liquidate base inventory ที่ราคาปัจจุบันและคืน cash หลังต้นทุน execution."""
    base_notional = position.base_qty * price
    liquidation_cost = base_notional * fee_per_leg_pct / 100
    cash = position.quote_cash_usd + base_notional - liquidation_cost
    position.quote_cash_usd = cash
    position.base_qty = 0.0
    position.last_price = price
    position.accumulated_pnl = cash - position.total_capital
    return cash


def apply_intraday_grid_candles(
    position: GridPosition,
    candles: list[dict],
    fee_per_leg_pct: float,
) -> list[dict]:
    """จำลอง limit fills จากแท่ง intraday ที่ปิดแล้ว โดยไม่เดาลำดับราคาในแท่ง.

    Order ที่มีอยู่ตอนเปิดแท่งและถูกช่วง low-high คร่อมจะ fill ได้ครั้งเดียว ส่วน order ฝั่งตรงข้าม
    ที่สร้างหลัง fill จะเริ่ม active ในแท่งถัดไปเท่านั้น จึงไม่สร้างกำไรหลายรอบจาก OHLC แท่งเดียว.
    """
    fills: list[dict] = []
    step = position.center_price * position.grid_width_pct / 100 / position.num_levels
    tolerance = max(position.center_price, 1.0) * 1e-10

    for candle in sorted(candles, key=lambda c: int(c.get("t", c.get("T", 0)))):
        candle_ts = int(candle.get("T", candle.get("t", 0)) or 0)
        if candle_ts <= position.last_processed_ts:
            continue
        low, high, close = float(candle["l"]), float(candle["h"]), float(candle["c"])
        active_at_open = [o for o in position.orders if not o.filled]
        new_orders: list[GridOrder] = []

        for order in active_at_open:
            if not (low <= order.price <= high):
                continue
            fee_usd = order.size_usd * fee_per_leg_pct / 100
            if order.side == GridOrderSide.BUY:
                total_cost = order.size_usd + fee_usd
                if position.quote_cash_usd + tolerance < total_cost:
                    continue
                position.quote_cash_usd -= total_cost
                position.base_qty += order.size_usd / order.price
                counterpart_side = GridOrderSide.SELL
                counterpart_price = order.price + step
            else:
                qty = order.size_usd / order.price
                if position.base_qty + tolerance < qty:
                    continue
                position.base_qty -= qty
                position.quote_cash_usd += order.size_usd - fee_usd
                counterpart_side = GridOrderSide.BUY
                counterpart_price = order.price - step

            order.filled = True
            position.filled_volume_usd += order.size_usd
            fills.append(
                {
                    "ts": candle_ts,
                    "side": order.side.value,
                    "price": order.price,
                    "size_usd": order.size_usd,
                    "fee_usd": fee_usd,
                }
            )
            lower = position.center_price * (1 - position.grid_width_pct / 100)
            upper = position.center_price * (1 + position.grid_width_pct / 100)
            if lower - tolerance <= counterpart_price <= upper + tolerance:
                duplicate = any(
                    not existing.filled
                    and existing.side == counterpart_side
                    and abs(existing.price - counterpart_price) <= tolerance
                    for existing in position.orders + new_orders
                )
                if not duplicate:
                    new_orders.append(
                        GridOrder(counterpart_price, order.size_usd, counterpart_side, filled=False)
                    )

        position.orders = [o for o in position.orders if not o.filled] + new_orders
        position.last_price = close
        position.last_processed_ts = candle_ts
        position.accumulated_pnl = mark_grid_to_market(position, close) - position.total_capital

    return fills


def generate_grid_orders(
    center_price: float, grid_width_pct: float, num_levels: int, total_capital_usd: float
) -> list[GridOrder]:
    """
    Generate buy and sell grid orders.

    Args:
        center_price: Center price (e.g., 45000 for BTC)
        grid_width_pct: Grid width as % (e.g., 5.0 = ±5%)
        num_levels: Number of levels per side (e.g., 5)
        total_capital_usd: Total capital to distribute (e.g., $30)

    Returns:
        List of GridOrder (buy + sell orders)

    Example:
        center = 45000, width = 5%, levels = 5
        → buy at 42750, 43500, 44250, 45000, 45750
        → sell at 45750, 46500, 47250, 48000, 48750
        → each order size = $30 / (5 * 2) = $3 per order
    """

    orders = []
    size_per_order = total_capital_usd / (num_levels * 2)  # Split between buy + sell

    lower_price = center_price * (1 - grid_width_pct / 100)
    upper_price = center_price * (1 + grid_width_pct / 100)

    # Buy ต่ำกว่า center และ sell สูงกว่า center อย่างละ num_levels; ทุนครึ่งต่อฝั่ง
    for i in range(num_levels):
        price = lower_price + (center_price - lower_price) * (i / num_levels)
        orders.append(GridOrder(price=price, size_usd=size_per_order, side=GridOrderSide.BUY, filled=False))

    # Generate sell orders (ascending price, starting above center)
    for i in range(1, num_levels + 1):
        price = center_price + (upper_price - center_price) * (i / num_levels)
        orders.append(GridOrder(price=price, size_usd=size_per_order, side=GridOrderSide.SELL, filled=False))

    return orders


def estimate_grid_return(
    volatility_daily_pct: float,
    grid_width_pct: float = 5.0,
    num_levels: int = 5,
    fee_per_trade_pct: float = 0.1,  # taker fee 0.1%
) -> dict:
    """
    Rough estimate of grid trading return based on volatility.

    Logic:
    - Grid spans ±grid_width_pct
    - In 1 full cycle (down then up), you complete 1 buy-sell pair
    - Profit per cycle = grid_width_pct * 2 (down 5% buy, up 5% sell)
    - Fee per cycle = fee_per_trade_pct * 2
    - Cycles per day = volatility_daily_pct / grid_width_pct (rough)

    Args:
        volatility_daily_pct: Daily volatility e.g. 3.5%
        grid_width_pct: Grid width e.g. 5%
        num_levels: Levels per side e.g. 5
        fee_per_trade_pct: Trading fee per order e.g. 0.1%

    Returns:
        dict with estimated daily/monthly return percentages
    """

    if volatility_daily_pct < grid_width_pct:
        # Not enough volatility to complete even 1 cycle
        return {"daily_pct": 0, "monthly_pct": 0, "note": "Insufficient volatility"}

    # Rough: cycles per day — bug found: เดิมหาร grid_width_pct*2 ทำให้ที่ vol==width (เงื่อนไข early-
    # return ด้านบนถือว่า "พอสำหรับ 1 cycle") กลับคำนวณได้ cycles_per_day=0.5 ขัดแย้งกันเอง แก้ให้ตรงกับ
    # threshold ของ early-return (vol < width -> ไม่พอ 1 cycle)
    cycles_per_day = volatility_daily_pct / grid_width_pct

    # Per-cycle profit (before fee)
    profit_per_cycle_pct = grid_width_pct * 2  # buy at bottom, sell at top

    # Per-cycle fee (buy + sell)
    fee_per_cycle_pct = fee_per_trade_pct * 2 * num_levels  # each level gets both sides

    # Net per cycle
    net_per_cycle_pct = profit_per_cycle_pct - fee_per_cycle_pct

    daily_pct = net_per_cycle_pct * cycles_per_day
    monthly_pct = daily_pct * 30

    return {
        "daily_pct": round(daily_pct, 2),
        "monthly_pct": round(monthly_pct, 2),
        "cycles_per_day": round(cycles_per_day, 2),
        "note": f"Assume {volatility_daily_pct}% daily vol, {grid_width_pct}% grid, {num_levels} levels",
    }

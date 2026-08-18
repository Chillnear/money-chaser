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


def apply_daily_grid_pnl(
    position: GridPosition,
    day_high: float,
    day_low: float,
    current_price: float,
    closes: list[float],
    fee_per_leg_pct: float,
    realized_pnl_usd: float,
) -> float:
    """คำนวณ P&L ของ grid position ใน 1 วัน แล้วอัปเดต position.accumulated_pnl ในที่ (in-place) คืนค่า
    realized_pnl_usd สะสมใหม่ (ต้องเก็บสถานะนี้แยกจาก position.accumulated_pnl เพราะ accumulated_pnl =
    realized - unrealized ไม่ใช่ realized เพียวๆ — ใครเรียกต้องเก็บ realized_pnl_usd ไว้ข้ามวันเอง แล้วรีเซ็ต
    เป็น 0 ทุกครั้งที่เปิด/ปิด position ใหม่)

    ใช้ร่วมกันทั้ง backtest (scripts/backtest_grid_farming.py) และ shadow tracker (src/shadow_grid.py)
    โดยตั้งใจ — เพื่อไม่ให้ตรรกะเพี้ยนไปคนละทางจนเทียบผล backtest กับ live shadow กันไม่ได้จริง

    บั๊กที่เจอและแก้แล้ว (2026-08-17, ดู docstring บนสุดของ scripts/backtest_grid_farming.py): เดิมนับ
    "cycle กำไร" จากแค่ช่วง high-low ของวันเทียบกรอบ grid โดยไม่สนทิศทาง ทำให้วันที่ราคาวิ่งทางเดียวยาวๆ
    (ไม่ได้ไป-กลับจริง) ก็นับเป็นกำไรเต็มเหมือนวันที่แกว่งไป-กลับจริง เลยไม่มีทางขาดทุนเลย (win rate 100%
    ทุกเหรียญ ทั้งที่เป็นไปไม่ได้จริง) แก้โดย (1) นับ cycle ได้เฉพาะวันที่ราคา "กลับทิศ" จากเมื่อวานจริง
    (ไม่ใช่วิ่งทางเดียวต่อเนื่อง) และ (2) mark-to-market ขาดทุนที่ยังไม่ realize เมื่อราคาหลุดขอบล่างของ grid
    ไปเรื่อยๆ ไม่กลับมา (ของที่ถืออยู่มีมูลค่าลดลงจริง ไม่ใช่แค่ประมาณการเฉยๆ)
    """
    lower = position.center_price * (1 - position.grid_width_pct / 100)
    upper = position.center_price * (1 + position.grid_width_pct / 100)
    overlap = max(0.0, min(day_high, upper) - max(day_low, lower))
    one_way_width = position.center_price * position.grid_width_pct / 100
    raw_cycles_today = min(overlap / (one_way_width * 2), 3.0) if one_way_width > 0 else 0.0

    is_reversal_day = True  # ข้อมูลไม่พอเทียบ (แท่งแรกๆ) -> fail-open ตามพฤติกรรมเดิม
    if len(closes) >= 3:
        change_today = closes[-1] - closes[-2]
        change_yesterday = closes[-2] - closes[-3]
        if change_yesterday != 0 and change_today != 0:
            is_reversal_day = (change_today > 0) != (change_yesterday > 0)
    cycles_today = raw_cycles_today if is_reversal_day else 0.0

    fee_per_cycle_pct = fee_per_leg_pct * 2  # เข้า 1 ครั้ง ออก 1 ครั้งต่อ cycle
    pnl_pct_today = cycles_today * (position.grid_width_pct * 2 - fee_per_cycle_pct)
    new_realized_pnl_usd = realized_pnl_usd + pnl_pct_today / 100 * position.total_capital

    unrealized_loss_usd = 0.0
    if current_price < lower:
        drawdown_pct = (lower - current_price) / lower * 100
        unrealized_loss_usd = drawdown_pct * 0.5 / 100 * position.total_capital

    position.accumulated_pnl = new_realized_pnl_usd - unrealized_loss_usd
    return new_realized_pnl_usd


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

    # Generate buy orders (ascending price, from lower bound up to center — must stay <= center,
    # bug found: เดิม interpolate ไปถึง upper_price ทำให้ order สุดท้ายทะลุขึ้นไปเหนือ center)
    for i in range(num_levels):
        price = lower_price + (center_price - lower_price) * (i / (num_levels - 1)) if num_levels > 1 else center_price
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

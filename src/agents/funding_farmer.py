"""
Funding Rate Farming Agent — ตัดสินใจเกี่ยวกับ delta-neutral long spot + short perp

ไม่ใช้ AI (LLM) — ใช้โค้ด deterministic เท่านั้น เพราะ:
1. Funding rate ชัดเจน (ตัวเลขไม่มี opinion)
2. Risk rules แข็งแกร่ง (ไม่ควร delegate ให้ LLM ตัดสินโครงสร้าง)
3. Execution ต้องสวย (ไม่มี hallucination)

Logic:
- Monitor funding rate every 8h cycle (3x per day)
- IF funding > threshold AND basis spread < limit → OPEN pair
- ELIF funding < 0.005% for 2+ days → CLOSE (rate died)
- ELIF position P&L > target → HARVEST & close
- ELSE: HOLD current position (do nothing)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


FarmingAction = Literal["open", "hold", "rebalance", "close", "harvest"]


@dataclass
class FundingRate:
    """Current funding info for a pair (e.g., BTCUSDT)"""

    symbol: str
    rate_per_8h: float  # e.g., 0.025 = 0.025% per 8 hours
    spot_price: float
    perp_price: float
    spot_liquidity_usd: float  # 24h volume or order book depth
    perp_liquidity_usd: float


@dataclass
class FarmingPosition:
    """Current delta-neutral position (if open)"""

    symbol: str
    long_notional_usd: float  # spot position size
    short_notional_usd: float  # perp short size
    entry_price: float
    entry_time: str  # ISO 8601
    accumulated_funding_usd: float  # total funding collected so far


@dataclass
class FarmingDecision:
    action: FarmingAction
    symbol: str | None  # e.g., "BTCUSDT", None if hold/close current
    long_notional_usd: float | None  # if opening, long size
    short_notional_usd: float | None  # if opening, short size
    reason: str
    funding_rate_per_8h: float | None  # actual rate used


class FundingFarmingAgent:
    """
    Deterministic agent for farming funding rates.

    Configuration (hardcoded, can move to config/*.yaml later):
    - MIN_FUNDING_RATE_ANNUAL: 0.10 (10% annualized, ~0.024% per 8h)
    - MAX_BASIS_SPREAD_PCT: 1.0 (if |perp_price - spot_price| / spot > 1%, skip)
    - MAX_POSITION_NOTIONAL: 0.8 * available_capital
    - MIN_POSITION_NOTIONAL: $20 (long $10 + short $10 minimum)
    - HARVEST_TARGET_PROFIT_PCT: 30 (if made 30% profit, close and take off table)
    - LOW_FUNDING_THRESHOLD: 0.005 (if rate < 0.005% per 8h, close after 2 days at low)
    """

    def __init__(
        self,
        min_funding_annual_pct: float = 10.0,  # 10% per year minimum
        max_basis_spread_pct: float = 1.0,
        harvest_target_profit_pct: float = 30.0,
        low_funding_threshold_pct: float = 0.005,
    ):
        # bug found: FundingRate.rate_per_8h เก็บเป็น "ตัวเลขเปอร์เซ็นต์" ตรงๆอยู่แล้ว (0.025 = 0.025%
        # ต่อ 8 ชม. ตามตัวอย่างใน docstring ของ FundingRate ไม่ใช่ decimal fraction) เดิมหาร /100 ซ้ำ
        # ทำให้ threshold เพี้ยนไป 100 เท่า (10% ต่อปี กลายเป็นเทียบเท่า ~0.0000913% ต่อ 8ชม. แทนที่จะเป็น
        # ~0.00913%) ทำให้ funding ต่ำๆที่ไม่ควรผ่านกลับผ่านเกณฑ์ไปเปิดไม้ได้
        self.min_funding_annual_pct = min_funding_annual_pct
        self.min_funding_per_8h = min_funding_annual_pct / (365 * 3)
        self.max_basis_spread_pct = max_basis_spread_pct
        self.harvest_target_profit_pct = harvest_target_profit_pct
        self.low_funding_threshold_pct = low_funding_threshold_pct
        self.low_funding_days_count = 0  # track consecutive days of low funding

    def decide(
        self,
        current_position: FarmingPosition | None,
        funding_rate: FundingRate,
        available_capital_usd: float,
        risk_multiplier: float = 1.0,  # from risk breaker (if drawn down, reduce size)
    ) -> FarmingDecision:
        """
        Main decision logic.

        Args:
            current_position: Current open position or None
            funding_rate: Latest funding info
            available_capital_usd: Usable capital (after reserves)
            risk_multiplier: 0-1, reduces position size if account under stress

        Returns:
            FarmingDecision with action + parameters
        """

        # 1. Check if we should close current position
        if current_position is not None:
            close_reason = self._should_close_position(
                current_position, funding_rate, available_capital_usd
            )
            if close_reason:
                return FarmingDecision(
                    action="close",
                    symbol=current_position.symbol,
                    long_notional_usd=None,
                    short_notional_usd=None,
                    reason=close_reason,
                    funding_rate_per_8h=None,
                )

        # 2. If no position, check if we should open one
        if current_position is None:
            open_decision = self._should_open_position(funding_rate, available_capital_usd, risk_multiplier)
            if open_decision:
                return open_decision

        # 3. Default: hold current position (or do nothing if no position)
        return FarmingDecision(
            action="hold",
            symbol=current_position.symbol if current_position else None,
            long_notional_usd=None,
            short_notional_usd=None,
            reason="Funding rate insufficient or no position; waiting",
            funding_rate_per_8h=funding_rate.rate_per_8h,
        )

    def _should_close_position(
        self, pos: FarmingPosition, rate: FundingRate, capital: float
    ) -> str | None:
        """
        Returns reason string if should close, None otherwise.
        """

        # Check 1: Harvest target hit (30% profit)
        if pos.accumulated_funding_usd > 0:
            pnl_pct = (pos.accumulated_funding_usd / (pos.long_notional_usd + pos.short_notional_usd)) * 100
            if pnl_pct >= self.harvest_target_profit_pct:
                return f"Harvest target hit ({pnl_pct:.1f}% profit collected)"

        # Check 2: Funding rate collapsed (< 0.005% per 8h)
        if rate.rate_per_8h < self.low_funding_threshold_pct:
            self.low_funding_days_count += 1
            if self.low_funding_days_count >= 2:
                return f"Funding rate too low ({rate.rate_per_8h:.4f}% per 8h) for 2+ cycles, closing"
        else:
            self.low_funding_days_count = 0

        # Check 3: Symbol mismatch (position in BTC, best opportunity in ETH)
        if pos.symbol != rate.symbol:
            # Decide whether to rotate or hold
            # For now: only rotate if rate is significantly better (2x)
            if rate.rate_per_8h > pos.accumulated_funding_usd * 2.0:  # heuristic
                return f"Better opportunity in {rate.symbol} ({rate.rate_per_8h:.4f}% vs {pos.symbol})"

        # Check 4: Basis diverged too much (unusual)
        basis_pct = abs(rate.perp_price - rate.spot_price) / rate.spot_price * 100
        if basis_pct > self.max_basis_spread_pct * 2:
            return f"Basis spread too large ({basis_pct:.2f}%), unwind to be safe"

        return None

    def _should_open_position(
        self, rate: FundingRate, capital: float, risk_mult: float
    ) -> FarmingDecision | None:
        """
        Returns FarmingDecision if should open, None if should wait.
        """

        # Check 1: Funding rate meets minimum threshold
        if rate.rate_per_8h < self.min_funding_per_8h:
            return None  # Too low, wait

        # Check 2: Basis spread within limits
        basis_pct = abs(rate.perp_price - rate.spot_price) / rate.spot_price * 100
        if basis_pct > self.max_basis_spread_pct:
            return None  # Spread too wide, risky

        # Check 3: Liquidity adequate (not strictly enforced, but logged)
        if rate.spot_liquidity_usd < 100_000 or rate.perp_liquidity_usd < 100_000:
            logger.warning(
                f"Low liquidity for {rate.symbol}: spot=${rate.spot_liquidity_usd}, "
                f"perp=${rate.perp_liquidity_usd}. Proceeding with caution."
            )

        # Compute position size
        max_notional = capital * 0.8 * risk_mult
        min_notional = 20  # $10 spot + $10 perp minimum
        position_notional = min(max_notional, capital * 0.5 * risk_mult)  # use 50% of capital if good

        if position_notional < min_notional:
            return None  # Not enough capital even for minimum

        return FarmingDecision(
            action="open",
            symbol=rate.symbol,
            long_notional_usd=position_notional / 2,
            short_notional_usd=position_notional / 2,
            reason=f"Funding {rate.rate_per_8h:.4f}% per 8h (annualized: {rate.rate_per_8h * 365 * 3:.1f}%), "
            f"basis {basis_pct:.2f}%, liquidity adequate",
            funding_rate_per_8h=rate.rate_per_8h,
        )


def estimate_annual_return(
    funding_per_8h: float,
    rebalance_cost_pct: float = 0.008,  # ~0.8% per year from daily/weekly rebalance
    liquidation_risk_pct: float = 0.005,  # 0.5% per year
) -> dict:
    """
    Back-of-napkin estimation of annual return after costs.

    Args:
        funding_per_8h: e.g., 0.025 = 0.025% per 8 hours
        rebalance_cost_pct: Expected annual cost from rebalancing
        liquidation_risk_pct: Insurance against smart contract / execution risk

    Returns:
        dict with gross, costs, net percentages
    """
    # bug found: หาร /100 ซ้ำเหมือนกันกับ min_funding_per_8h ด้านบน — funding_per_8h เป็นตัวเลข
    # เปอร์เซ็นต์อยู่แล้ว (0.025 = 0.025%) คูณจำนวนครั้งต่อปี (365*3) ก็ได้เปอร์เซ็นต์ต่อปีตรงๆ ไม่ต้องหารอีก
    gross = funding_per_8h * 365 * 3
    costs = rebalance_cost_pct + liquidation_risk_pct
    net = gross - costs

    return {
        "gross_annual_pct": round(gross, 2),
        # bug found: costs_pct มักเป็นเลขเล็ก (เช่น 0.013) round เหลือ 2 ตำแหน่งจะปัดเหลือ 0.01 หายความ
        # แม่นยำไปเลย ต้อง round ให้ละเอียดกว่า gross/net ที่เป็นเลขหลักสิบ
        "costs_pct": round(costs, 4),
        "net_annual_pct": round(net, 2),
    }

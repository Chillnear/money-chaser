"""
Portfolio Manager — Orchestrate 3 strategies (Directional + Farming + Grid).

Responsibilities:
1. Allocate capital to each strategy
2. Run all 3 agents and collect decisions
3. Validate against risk rules
4. Execute all approved decisions
5. Track combined P&L and state
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class StrategyAllocation:
    """Capital allocation per strategy"""

    directional_usd: float
    farming_usd: float
    grid_usd: float
    cash_buffer_usd: float

    @property
    def total(self) -> float:
        return self.directional_usd + self.farming_usd + self.grid_usd + self.cash_buffer_usd

    def validate(self, max_total: float) -> bool:
        """Check allocation doesn't exceed maximum."""
        return self.total <= max_total


@dataclass
class PortfolioState:
    """Combined state of all 3 strategies"""

    date: str
    total_equity_usd: float
    peak_equity_usd: float
    directional_position: dict | None
    farming_position: dict | None
    grid_position: dict | None
    total_pnl_usd: float
    total_pnl_pct: float
    daily_pnl_usd: float
    daily_pnl_pct: float


@dataclass
class DailyDecisions:
    """Decisions from all 3 agents for the day"""

    date: str
    directional_action: str
    directional_reason: str
    farming_action: str
    farming_reason: str
    grid_action: str
    grid_reason: str
    portfolio_action: str  # overall: proceed / halt / partial
    risk_alerts: list[str]


class PortfolioManager:
    """
    Coordinate 3 strategies, manage risk, track state.
    """

    def __init__(self, total_capital_usd: float = 100.0, min_buffer_usd: float = 5.0):
        self.total_capital = total_capital_usd
        self.min_buffer = min_buffer_usd
        self.allocation = None
        self.state = None
        self.daily_decisions = None

    def compute_allocation(
        self, directional_pct: float = 40, farming_pct: float = 30, grid_pct: float = 30
    ) -> StrategyAllocation:
        """
        Compute capital allocation based on percentages.

        Args:
            directional_pct: % of capital for directional trading
            farming_pct: % of capital for farming
            grid_pct: % of capital for grid trading

        Returns:
            StrategyAllocation with USD amounts
        """
        available = self.total_capital - self.min_buffer
        directional = available * (directional_pct / 100)
        farming = available * (farming_pct / 100)
        grid = available * (grid_pct / 100)

        allocation = StrategyAllocation(
            directional_usd=directional,
            farming_usd=farming,
            grid_usd=grid,
            cash_buffer_usd=self.min_buffer,
        )

        if not allocation.validate(self.total_capital):
            raise ValueError(f"Allocation exceeds total capital: {allocation.total} > {self.total_capital}")

        self.allocation = allocation
        logger.info(
            f"Allocation computed: Directional ${directional:.0f}, "
            f"Farming ${farming:.0f}, Grid ${grid:.0f}, Buffer ${self.min_buffer:.0f}"
        )
        return allocation

    def validate_combined_risk(
        self,
        directional_decision: dict,
        farming_decision: dict,
        grid_decision: dict,
        current_equity: float,
        daily_loss_limit_pct: float = 2.0,
    ) -> tuple[bool, list[str]]:
        """
        Validate that all 3 decisions together don't violate portfolio risk rules.

        Args:
            directional_decision: Action from DirectionalAgent
            farming_decision: Action from FundingFarmingAgent
            grid_decision: Action from GridTradingAgent
            current_equity: Current portfolio equity
            daily_loss_limit_pct: Max daily loss allowed

        Returns:
            (is_valid: bool, alerts: list[str])
        """
        alerts = []

        # Check 1: Total leverage across strategies
        estimated_leverage = 0.0
        if directional_decision.get("action") == "open" and directional_decision.get("leverage", 1.0) > 1:
            estimated_leverage += directional_decision.get("leverage", 1.0)
        if farming_decision.get("action") == "open":
            estimated_leverage += 1.0  # Farming is 1x by definition
        if grid_decision.get("action") == "open":
            estimated_leverage += 1.0  # Grid is 1x

        if estimated_leverage > 2.5:
            alerts.append(f"⚠️ Combined leverage {estimated_leverage:.1f}x is high")

        # Check 2: Same asset conflict (avoid directional + grid on same pair)
        if (
            directional_decision.get("action") == "open"
            and grid_decision.get("action") == "open"
            and directional_decision.get("symbol") == grid_decision.get("symbol")
        ):
            alerts.append("⚠️ Both directional + grid trading same symbol, high correlation")

        # Check 3: Daily loss limit (approximate)
        # If any strategy is in draw-down, flag
        total_pnl = (
            directional_decision.get("current_pnl", 0)
            + farming_decision.get("current_pnl", 0)
            + grid_decision.get("current_pnl", 0)
        )
        if total_pnl < 0:
            loss_pct = abs(total_pnl / current_equity) * 100
            if loss_pct > daily_loss_limit_pct:
                return False, [f"❌ Daily loss {loss_pct:.1f}% exceeds limit {daily_loss_limit_pct}%"]

        is_valid = len([a for a in alerts if "❌" in a]) == 0
        return is_valid, alerts

    def record_daily_state(
        self,
        date: str,
        equity: float,
        peak_equity: float,
        directional_pos: dict | None,
        farming_pos: dict | None,
        grid_pos: dict | None,
        prev_equity: float,
    ) -> PortfolioState:
        """
        Record daily state for journaling.

        Args:
            date: ISO date
            equity: Current equity
            peak_equity: Peak equity ever
            directional_pos: Current directional position
            farming_pos: Current farming position
            grid_pos: Current grid position
            prev_equity: Equity from previous day

        Returns:
            PortfolioState
        """
        pnl_usd = equity - prev_equity
        pnl_pct = (pnl_usd / prev_equity * 100) if prev_equity > 0 else 0.0

        state = PortfolioState(
            date=date,
            total_equity_usd=equity,
            peak_equity_usd=peak_equity,
            directional_position=directional_pos,
            farming_position=farming_pos,
            grid_position=grid_pos,
            total_pnl_usd=equity - 100.0,  # Assuming starting capital is $100
            total_pnl_pct=(equity - 100.0) / 100.0 * 100,
            daily_pnl_usd=pnl_usd,
            daily_pnl_pct=pnl_pct,
        )

        self.state = state
        return state

    def record_daily_decisions(
        self,
        date: str,
        directional_action: str,
        directional_reason: str,
        farming_action: str,
        farming_reason: str,
        grid_action: str,
        grid_reason: str,
        risk_alerts: list[str],
    ) -> DailyDecisions:
        """
        Record daily decisions for audit trail.
        """
        # Determine overall portfolio action
        actions = [directional_action, farming_action, grid_action]
        if "❌" in str(risk_alerts):
            portfolio_action = "halt"
        elif sum(1 for a in actions if a == "open") > 1:
            portfolio_action = "proceed_multi"
        elif any(a == "close" for a in actions):
            portfolio_action = "proceed_with_exits"
        else:
            portfolio_action = "proceed"

        decisions = DailyDecisions(
            date=date,
            directional_action=directional_action,
            directional_reason=directional_reason,
            farming_action=farming_action,
            farming_reason=farming_reason,
            grid_action=grid_action,
            grid_reason=grid_reason,
            portfolio_action=portfolio_action,
            risk_alerts=risk_alerts,
        )

        self.daily_decisions = decisions
        return decisions

    def get_summary(self) -> dict:
        """
        Get current portfolio summary for reporting.

        Returns:
            dict with state, allocation, decisions
        """
        return {
            "allocation": asdict(self.allocation) if self.allocation else None,
            "state": asdict(self.state) if self.state else None,
            "decisions": asdict(self.daily_decisions) if self.daily_decisions else None,
        }

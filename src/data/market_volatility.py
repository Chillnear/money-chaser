"""
Market volatility computation — ใช้สำหรับ Grid Trading Agent.

Volatility ถูกใช้เพื่อ:
1. Decide ว่าควร open/close grid
2. Estimate potential grid returns
3. Detect boring markets (volatility << threshold)
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


def compute_volatility_24h(prices: Sequence[float], lookback: int = 24) -> float:
    """
    Compute 24-hour rolling volatility (annualized).

    Args:
        prices: List of closing prices (should be at least lookback points)
        lookback: Number of periods to look back (default 24 for hourly data)

    Returns:
        Volatility as percentage (e.g., 3.5 = 3.5% daily volatility)

    Formula:
        1. Compute hourly returns: r_t = (price_t - price_{t-1}) / price_{t-1}
        2. Std dev of returns: σ = std(r)
        3. Annualize if needed, but for grid we want DAILY vol
        4. Return as percentage: σ * 100
    """
    if len(prices) < lookback:
        logger.warning(f"Not enough price data ({len(prices)}), need at least {lookback}")
        return 0.0

    prices_arr = np.array(prices[-lookback:], dtype=float)

    # Compute returns
    returns = np.diff(prices_arr) / prices_arr[:-1]

    # Standard deviation of returns (this is daily volatility if prices are daily closes)
    volatility = np.std(returns)

    return volatility * 100  # Convert to percentage


def compute_volatility_intraday(prices: Sequence[float], periods: int = 24) -> float:
    """
    Compute intraday volatility from hourly data.

    For grid trading on hourly prices:
    - If you have last 24 hourly prices
    - Volatility = std(hourly_returns) * 100

    Args:
        prices: Hourly closing prices
        periods: How many periods (hours) to use

    Returns:
        Intraday volatility %
    """
    return compute_volatility_24h(prices, lookback=periods)


def estimate_daily_range_pct(volatility_pct: float, confidence: float = 0.95) -> tuple[float, float]:
    """
    Estimate daily price range based on volatility (normal distribution assumption).

    Args:
        volatility_pct: Daily volatility as % (e.g., 3.5)
        confidence: Confidence level (0.95 = 95%, gives ±1.96σ bounds)

    Returns:
        (lower_pct, upper_pct) = expected range as % change from current price
        Example: (−6.9%, +6.9%) if vol=3.5% and confidence=95%
    """
    z_score = {
        0.68: 1.0,  # 1σ
        0.95: 1.96,  # 2σ
        0.99: 2.58,  # 3σ
    }.get(confidence, 1.96)

    range_pct = volatility_pct * z_score
    return (-range_pct, range_pct)


def is_volatility_suitable_for_grid(
    volatility_pct: float, grid_width_pct: float, min_threshold_pct: float = 1.5
) -> bool:
    """
    Check if volatility is suitable for grid trading.

    Rule of thumb:
    - If vol < grid_width: Grid won't complete full buy-sell cycles
    - If vol > grid_width * 3: Price might drift outside grid
    - Sweet spot: vol = 1.5x to 3x of grid_width

    Args:
        volatility_pct: Current 24h volatility
        grid_width_pct: Grid width (e.g., 5%)
        min_threshold_pct: Minimum volatility to trade

    Returns:
        True if volatility is suitable, False otherwise
    """
    if volatility_pct < min_threshold_pct:
        return False
    if volatility_pct > grid_width_pct * 4:
        # Volatility too high, price might drift out of grid
        logger.warning(f"Volatility {volatility_pct:.2f}% is very high, grid might be inefficient")
        return True  # Still allow, but warn
    return True


class VolatilityTracker:
    """
    Track volatility over time and detect changes.
    Useful for deciding when to close grids (volatility collapse).
    """

    def __init__(self, window_size: int = 7):
        """
        Args:
            window_size: How many days to track volatility
        """
        self.window_size = window_size
        self.volatility_history: list[float] = []

    def update(self, volatility_pct: float) -> None:
        """Add new volatility measurement."""
        self.volatility_history.append(volatility_pct)
        if len(self.volatility_history) > self.window_size:
            self.volatility_history.pop(0)

    def get_average(self) -> float:
        """Get average volatility over window."""
        if not self.volatility_history:
            return 0.0
        return np.mean(self.volatility_history)

    def get_trend(self) -> str:
        """Get volatility trend: 'rising', 'falling', 'stable'."""
        if len(self.volatility_history) < 2:
            return "unknown"

        recent = self.volatility_history[-1]
        avg = self.get_average()

        if recent > avg * 1.2:
            return "rising"
        elif recent < avg * 0.8:
            return "falling"
        else:
            return "stable"

    def has_collapsed(self, threshold_pct: float = 0.5) -> bool:
        """Check if volatility has collapsed below threshold."""
        if not self.volatility_history:
            return False
        return self.volatility_history[-1] < threshold_pct

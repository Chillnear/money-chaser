"""
Unit tests for FundingFarmingAgent logic.
"""

import pytest

from src.agents.funding_farmer import (
    FarmingPosition,
    FundingFarmingAgent,
    FundingRate,
    estimate_annual_return,
)


def test_should_open_position_good_funding():
    """Should open when funding is attractive."""
    agent = FundingFarmingAgent(min_funding_annual_pct=10.0, max_basis_spread_pct=1.0)

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.025,  # 0.025% per 8h = ~27% annual
        spot_price=45000,
        perp_price=45050,  # 0.11% spread (OK)
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    decision = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=500,
    )

    assert decision.action == "open"
    assert decision.symbol == "BTCUSDT"
    assert decision.long_notional_usd == 125  # 50% of 500 / 2
    assert decision.short_notional_usd == 125


def test_should_not_open_low_funding():
    """Should not open when funding is below threshold."""
    agent = FundingFarmingAgent(min_funding_annual_pct=10.0)

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.003,  # 0.003% per 8h = ~3.3% annual (too low)
        spot_price=45000,
        perp_price=45000,
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    decision = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=500,
    )

    assert decision.action == "hold"


def test_should_not_open_wide_basis():
    """Should not open when basis spread is too wide."""
    agent = FundingFarmingAgent(max_basis_spread_pct=1.0)

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.025,
        spot_price=45000,
        perp_price=45500,  # 1.11% spread (too wide)
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    decision = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=500,
    )

    assert decision.action == "hold"


def test_should_close_harvest_target():
    """Should close when position has made enough profit."""
    agent = FundingFarmingAgent(harvest_target_profit_pct=30.0)

    position = FarmingPosition(
        symbol="BTCUSDT",
        long_notional_usd=100,
        short_notional_usd=100,
        entry_price=45000,
        entry_time="2026-07-29T10:00:00Z",
        accumulated_funding_usd=65,  # 32.5% profit on $200 position
    )

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.025,
        spot_price=45000,
        perp_price=45000,
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    decision = agent.decide(
        current_position=position,
        funding_rate=funding,
        available_capital_usd=500,
    )

    assert decision.action == "close"
    assert "Harvest" in decision.reason


def test_should_close_low_funding():
    """Should close when funding rate dies (< threshold for 2+ cycles)."""
    agent = FundingFarmingAgent(low_funding_threshold_pct=0.005)

    position = FarmingPosition(
        symbol="BTCUSDT",
        long_notional_usd=100,
        short_notional_usd=100,
        entry_price=45000,
        entry_time="2026-07-29T10:00:00Z",
        accumulated_funding_usd=5,
    )

    funding_low = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.002,  # < 0.005% threshold
        spot_price=45000,
        perp_price=45000,
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    # First call: notice low funding
    decision1 = agent.decide(
        current_position=position,
        funding_rate=funding_low,
        available_capital_usd=500,
    )
    assert decision1.action == "hold"  # First low, just notice

    # Second call: close after 2 lows
    decision2 = agent.decide(
        current_position=position,
        funding_rate=funding_low,
        available_capital_usd=500,
    )
    assert decision2.action == "close"
    assert "too low" in decision2.reason.lower()


def test_estimate_annual_return():
    """Sanity check on return estimation."""
    result = estimate_annual_return(
        funding_per_8h=0.025,
        rebalance_cost_pct=0.008,
        liquidation_risk_pct=0.005,
    )

    assert result["gross_annual_pct"] == pytest.approx(27.38, abs=0.1)
    assert result["costs_pct"] == pytest.approx(0.013, abs=0.001)
    assert result["net_annual_pct"] > 0
    assert result["net_annual_pct"] < result["gross_annual_pct"]


def test_min_capital_too_small():
    """Should not open if available capital too small."""
    agent = FundingFarmingAgent()

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.025,
        spot_price=45000,
        perp_price=45000,
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    decision = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=10,  # Too small (min is $20)
    )

    assert decision.action == "hold"


def test_risk_multiplier_reduces_size():
    """Risk breaker reduces position size proportionally."""
    agent = FundingFarmingAgent()

    funding = FundingRate(
        symbol="BTCUSDT",
        rate_per_8h=0.025,
        spot_price=45000,
        perp_price=45000,
        spot_liquidity_usd=500_000,
        perp_liquidity_usd=200_000,
    )

    # Normal risk
    decision_normal = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=500,
        risk_multiplier=1.0,
    )

    # Reduced risk (e.g., after a loss)
    decision_reduced = agent.decide(
        current_position=None,
        funding_rate=funding,
        available_capital_usd=500,
        risk_multiplier=0.5,
    )

    assert decision_normal.long_notional_usd > decision_reduced.long_notional_usd
    assert decision_reduced.action == "open"  # Still opens, just smaller

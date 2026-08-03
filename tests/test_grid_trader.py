"""
Unit tests for GridTradingAgent logic.
"""

import pytest

from src.agents.grid_trader import (
    GridDecision,
    GridOrder,
    GridOrderSide,
    GridPosition,
    GridTradingAgent,
    estimate_grid_return,
    generate_grid_orders,
)


def test_should_open_grid_high_volatility():
    """Should open when volatility is high enough."""
    agent = GridTradingAgent(min_volatility_open=1.5)

    decision = agent.decide(
        current_position=None,
        current_price=45000,
        volatility_24h=3.5,  # 3.5% > 1.5% threshold
        available_capital_usd=100,
    )

    assert decision.action == "open"
    assert decision.grid_width_pct == 5.0
    assert decision.num_levels == 5
    assert decision.total_capital_usd == pytest.approx(90, abs=1)  # 90% of 100


def test_should_not_open_low_volatility():
    """Should not open when volatility is low."""
    agent = GridTradingAgent(min_volatility_open=1.5)

    decision = agent.decide(
        current_position=None,
        current_price=45000,
        volatility_24h=0.5,  # 0.5% < 1.5% threshold
        available_capital_usd=100,
    )

    assert decision.action == "hold"


def test_should_not_open_insufficient_capital():
    """Should not open with too little capital."""
    agent = GridTradingAgent()

    decision = agent.decide(
        current_position=None,
        current_price=45000,
        volatility_24h=5.0,
        available_capital_usd=5,  # Less than $10 minimum
    )

    assert decision.action == "hold"


def test_should_close_grid_harvest_target():
    """Should close when grid has made enough profit."""
    agent = GridTradingAgent(harvest_target_profit_pct=30.0)

    position = GridPosition(
        symbol="BTCUSDT",
        center_price=45000,
        grid_width_pct=5.0,
        num_levels=5,
        total_capital=30,
        accumulated_pnl=10,  # 33% profit on $30
    )

    decision = agent.decide(
        current_position=position,
        current_price=45000,
        volatility_24h=3.0,
    )

    assert decision.action == "close"
    assert "Harvest" in decision.reason


def test_should_close_grid_low_volatility():
    """Should close when volatility dies (boring market)."""
    agent = GridTradingAgent(max_volatility_close=0.5)

    position = GridPosition(
        symbol="BTCUSDT",
        center_price=45000,
        grid_width_pct=5.0,
        num_levels=5,
        total_capital=30,
    )

    # First low vol day
    decision1 = agent.decide(
        current_position=position,
        current_price=45000,
        volatility_24h=0.3,
    )
    assert decision1.action == "hold"  # Just detected low vol

    # Second low vol day
    decision2 = agent.decide(
        current_position=position,
        current_price=45000,
        volatility_24h=0.3,
    )
    assert decision2.action == "close"
    assert "too low" in decision2.reason.lower()


def test_should_close_grid_price_drifted():
    """Should close if price drifted way outside grid."""
    agent = GridTradingAgent()

    position = GridPosition(
        symbol="BTCUSDT",
        center_price=45000,
        grid_width_pct=5.0,  # Grid: 42,750 to 47,250
        num_levels=5,
        total_capital=30,
    )

    # Price dropped to 40,000 (well below grid)
    decision = agent.decide(
        current_position=position,
        current_price=40000,  # way below 42,750
        volatility_24h=10.0,  # even with high vol
    )

    assert decision.action == "close"
    assert "drifted" in decision.reason.lower()


def test_generate_grid_orders_symmetric():
    """Grid orders should be symmetric around center price."""
    orders = generate_grid_orders(center_price=45000, grid_width_pct=5.0, num_levels=5, total_capital_usd=30)

    # Should have 10 orders total (5 buy + 5 sell)
    assert len(orders) == 10

    buy_orders = [o for o in orders if o.side == GridOrderSide.BUY]
    sell_orders = [o for o in orders if o.side == GridOrderSide.SELL]

    assert len(buy_orders) == 5
    assert len(sell_orders) == 5

    # Buy orders should be below center, ascending
    assert all(o.price <= 45000 for o in buy_orders)
    assert buy_orders[0].price < buy_orders[1].price < buy_orders[4].price

    # Sell orders should be above center, ascending
    assert all(o.price >= 45000 for o in sell_orders)
    assert sell_orders[0].price < sell_orders[1].price < sell_orders[4].price

    # Each order should have equal size
    expected_size = 30 / 10  # 5 levels * 2 sides
    assert all(pytest.approx(o.size_usd, abs=0.1) == expected_size for o in orders)


def test_generate_grid_orders_boundary():
    """Grid should span from center*(1-width%) to center*(1+width%)."""
    center = 45000
    width = 5.0
    lower_bound = center * (1 - width / 100)  # 42,750
    upper_bound = center * (1 + width / 100)  # 47,250

    orders = generate_grid_orders(center_price=center, grid_width_pct=width, num_levels=5, total_capital_usd=30)

    # Lowest buy should be near lower bound
    buy_orders = [o for o in orders if o.side == GridOrderSide.BUY]
    assert buy_orders[0].price == pytest.approx(lower_bound, rel=0.01)

    # Highest sell should be near upper bound
    sell_orders = [o for o in orders if o.side == GridOrderSide.SELL]
    assert sell_orders[-1].price == pytest.approx(upper_bound, rel=0.01)


def test_estimate_grid_return_high_volatility():
    """High volatility should produce better grid returns."""
    result = estimate_grid_return(volatility_daily_pct=5.0, grid_width_pct=5.0)

    assert result["daily_pct"] > 0
    assert result["monthly_pct"] > 0
    assert result["cycles_per_day"] >= 1


def test_estimate_grid_return_low_volatility():
    """Low volatility should produce zero or negative returns."""
    result = estimate_grid_return(volatility_daily_pct=0.5, grid_width_pct=5.0)

    # Not enough vol to complete even 1 cycle
    assert result["daily_pct"] == 0
    assert result["monthly_pct"] == 0


def test_estimate_grid_return_reasonable_ranges():
    """Return estimates should be in reasonable ranges."""
    # Normal market: 3% daily vol
    result = estimate_grid_return(volatility_daily_pct=3.0, grid_width_pct=5.0, fee_per_trade_pct=0.1)

    # Should be positive but not crazy (e.g., <10% daily)
    assert 0 <= result["daily_pct"] < 10
    assert 0 <= result["monthly_pct"] < 300


def test_risk_multiplier_reduces_grid_capital():
    """Risk multiplier should reduce grid capital proportionally."""
    agent = GridTradingAgent()

    decision_normal = agent.decide(
        current_position=None,
        current_price=45000,
        volatility_24h=5.0,
        available_capital_usd=100,
        risk_multiplier=1.0,
    )

    decision_reduced = agent.decide(
        current_position=None,
        current_price=45000,
        volatility_24h=5.0,
        available_capital_usd=100,
        risk_multiplier=0.5,
    )

    # Reduced risk should use less capital
    assert decision_reduced.total_capital_usd < decision_normal.total_capital_usd
    assert decision_reduced.action == "open"  # Still opens, just smaller

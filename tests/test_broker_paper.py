from __future__ import annotations

import pytest

from src.execution.broker_paper import PaperBroker

DAY = 86400.0


def test_open_position_long_pays_slippage_premium():
    broker = PaperBroker(starting_equity_usd=100.0, taker_fee_pct=0.0, slippage_pct=1.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    assert pos.entry_price == pytest.approx(101.0)  # ซื้อแพงกว่า mid 1%
    assert pos.stop_price == pytest.approx(101.0 * 0.98)
    assert pos.take_profit_price == pytest.approx(101.0 * 1.04)


def test_open_position_short_gets_worse_lower_price():
    broker = PaperBroker(starting_equity_usd=100.0, taker_fee_pct=0.0, slippage_pct=1.0)
    pos = broker.open_position("BTC", "short", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    assert pos.entry_price == pytest.approx(99.0)  # ขายได้ถูกกว่า mid 1%
    assert pos.stop_price == pytest.approx(99.0 * 1.02)
    assert pos.take_profit_price == pytest.approx(99.0 * 0.96)


def test_close_long_profit_no_fee_no_slippage():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)

    trade = broker.close_position(pos, exit_price=110.0, now_ts=DAY, reason="take_profit_hit")

    assert trade.pnl_usd == pytest.approx(10.0)
    assert trade.fee_usd == pytest.approx(0.0)
    assert broker.get_account_equity() == pytest.approx(1010.0)


def test_close_short_profit_no_fee_no_slippage():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "short", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)

    trade = broker.close_position(pos, exit_price=90.0, now_ts=DAY, reason="take_profit_hit")

    assert trade.pnl_usd == pytest.approx(10.0)
    assert broker.get_account_equity() == pytest.approx(1010.0)


def test_close_position_fee_reduces_pnl():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.1, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)

    trade = broker.close_position(pos, exit_price=110.0, now_ts=DAY, reason="take_profit_hit")

    # entry_fee = 100*0.001=0.1, exit_notional=110, exit_fee=110*0.001=0.11, total=0.21
    assert trade.fee_usd == pytest.approx(0.21)
    assert trade.pnl_usd == pytest.approx(10.0 - 0.21)


def test_close_position_loss_reduces_equity():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)

    trade = broker.close_position(pos, exit_price=98.0, now_ts=DAY, reason="stop_loss_hit")

    assert trade.pnl_usd == pytest.approx(-2.0)
    assert broker.get_account_equity() == pytest.approx(998.0)


def test_check_candle_trigger_long_sl_and_tp():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    # stop_price=98, tp_price=104

    sl_hit, tp_hit = broker.check_candle_trigger(pos, candle_high=103.0, candle_low=99.0)
    assert sl_hit is False and tp_hit is False

    sl_hit, tp_hit = broker.check_candle_trigger(pos, candle_high=103.0, candle_low=97.0)
    assert sl_hit is True and tp_hit is False

    sl_hit, tp_hit = broker.check_candle_trigger(pos, candle_high=105.0, candle_low=99.0)
    assert sl_hit is False and tp_hit is True


def test_check_candle_trigger_short_sl_and_tp():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "short", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    # stop_price=102, tp_price=96

    sl_hit, tp_hit = broker.check_candle_trigger(pos, candle_high=103.0, candle_low=99.0)
    assert sl_hit is True and tp_hit is False

    sl_hit, tp_hit = broker.check_candle_trigger(pos, candle_high=101.0, candle_low=95.0)
    assert sl_hit is False and tp_hit is True


def test_evaluate_exit_prioritizes_sl_over_tp():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    # แท่งเทียนแกว่งทะลุทั้ง SL(98) และ TP(104) ในวันเดียว
    decision = broker.evaluate_exit(pos, candle_high=105.0, candle_low=95.0, now_ts=DAY, max_holding_days=5)
    assert decision.should_exit is True
    assert decision.reason == "stop_loss_hit"


def test_evaluate_exit_time_based_when_no_trigger():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    decision = broker.evaluate_exit(pos, candle_high=101.0, candle_low=99.0, now_ts=6 * DAY, max_holding_days=5)
    assert decision.should_exit is True
    assert decision.reason == "max_holding_days_exceeded"


def test_evaluate_exit_holds_when_nothing_triggered():
    broker = PaperBroker(starting_equity_usd=1000.0, taker_fee_pct=0.0, slippage_pct=0.0)
    pos = broker.open_position("BTC", "long", 100.0, mid_price=100.0, stop_pct=2.0, take_profit_pct=4.0, now_ts=0.0)
    decision = broker.evaluate_exit(pos, candle_high=101.0, candle_low=99.0, now_ts=2 * DAY, max_holding_days=5)
    assert decision.should_exit is False

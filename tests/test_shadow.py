from __future__ import annotations

from src.settings import AppConfig, RiskConfig, Secrets, Settings
from src.execution.broker_base import Position
from src.shadow import run_funding_carry_shadow_day
from src.util.io import load_json, save_json


def _settings():
    return Settings(
        mode="paper", risk=RiskConfig.load(), app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )


class FakeHlClient:
    """คุมได้ว่า get_candles() ของแต่ละ asset จะคืนอะไร — จำลองแค่ high/low/close วันล่าสุดพอสำหรับ
    evaluate_exit ไม่ต้องมีข้อมูลจริงครบเหมือน production hl_client
    """

    def __init__(self, candles_by_asset: dict[str, list[dict]] | None = None):
        self.candles_by_asset = candles_by_asset or {}

    def get_candles(self, asset, interval="1d", lookback_days=2):
        if asset not in self.candles_by_asset:
            raise ValueError(f"ไม่มีข้อมูลปลอมสำหรับ {asset} ในเทสนี้")
        return self.candles_by_asset[asset]


def test_skips_extreme_funding_when_minimum_notional_exceeds_shadow_risk_budget(tmp_path):
    settings = _settings()
    shortlist = [{"coin": "BTC", "funding_score": 0.9}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.001, "mark_px": 100.0}]
    price_features_by_coin = {"BTC": {"atr_pct": 2.0}}

    result = run_funding_carry_shadow_day(
        settings, FakeHlClient(), shortlist, {}, price_features_by_coin, universe_snapshot,
        now_ts=1_800_000_000.0, journal_dir=tmp_path,
    )

    assert result["action"] == "flat"
    assert result["open_position"] is None
    assert result["equity_usd"] is not None

    saved_state = load_json(tmp_path / "shadow_funding_carry_state.json", default=None)
    assert saved_state["open_position"] is None


def test_flat_when_funding_not_extreme_and_no_position(tmp_path):
    settings = _settings()
    shortlist = [{"coin": "BTC", "funding_score": 0.55}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.0001, "mark_px": 100.0}]

    result = run_funding_carry_shadow_day(
        settings, FakeHlClient(), shortlist, {}, {"BTC": {"atr_pct": 2.0}}, universe_snapshot,
        now_ts=1_800_000_000.0, journal_dir=tmp_path,
    )

    assert result["action"] == "flat"
    assert result["open_position"] is None
    assert result["equity_usd"] == 28.0  # ไม่เปิดไม้ -> equity เท่าทุนตั้งต้นเดิม


def test_holds_position_across_days_and_then_closes_on_stop_hit(tmp_path):
    settings = _settings()
    shortlist = [{"coin": "BTC", "funding_score": 0.9}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.001, "mark_px": 100.0}]
    price_features_by_coin = {"BTC": {"atr_pct": 2.0}}

    position = Position(
        asset="BTC", side="short", notional_usd=10.0, entry_price=100.0,
        stop_price=103.0, take_profit_price=94.0, opened_at_ts=1_800_000_000.0,
    )
    save_json(tmp_path / "shadow_funding_carry_state.json", {"equity_usd": 28.0, "open_position": position.__dict__})
    stop_price = position.stop_price

    # แท่งเทียนวันถัดไปราคาพุ่งชน stop ของฝั่ง short (high >= stop_price)
    hl_client_day2 = FakeHlClient({"BTC": [{"h": stop_price * 1.05, "l": stop_price * 0.99, "c": stop_price * 1.02}]})
    day2 = run_funding_carry_shadow_day(
        settings, hl_client_day2, shortlist, {}, price_features_by_coin, universe_snapshot,
        now_ts=1_800_086_400.0, journal_dir=tmp_path,
    )

    assert day2["action"] == "closed_stop_loss_hit"
    assert day2["open_position"] is None


def test_fails_safe_and_never_raises_when_hl_client_errors(tmp_path):
    settings = _settings()
    shortlist = [{"coin": "BTC", "funding_score": 0.9}]
    universe_snapshot = [{"coin": "BTC", "funding": 0.001, "mark_px": 100.0}]
    price_features_by_coin = {"BTC": {"atr_pct": 2.0}}

    position = Position(
        asset="BTC", side="short", notional_usd=10.0, entry_price=100.0,
        stop_price=103.0, take_profit_price=94.0, opened_at_ts=1_800_000_000.0,
    )
    save_json(tmp_path / "shadow_funding_carry_state.json", {"equity_usd": 28.0, "open_position": position.__dict__})

    class BrokenHlClient:
        def get_candles(self, *args, **kwargs):
            raise RuntimeError("network ล้มเหลวจำลอง")

    result = run_funding_carry_shadow_day(
        settings, BrokenHlClient(), shortlist, {}, price_features_by_coin, universe_snapshot,
        now_ts=1_800_086_400.0, journal_dir=tmp_path,
    )

    assert result["action"] == "error"
    assert "error" in result

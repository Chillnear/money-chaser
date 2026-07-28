from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.execution.broker_paper import PaperBroker
from src.execution.reconcile import mark_run_complete
from src.main import DailyRunResult, load_journal_state, run_daily_pipeline, save_journal_state, JournalState
from src.risk.breaker import BreakerState, write_kill_file
from src.settings import AppConfig, RiskConfig, Secrets, Settings

REGISTRY = {
    "roles": {
        "analyst_trend": {"model": "m-trend", "provider": "alibaba", "tier": "mid", "source": "litellm"},
        "analyst_positioning": {"model": "m-pos", "provider": "anthropic", "tier": "mid", "source": "litellm"},
        "analyst_macro": {"model": "m-macro", "provider": "deepseek", "tier": "mid", "source": "litellm"},
        "redteam": {"model": "m-red", "provider": "google", "tier": "cheap", "source": "litellm"},
        "judge": {"model": "m-judge", "provider": "anthropic", "tier": "frontier", "source": "litellm"},
        "reflector": {"model": "m-reflect", "provider": "openai", "tier": "frontier", "source": "litellm"},
    }
}


def _make_settings() -> Settings:
    return Settings(
        mode="paper",
        risk=RiskConfig.load(),
        app=AppConfig.load(),
        secrets=Secrets(litellm_base_url="https://fake.example.com", litellm_key_1="k1", litellm_key_2="k2"),
    )


def _make_candles(n: int = 60, start: float = 100.0, daily_pct: float = 1.0, start_ts_ms: int = 0) -> list[dict]:
    """สร้างแท่งเทียนขาขึ้นชัดเจน n วัน — ใช้ทดสอบ path ที่ต้องมีเทรนด์แรงพอให้ ADX/EMA ชัด"""
    candles = []
    price = start
    ts = start_ts_ms or int(time.time() * 1000) - n * 86400 * 1000
    for i in range(n):
        open_price = price
        close_price = price * (1 + daily_pct / 100)
        high = max(open_price, close_price) * 1.002
        low = min(open_price, close_price) * 0.998
        candles.append(
            {"t": ts + i * 86400 * 1000, "T": ts + (i + 1) * 86400 * 1000, "o": open_price, "h": high, "l": low, "c": close_price, "v": 1000.0}
        )
        price = close_price
    return candles


def _make_universe_snapshot() -> list[dict]:
    return [
        {"coin": "BTC", "funding": 0.0001, "open_interest_usd": 5.0e7, "day_volume_usd": 1.0e8, "mark_px": 100.0, "prev_day_px": 99.0},
        {"coin": "PAXG", "funding": 0.0, "open_interest_usd": 5.0e7, "day_volume_usd": 1.0e8, "mark_px": 100.0, "prev_day_px": 100.0},
    ]


class FakeHLClient:
    def __init__(self, universe_snapshot, candles_by_coin):
        self._universe_snapshot = universe_snapshot
        self._candles_by_coin = candles_by_coin
        self.get_candles_calls: list[tuple] = []

    def get_universe_snapshot(self):
        return self._universe_snapshot

    def get_candles(self, coin, interval="1d", lookback_days=400):
        self.get_candles_calls.append((coin, interval, lookback_days))
        return self._candles_by_coin[coin]


def _fake_llm_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _analyst_json(asset="BTC", direction="long", confidence=70):
    return json.dumps(
        {
            "candidates": [
                {
                    "asset": asset,
                    "direction": direction,
                    "confidence": confidence,
                    "thesis": "เทรนด์ขึ้นแรง ADX สูง",
                    "key_evidence": ["adx สูง", "ema gap บวก"],
                    "invalidation": "หลุด EMA50",
                    "expected_move_pct": 3.0,
                    "horizon_days": 2,
                }
            ]
        }
    )


def _judge_json(action="long", asset="BTC", confidence=75):
    return json.dumps(
        {
            "action": action,
            "asset": asset,
            "confidence": confidence,
            "stop_pct": 3.0,
            "take_profit_pct": 6.0,
            "reasoning": "เทรนด์ชัดและ analyst ส่วนใหญ่เห็นตรงกัน",
            "why_this_over_others": "BTC มี composite score สูงสุดและมีเทรนด์ชัดกว่า PAXG",
            "agreement_summary": "trend และ positioning เห็นตรงกัน",
            "redteam_response": "รับทราบข้อค้านแต่ยังมั่นใจ",
            "lessons_applied": [],
        }
    )


def _make_llm_client(completion_side_effect):
    from src.agents.llm import LLMClient

    completion_fn = MagicMock(side_effect=completion_side_effect)
    cost_fn = MagicMock(return_value=0.001)
    return LLMClient(base_url="https://fake.example.com", api_keys=["k1"], completion_fn=completion_fn, cost_fn=cost_fn)


@pytest.fixture
def settings():
    return _make_settings()


@pytest.fixture
def hl_client():
    universe = _make_universe_snapshot()
    candles = {
        "BTC": _make_candles(n=60, start=80.0, daily_pct=1.5),  # เทรนด์ขึ้นแรง
        "PAXG": _make_candles(n=60, start=100.0, daily_pct=0.02),  # เกือบนิ่ง (chop)
    }
    return FakeHLClient(universe, candles)


def test_run_daily_pipeline_opens_long_position_on_bullish_consensus(tmp_path, settings, hl_client):
    # ทุก analyst + redteam เห็นตรงกันว่า BTC long, judge ตัดสินใจ long ด้วย confidence สูงพอผ่านทุก gate
    responses = [_fake_llm_response(_analyst_json())] * 4 + [_fake_llm_response(_judge_json())]
    llm_client = _make_llm_client(responses)
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=time.time(),
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "opened_long"
    saved_state = json.loads((tmp_path / "journal" / "state.json").read_text(encoding="utf-8"))
    assert saved_state["open_position"]["asset"] == "BTC"
    assert saved_state["open_position"]["side"] == "long"


def test_run_daily_pipeline_flat_when_judge_abstains(tmp_path, settings, hl_client):
    # analyst/redteam ตอบปกติ แต่ judge ตอบ garbage ทุกครั้ง -> abstain -> FLAT
    responses = [_fake_llm_response(_analyst_json())] * 4 + [_fake_llm_response("ไม่ใช่ json เลย")] * 3
    llm_client = _make_llm_client(responses)
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=time.time(),
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "flat"
    assert "abstain" in result.reason


def test_run_daily_pipeline_skips_when_already_run_today(tmp_path, settings, hl_client):
    last_run_path = tmp_path / "last_run.json"
    mark_run_complete(last_run_path, "2026-07-27")
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=last_run_path,
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "skipped_already_ran"


def test_run_daily_pipeline_skips_when_killed(tmp_path, settings, hl_client):
    kill_path = tmp_path / "KILL"
    write_kill_file(kill_path, "ทดสอบ")
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        journal_dir=tmp_path / "journal",
        kill_path=kill_path,
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "skipped_killed"


def test_run_daily_pipeline_skips_when_breaker_paused(tmp_path, settings, hl_client):
    journal_dir = tmp_path / "journal"
    now_ts = time.time()
    paused_state = JournalState(
        equity_usd=28.0,
        peak_equity_usd=28.0,
        open_position=None,
        breaker=BreakerState(paused_until_ts=now_ts + 3600, pause_reason="ทดสอบพัก breaker"),
    )
    save_journal_state(journal_dir, paused_state)
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=now_ts,
        journal_dir=journal_dir,
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "skipped_paused"
    assert "ทดสอบพัก breaker" in result.reason


def test_run_daily_pipeline_skips_on_macro_event_veto(tmp_path, settings, hl_client):
    # P5.2: ห้ามเทรดวันมีข่าวมหภาคสำคัญ — ต้องข้ามทั้งวัน (ก่อนเรียก LLM เลย) ไม่ใช่แค่ veto ไม้สุดท้าย
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-29",
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
        macro_veto_status={"vetoed": True, "reason": "วันนี้มีข่าวมหภาคสำคัญใกล้เวลา: FOMC Statement — ห้ามเทรดวันนี้"},
    )

    assert result.action_taken == "skipped_macro_event"
    assert "FOMC Statement" in result.reason
    # ต้องไม่มีการเรียก LLM เลยตอนถูก veto (ประหยัดค่าใช้จ่าย)
    assert llm_client._completion_fn.call_count == 0


def test_run_daily_pipeline_does_not_skip_when_macro_veto_status_missing_or_not_vetoed(tmp_path, settings, hl_client):
    # fail-safe: macro_veto_status=None (ดึงข้อมูลไม่ได้) หรือ vetoed=False ต้องไม่บล็อกการเทรด
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
        macro_veto_status=None,
    )

    assert result.action_taken != "skipped_macro_event"


def test_run_daily_pipeline_skips_on_equity_reconcile_mismatch(tmp_path, settings, hl_client):
    journal_dir = tmp_path / "journal"
    mismatched_state = JournalState(equity_usd=1000.0, peak_equity_usd=1000.0, open_position=None, breaker=BreakerState())
    save_journal_state(journal_dir, mismatched_state)
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)  # equity ไม่ตรงกับ journal

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        journal_dir=journal_dir,
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "skipped_reconcile_mismatch"


def test_run_daily_pipeline_uses_baseline_without_any_llm_call_when_budget_exhausted(tmp_path, settings, hl_client):
    # เดือนนี้ใช้ไปเกิน hard_stop_usd แล้ว (15.0) -> DEGRADE_LLM_OFF -> ต้องใช้ src/baseline.py แทน
    # ไม่เรียก LLM เลยแม้แต่ครั้งเดียว แต่ระบบยังเทรดต่อได้ (ไม่ fail-closed เป็น FLAT เปล่าๆ)
    llm_client = _make_llm_client([])
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)
    now_ts = time.time()
    llm_cost_records = [{"ts": now_ts - 100, "cost_usd": 20.0}]

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=now_ts,
        journal_dir=tmp_path / "journal",
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
        llm_cost_records=llm_cost_records,
    )

    # fixture BTC เป็นเทรนด์ขึ้นแรง -> baseline เลือก BTC long ได้เอง โดยไม่ต้องมี LLM เลย
    assert result.action_taken == "opened_long"
    llm_client._completion_fn.assert_not_called()

    decisions_log = (tmp_path / "journal" / "decisions.jsonl").read_text(encoding="utf-8")
    assert '"source": "baseline"' in decisions_log


def test_manage_existing_position_closes_on_stop_loss_and_clears_open_position(tmp_path, settings, hl_client):
    journal_dir = tmp_path / "journal"
    now_ts = time.time()
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    # เปิดไม้ BTC long ด้วยมือผ่าน broker เดียวกัน เพื่อให้ entry_price/stop_price สอดคล้องกับ fee/slippage จริง
    position = broker.open_position(
        asset="BTC", side="long", notional_usd=15.0, mid_price=100.0, stop_pct=3.0, take_profit_pct=6.0, now_ts=now_ts - 86400
    )
    from dataclasses import asdict

    existing_state = JournalState(
        equity_usd=broker.get_account_equity(), peak_equity_usd=28.0, open_position=asdict(position), breaker=BreakerState()
    )
    save_journal_state(journal_dir, existing_state)

    # แท่งเทียนล่าสุดของ BTC ราคาร่วงทะลุ stop_price -> ต้องปิดไม้ด้วย stop_loss_hit
    stop_price = position.stop_price
    hl_client._candles_by_coin["BTC"] = [
        {"t": 0, "T": 0, "o": 100.0, "h": 100.0, "l": stop_price - 1.0, "c": stop_price - 0.5, "v": 100.0},
    ]

    llm_client = _make_llm_client([])  # ไม่ควรถูกเรียกเลยเพราะมีไม้เปิดอยู่ -> ข้าม step agents ทั้งหมด
    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=now_ts,
        journal_dir=journal_dir,
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "closed_position"
    assert "stop_loss_hit" in result.reason
    saved_state = json.loads((journal_dir / "state.json").read_text(encoding="utf-8"))
    assert saved_state["open_position"] is None
    llm_client._completion_fn.assert_not_called()


def test_manage_existing_position_holds_when_no_exit_condition(tmp_path, settings, hl_client):
    journal_dir = tmp_path / "journal"
    now_ts = time.time()
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)

    position = broker.open_position(
        asset="BTC", side="long", notional_usd=15.0, mid_price=100.0, stop_pct=3.0, take_profit_pct=6.0, now_ts=now_ts
    )
    from dataclasses import asdict

    existing_state = JournalState(
        equity_usd=broker.get_account_equity(), peak_equity_usd=28.0, open_position=asdict(position), breaker=BreakerState()
    )
    save_journal_state(journal_dir, existing_state)

    # แท่งเทียนอยู่ระหว่าง stop กับ take profit พอดี ไม่ชนอะไรเลย และยังไม่ครบ max_holding_days
    hl_client._candles_by_coin["BTC"] = [
        {"t": 0, "T": 0, "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5, "v": 100.0},
    ]

    llm_client = _make_llm_client([])
    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=REGISTRY,
        today_date="2026-07-27",
        now_ts=now_ts,
        journal_dir=journal_dir,
        kill_path=tmp_path / "KILL",
        last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "held_position"
    saved_state = json.loads((journal_dir / "state.json").read_text(encoding="utf-8"))
    assert saved_state["open_position"]["asset"] == "BTC"


def test_pipeline_writes_equity_jsonl_every_run(tmp_path, settings, hl_client):
    responses = [_fake_llm_response(_analyst_json())] * 4 + [_fake_llm_response(_judge_json())]
    llm_client = _make_llm_client(responses)
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)
    journal_dir = tmp_path / "journal"

    run_daily_pipeline(
        settings=settings, hl_client=hl_client, llm_client=llm_client, broker=broker,
        model_registry=REGISTRY, today_date="2026-07-27", now_ts=time.time(),
        journal_dir=journal_dir, kill_path=tmp_path / "KILL", last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    lines = (journal_dir / "equity.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["date"] == "2026-07-27"
    assert record["equity_usd"] == pytest.approx(28.0)
    assert record["has_open_position"] is True


def test_pipeline_writes_llm_cost_jsonl_so_cost_governor_can_work(tmp_path, settings, hl_client):
    # ถ้าไม่เขียนไฟล์นี้ degradation ladder จะไม่มีวันทำงาน (cost governor อ่านได้ค่าว่างตลอด)
    responses = [_fake_llm_response(_analyst_json())] * 4 + [_fake_llm_response(_judge_json())]
    llm_client = _make_llm_client(responses)
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)
    journal_dir = tmp_path / "journal"

    run_daily_pipeline(
        settings=settings, hl_client=hl_client, llm_client=llm_client, broker=broker,
        model_registry=REGISTRY, today_date="2026-07-27", now_ts=time.time(),
        journal_dir=journal_dir, kill_path=tmp_path / "KILL", last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    lines = (journal_dir / "llm_cost.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    roles = {r["role"] for r in records}

    assert roles == {"analyst_trend", "analyst_positioning", "analyst_macro", "redteam", "judge"}
    assert all("cost_usd" in r and "ts" in r for r in records)
    assert sum(r["cost_usd"] for r in records) > 0


def test_pipeline_equity_jsonl_records_flat_days_too(tmp_path, settings, hl_client):
    # วันที่ไม่เทรด (FLAT) ก็ต้องบันทึก equity ไว้ ไม่งั้นกราฟจะขาดช่วง
    responses = [_fake_llm_response(_analyst_json())] * 4 + [_fake_llm_response(_judge_json(action="flat", asset=None))]
    llm_client = _make_llm_client(responses)
    broker = PaperBroker(starting_equity_usd=28.0, taker_fee_pct=0.045, slippage_pct=0.05)
    journal_dir = tmp_path / "journal"

    result = run_daily_pipeline(
        settings=settings, hl_client=hl_client, llm_client=llm_client, broker=broker,
        model_registry=REGISTRY, today_date="2026-07-27", now_ts=time.time(),
        journal_dir=journal_dir, kill_path=tmp_path / "KILL", last_run_path=tmp_path / "last_run.json",
        starting_equity_usd=28.0,
    )

    assert result.action_taken == "flat"
    record = json.loads((journal_dir / "equity.jsonl").read_text(encoding="utf-8").strip())
    assert record["has_open_position"] is False


def test_load_and_save_journal_state_roundtrip(tmp_path):
    journal_dir = tmp_path / "journal"
    state = JournalState(
        equity_usd=30.5,
        peak_equity_usd=32.0,
        open_position=None,
        breaker=BreakerState(consecutive_losses=2, halving_remaining=1),
    )
    save_journal_state(journal_dir, state)
    loaded = load_journal_state(journal_dir, starting_equity_usd=999.0)

    assert loaded.equity_usd == 30.5
    assert loaded.peak_equity_usd == 32.0
    assert loaded.breaker.consecutive_losses == 2
    assert loaded.breaker.halving_remaining == 1


def test_load_journal_state_defaults_when_no_file(tmp_path):
    loaded = load_journal_state(tmp_path / "journal", starting_equity_usd=28.0)
    assert loaded.equity_usd == 28.0
    assert loaded.peak_equity_usd == 28.0
    assert loaded.open_position is None
    assert loaded.breaker == BreakerState()

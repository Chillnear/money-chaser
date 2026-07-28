"""
Entrypoint รอบเทรดรายวัน — ประกอบทุกชั้น (data -> features -> screening -> agents -> risk -> execution
-> journal) เป็น 1 รอบ ตาม BUILD-SPEC.md §2 (ลำดับขั้น 1-11; ขั้น 12 baseline_shadow และ 13 commit state
ทำนอก pipeline นี้ — baseline_shadow เรียกแยกได้ถ้ามี src/baseline.py (P4.2), commit เป็นหน้าที่ของ
GitHub Actions workflow ไม่ใช่โค้ด Python)

ออกแบบให้ inject ทุก client/broker เข้ามาได้ (dependency injection) เพื่อเทสได้เต็มที่โดยไม่พึ่ง network จริง
— sandbox พัฒนาบล็อก outbound ทั้งหมด ต้อง smoke test อีกรอบบน GitHub Actions ก่อนใช้งานจริง (เหมือนไฟล์
data/*.py และ agents/llm.py ก่อนหน้านี้)
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from src.agents.analysts import AgentRunResult, run_analyst
from src.agents.judge import run_judge
from src.agents.llm import (
    DEGRADE_LLM_OFF,
    LLMClient,
    compute_spend,
    get_degradation_level,
    roles_active_at_level,
)
from src.agents.redteam import run_redteam
from src.agents.registry import assert_provider_diversity
from src.baseline import BASELINE_RISK_MULTIPLIER
from src.baseline import decide as baseline_decide
from src.data.combo_signals import classify_combination_pattern
from src.data.features import build_price_features, render_feature_table
from src.data.hl_market import HyperliquidClient
from src.data.oi_tracker import OI_HISTORY_FILENAME, compute_oi_change_pct, load_oi_history, record_oi_snapshot
from src.data.regime import classify_regime
from src.data.screening import build_shortlist
from src.execution.broker_base import BrokerBase, Position
from src.execution.reconcile import has_run_today, mark_run_complete, reconcile_equity
from src.risk.breaker import (
    BreakerState,
    apply_trade_result,
    is_killed,
    is_paused,
    should_trigger_kill,
    size_multiplier,
    write_kill_file,
)
from src.risk.rules import FUNDING_PERIODS_PER_YEAR, evaluate_all_gates
from src.risk.sizing import compute_position_size
from src.settings import STATE_DIR, Settings
from src.util.io import append_jsonl, load_json, load_jsonl, save_json

JOURNAL_DIR = STATE_DIR / "journal"
KILL_PATH = STATE_DIR / "KILL"
LAST_RUN_PATH = STATE_DIR / "last_run.json"

ANALYST_ROLE_ORDER = ["analyst_trend", "analyst_positioning", "analyst_macro"]


@dataclass
class JournalState:
    """สถานะที่ต้องคงอยู่ข้ามวัน — persist ที่ state/journal/state.json (CI commit กลับทุกวัน)"""

    equity_usd: float
    peak_equity_usd: float
    open_position: dict | None  # serialize ของ execution.broker_base.Position หรือ None ถ้าไม่มีไม้เปิดอยู่
    breaker: BreakerState


def load_journal_state(journal_dir: Path, starting_equity_usd: float) -> JournalState:
    raw = load_json(journal_dir / "state.json", default=None)
    if raw is None:
        return JournalState(
            equity_usd=starting_equity_usd,
            peak_equity_usd=starting_equity_usd,
            open_position=None,
            breaker=BreakerState(),
        )
    return JournalState(
        equity_usd=raw["equity_usd"],
        peak_equity_usd=raw["peak_equity_usd"],
        open_position=raw.get("open_position"),
        breaker=BreakerState(**raw["breaker"]),
    )


def save_journal_state(journal_dir: Path, state: JournalState) -> None:
    save_json(
        journal_dir / "state.json",
        {
            "equity_usd": state.equity_usd,
            "peak_equity_usd": state.peak_equity_usd,
            "open_position": state.open_position,
            "breaker": asdict(state.breaker),
        },
    )


@dataclass
class DailyRunResult:
    date: str
    action_taken: str
    reason: str
    equity_usd: float
    open_position: dict | None = None  # P5.6: ให้ LINE morning report บอก "ตอนนี้ถือไม้อะไรอยู่" ได้ด้วย


def _agent_result_to_log_dict(r: AgentRunResult) -> dict:
    """แปลง AgentRunResult (มี pydantic model ซ้อนอยู่ใน .output) เป็น dict ที่ json.dumps ได้ตรงๆ"""
    return {
        "role": r.role,
        "model": r.model,
        "provider": r.provider,
        "output": r.output.model_dump() if r.output else None,
        "abstained": r.abstained,
        "error": r.error,
        "cost_usd": r.cost_usd,
        "latency_ms": r.latency_ms,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "attempts": r.attempts,
    }


def log_llm_costs(journal_dir: Path, today_date: str, now_ts: float, agent_results: list, judge_result=None) -> None:
    """บันทึกต้นทุน LLM ทุก call ลง llm_cost.jsonl (BUILD-SPEC.md §4.2: "log ทุก call: role, model,
    tokens in/out, cost ประมาณ, latency")

    สำคัญ: ถ้าไม่เขียนไฟล์นี้ cost governor จะอ่านได้ค่าว่างตลอด แปลว่า degradation ladder
    จะไม่มีวันทำงาน และงบ LLM จะบานโดยไม่มีอะไรเบรก
    """
    records = []
    for r in agent_results:
        records.append(
            {
                "ts": now_ts,
                "date": today_date,
                "role": r.role,
                "model": r.model,
                "provider": r.provider,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "attempts": r.attempts,
                "abstained": r.abstained,
            }
        )
    if judge_result is not None:
        records.append(
            {
                "ts": now_ts,
                "date": today_date,
                "role": "judge",
                "model": judge_result.model,
                "provider": judge_result.provider,
                "cost_usd": judge_result.cost_usd,
                "latency_ms": judge_result.latency_ms,
                "tokens_in": judge_result.tokens_in,
                "tokens_out": judge_result.tokens_out,
                "attempts": judge_result.attempts,
                "abstained": judge_result.abstained,
            }
        )
    for record in records:
        append_jsonl(journal_dir / "llm_cost.jsonl", record)


def _finish(
    journal_dir: Path,
    last_run_path: Path,
    journal_state: JournalState,
    today_date: str,
    action: str,
    reason: str,
) -> DailyRunResult:
    """path ปิดท้ายที่ทุก branch ของ pipeline ใช้ร่วมกัน: save state + บันทึก equity + mark run + คืนผลสรุป"""
    save_journal_state(journal_dir, journal_state)
    append_jsonl(
        journal_dir / "equity.jsonl",
        {
            "ts": time.time(),
            "date": today_date,
            "equity_usd": journal_state.equity_usd,
            "peak_equity_usd": journal_state.peak_equity_usd,
            "action": action,
            "has_open_position": journal_state.open_position is not None,
        },
    )
    mark_run_complete(last_run_path, today_date, {"action": action})
    return DailyRunResult(
        date=today_date,
        action_taken=action,
        reason=reason,
        equity_usd=journal_state.equity_usd,
        open_position=journal_state.open_position,
    )


def manage_existing_position(
    settings: Settings,
    hl_client: HyperliquidClient,
    broker: BrokerBase,
    journal_state: JournalState,
    journal_dir: Path,
    last_run_path: Path,
    today_date: str,
    now_ts: float,
) -> DailyRunResult:
    """ขั้น 3 (manage_existing) ของ BUILD-SPEC.md §2 — มีไม้เปิดอยู่ ให้เช็ค exit เท่านั้น ไม่เปิดไม้ใหม่

    หมายเหตุ: thesis invalidation (กรณีที่ 3 ของ §2b) ยังไม่ implement ในเวอร์ชันนี้ — ต้องให้ judge
    ประเมิน invalidation condition ใหม่ทุกวันที่มีไม้เปิดอยู่ ซึ่งเป็น LLM call เพิ่มที่ยังไม่ได้ทำ
    (invalidation_triggered=False เสมอตอนนี้ — SL/TP/time exit ยังทำงานตามปกติ, เป็น fail-closed ที่ปลอดภัย
    กว่าการเดา invalidation ผิดๆ)
    """
    position = Position(**journal_state.open_position)
    candles = hl_client.get_candles(position.asset, interval="1d", lookback_days=2)
    latest = candles[-1]
    candle_high, candle_low = float(latest["h"]), float(latest["l"])
    mid_price = float(latest["c"])

    exit_decision = broker.evaluate_exit(
        position,
        candle_high,
        candle_low,
        now_ts,
        settings.risk.stops.max_holding_days,
        invalidation_triggered=False,
    )

    if not exit_decision.should_exit:
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "held_position",
            f"ถือ {position.asset} ({position.side}) ต่อ — ยังไม่ชนเงื่อนไขปิดไม้ใดๆ",
        )

    if exit_decision.reason == "stop_loss_hit":
        exit_price = position.stop_price
    elif exit_decision.reason == "take_profit_hit":
        exit_price = position.take_profit_price
    else:
        exit_price = mid_price  # time_exit / thesis_invalidated ปิดที่ราคาตลาดปัจจุบัน

    closed_trade = broker.close_position(position, exit_price, now_ts, exit_decision.reason)
    journal_state.breaker = apply_trade_result(
        journal_state.breaker,
        closed_trade.pnl_usd,
        settings.risk.breakers.consecutive_losses_halve_size,
        now_ts=now_ts,
    )
    journal_state.equity_usd = broker.get_account_equity()
    journal_state.peak_equity_usd = max(journal_state.peak_equity_usd, journal_state.equity_usd)
    journal_state.open_position = None

    append_jsonl(journal_dir / "trades.jsonl", asdict(closed_trade))

    if should_trigger_kill(journal_state.peak_equity_usd, journal_state.equity_usd, settings.risk.breakers.max_drawdown_pct):
        write_kill_file(
            KILL_PATH,
            f"drawdown {settings.risk.breakers.max_drawdown_pct}% จาก peak equity {journal_state.peak_equity_usd:.2f} "
            f"ถูกชน (equity ปัจจุบัน {journal_state.equity_usd:.2f})",
        )

    return _finish(
        journal_dir, last_run_path, journal_state, today_date, "closed_position",
        f"ปิดไม้ {position.asset} เหตุผล={exit_decision.reason} pnl={closed_trade.pnl_usd:.2f} USD",
    )


def run_daily_pipeline(
    settings: Settings,
    hl_client: HyperliquidClient,
    llm_client: LLMClient,
    broker: BrokerBase,
    model_registry: dict,
    today_date: str,
    now_ts: float | None = None,
    journal_dir: Path = JOURNAL_DIR,
    kill_path: Path = KILL_PATH,
    last_run_path: Path = LAST_RUN_PATH,
    starting_equity_usd: float = 28.0,
    macro_snapshot: dict | None = None,
    macro_veto_status: dict | None = None,
    sentiment: dict | None = None,
    news_headline_titles: list[str] | None = None,
    llm_cost_records: list[dict] | None = None,
    lessons_text: str = "",
    hit_rate_by_role: dict | None = None,
) -> DailyRunResult:
    now_ts = now_ts if now_ts is not None else time.time()

    # 1. preflight — KILL file, run lock, breaker pause (BUILD-SPEC.md §2 ขั้น 1)
    if is_killed(kill_path):
        return DailyRunResult(today_date, "skipped_killed", "พบไฟล์ KILL — ต้องให้มนุษย์ตรวจสอบและลบไฟล์เองก่อนรันต่อ", 0.0)

    if has_run_today(last_run_path, today_date):
        return DailyRunResult(today_date, "skipped_already_ran", f"รันสำเร็จไปแล้ววันที่ {today_date} (idempotency)", 0.0)

    # diversity check ต้องผ่านก่อนเรียก agent ใดๆ (BUILD-SPEC.md §4.1) — raise RegistryError ตรงๆถ้าไม่ผ่าน
    assert_provider_diversity(model_registry)

    journal_state = load_journal_state(journal_dir, starting_equity_usd)

    if is_paused(journal_state.breaker, now_ts):
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "skipped_paused",
            journal_state.breaker.pause_reason or "อยู่ในช่วงพัก breaker",
        )

    # 1b. macro event veto — ห้ามเทรดวันมีข่าวมหภาคสำคัญ (P5.2, ตามที่ผู้ใช้เลือกรับจาก playbook)
    # เช็คก่อนเริ่มเก็บข้อมูล/เรียก LLM เลย เพราะกฎคือ "ห้ามเทรดทั้งวัน" ไม่ใช่แค่ veto ไม้สุดท้าย —
    # ประหยัดค่า LLM ด้วยในวันที่รู้อยู่แล้วว่าจะไม่เทรด
    # fail-safe: macro_veto_status เป็น None หรือ vetoed=False (รวมถึงกรณีดึงข้อมูลไม่ได้) = ไม่บล็อก
    if macro_veto_status and macro_veto_status.get("vetoed"):
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "skipped_macro_event",
            macro_veto_status.get("reason", "วันนี้มีข่าวมหภาคสำคัญ — ข้ามการเทรดเพื่อความปลอดภัย"),
        )

    # 2. reconcile — ในโหมด paper journal คือ source of truth เดียวกับ broker (ไม่มี state อิสระให้เทียบ)
    # เทียบได้แค่ equity; broker_hl.py (P6) จะเพิ่ม position reconcile จริงจาก clearinghouseState
    broker_equity = broker.get_account_equity()
    equity_reconcile = reconcile_equity(journal_state.equity_usd, broker_equity)
    if not equity_reconcile.matched:
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "skipped_reconcile_mismatch",
            equity_reconcile.reason,
        )

    # 3. manage_existing — มีไม้เปิดอยู่ = ข้ามขั้น 4-10 ทั้งหมด (max 1 position/วัน)
    if journal_state.open_position is not None:
        return manage_existing_position(
            settings, hl_client, broker, journal_state, journal_dir, last_run_path, today_date, now_ts
        )

    # 4-5. collect_data + build_features (ต่อทุกตลาดใน universe pool)
    universe_snapshot = hl_client.get_universe_snapshot()
    features_cfg = settings.app["features"]
    candle_lookback = settings.app["data"]["candle_lookback"]

    # P5.3: โหลดประวัติ OI ที่เก็บสะสมไว้เอง (Hyperliquid ไม่มี endpoint ประวัติ OI ให้) เพื่อคำนวณ
    # % เปลี่ยนแปลง 24h/7d แล้วจับคู่กับ price/funding/volume เป็น "combination read" — ดู
    # src/data/oi_tracker.py กับ src/data/combo_signals.py สำหรับรายละเอียด
    oi_history_path = journal_dir / OI_HISTORY_FILENAME
    oi_history = load_oi_history(oi_history_path)
    funding_by_coin = {entry["coin"]: entry.get("funding", 0.0) for entry in universe_snapshot}
    oi_usd_by_coin = {entry["coin"]: entry.get("open_interest_usd") for entry in universe_snapshot}

    price_features_by_coin: dict[str, dict] = {}
    regime_by_coin: dict[str, dict] = {}
    for entry in universe_snapshot:
        coin = entry["coin"]
        try:
            candles = hl_client.get_candles(coin, interval="1d", lookback_days=candle_lookback)
            pf = build_price_features(candles, features_cfg)
        except Exception as exc:  # noqa: BLE001 - ข้อมูลขาดของตลาดใดตลาดหนึ่งไม่ควรทำ pipeline ทั้งหมดล้ม
            pf = {"ok": False, "error": str(exc)}

        if pf.get("ok"):
            current_oi_usd = oi_usd_by_coin.get(coin)
            oi_change_24h_pct = compute_oi_change_pct(oi_history, coin, current_oi_usd, today_date, lookback_days=1)
            oi_change_7d_pct = compute_oi_change_pct(oi_history, coin, current_oi_usd, today_date, lookback_days=7)
            funding_annualized_signed_pct = funding_by_coin.get(coin, 0.0) * FUNDING_PERIODS_PER_YEAR * 100
            combo = classify_combination_pattern(
                price_return_24h_pct=pf.get("returns_pct", {}).get(1),
                oi_change_24h_pct=oi_change_24h_pct,
                funding_annualized_signed_pct=funding_annualized_signed_pct,
                volume_spike_ratio=pf.get("volume_spike_ratio"),
            )
            pf["oi_change_24h_pct"] = oi_change_24h_pct
            pf["oi_change_7d_pct"] = oi_change_7d_pct
            pf["combination_pattern"] = combo.pattern
            pf["combination_pattern_label"] = combo.label

        price_features_by_coin[coin] = pf
        if pf.get("ok"):
            regime_by_coin[coin] = classify_regime(pf)

    # เก็บ OI ของวันนี้ไว้เป็น "ประวัติ" ให้รอบพรุ่งนี้เทียบ — ทำหลังคำนวณ combo ของวันนี้เสร็จแล้วเท่านั้น
    # กันไม่ให้ snapshot ของวันนี้ถูกเอาไปเทียบกับตัวเองเป็น oi_change_24h_pct=0% ผิดๆ
    record_oi_snapshot(oi_history_path, today_date, universe_snapshot)

    # 5b. screen — คัดจากพูลทั้งหมดเหลือ top N (โค้ดล้วน ไม่มี LLM)
    shortlist_result = build_shortlist(universe_snapshot, price_features_by_coin, settings.risk.mode_defaults.model_dump())

    allowed_assets = [item["coin"] for item in shortlist_result["shortlist"]]
    if shortlist_result.get("pinned_extra"):
        allowed_assets.append(shortlist_result["pinned_extra"]["coin"])

    if not allowed_assets:
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "flat",
            "ไม่มีผู้เข้าชิงผ่าน screening วันนี้ (universe pool บางผิดปกติ) — fail-closed เป็น FLAT",
        )

    llm_cfg = settings.app.raw.get("llm", {})
    feature_table = render_feature_table(
        shortlist_result,
        price_features_by_coin,
        regime_by_coin,
        sentiment=sentiment,
        macro_snapshot=macro_snapshot,
        news_headline_titles=news_headline_titles,
        token_cap=llm_cfg.get("input_token_cap", 3000),
    )
    feature_table_markdown = feature_table["markdown"]

    # 6-8. agents — เคารพ degradation ladder ของ cost governor (BUILD-SPEC.md §4.2)
    daily_spend = compute_spend(llm_cost_records or [], since_ts=now_ts - 86400, until_ts=now_ts)
    monthly_spend = compute_spend(llm_cost_records or [], since_ts=now_ts - 31 * 86400, until_ts=now_ts)
    degrade_level = get_degradation_level(
        daily_spend, monthly_spend, settings.risk.llm_budget.daily_soft_cap_usd, settings.risk.llm_budget.hard_stop_usd
    )

    require_analyst_agreement = True

    if degrade_level >= DEGRADE_LLM_OFF:
        # ปิด LLM ทั้งหมดเพราะเกินงบเดือน (DEGRADE_LLM_OFF) -> ใช้ src/baseline.py แทน (P4.2) ระบบยัง
        # เทรดต่อได้ ไม่ fail-closed เป็น FLAT เปล่าๆ (ตาม BUILD-SPEC.md §4.2: "ปิด LLM ใช้ baseline.py
        # ล้วน ระบบยังเทรดต่อได้ ไม่หยุดตาย") — ไม่มี analyst มา debate จริง จึงข้าม agreement gate
        baseline_decision = baseline_decide(
            shortlist_result["shortlist"],
            regime_by_coin,
            default_stop_pct=settings.risk.stops.stop_floor_pct,
            default_take_profit_pct=settings.risk.stops.stop_floor_pct * settings.risk.stops.reward_risk_ratio,
        )
        final_action = baseline_decision.action
        final_asset = baseline_decision.asset
        final_confidence = baseline_decision.confidence
        analyst_directions = []
        require_analyst_agreement = False

        append_jsonl(
            journal_dir / "decisions.jsonl",
            {
                "date": today_date,
                "ts": now_ts,
                "source": "baseline",
                "shortlist": shortlist_result["shortlist"],
                "pinned_extra": shortlist_result.get("pinned_extra"),
                "baseline_decision": asdict(baseline_decision),
                "degrade_level": degrade_level,
            },
        )
    else:
        active_roles = set(roles_active_at_level(degrade_level))

        analyst_results = [
            run_analyst(role, llm_client, model_registry, feature_table_markdown, lessons_text)
            for role in ANALYST_ROLE_ORDER
            if role in active_roles
        ]

        if "redteam" in active_roles:
            redteam_result = run_redteam(llm_client, model_registry, feature_table_markdown, analyst_results, lessons_text)
        else:
            redteam_result = AgentRunResult(
                role="redteam",
                model="-",
                provider="-",
                output=None,
                abstained=True,
                error="redteam ถูกตัดออกตาม cost governor degradation ladder วันนี้",
                cost_usd=0.0,
                latency_ms=0.0,
                tokens_in=0,
                tokens_out=0,
                attempts=0,
            )

        if "judge" not in active_roles:
            # เผื่อ edge case (ปกติ DEGRADE_LLM_OFF ข้างบนจะจับไปแล้ว) — fail-closed อีกชั้น
            return _finish(
                journal_dir, last_run_path, journal_state, today_date, "flat",
                "judge ไม่ active ตาม degradation ladder วันนี้ — fail-closed เป็น FLAT",
            )

        judge_result = run_judge(
            llm_client,
            model_registry,
            feature_table_markdown,
            allowed_assets,
            analyst_results,
            redteam_result,
            lessons_text=lessons_text,
            hit_rate_by_role=hit_rate_by_role,
        )

        append_jsonl(
            journal_dir / "decisions.jsonl",
            {
                "date": today_date,
                "ts": now_ts,
                "source": "llm",
                "shortlist": shortlist_result["shortlist"],
                "pinned_extra": shortlist_result.get("pinned_extra"),
                "rest_summary": shortlist_result.get("rest_summary", []),
                "regime_by_coin": regime_by_coin,
                "analyst_results": [_agent_result_to_log_dict(r) for r in analyst_results],
                "redteam_result": _agent_result_to_log_dict(redteam_result),
                "judge_output": judge_result.output.model_dump() if judge_result.output else None,
                "judge_abstained": judge_result.abstained,
                "judge_error": judge_result.error,
                "degrade_level": degrade_level,
            },
        )

        log_llm_costs(journal_dir, today_date, now_ts, analyst_results + [redteam_result], judge_result)

        if judge_result.abstained or judge_result.output is None:
            return _finish(
                journal_dir, last_run_path, journal_state, today_date, "flat", f"judge abstain: {judge_result.error}"
            )

        judge_output = judge_result.output
        final_action = judge_output.action
        final_asset = judge_output.asset
        final_confidence = judge_output.confidence
        analyst_directions = [
            c.direction
            for r in analyst_results
            if not r.abstained and r.output
            for c in r.output.candidates
            if c.asset == judge_output.asset
        ]

    # 9. risk_gate — sizing + hard rules ตัดสินใจสุดท้าย (โค้ดล้วน, veto ไม่ใช่ที่ปรึกษา — non-negotiable ข้อ 4)
    # ใช้ path เดียวกันไม่ว่า decision จะมาจาก LLM judge หรือ baseline.py (fallback) — risk engine ต้อง
    # veto ได้เท่ากันเสมอ ไม่ว่าที่มาของ decision จะเป็นอะไร
    shortlist_coins = set(allowed_assets)
    current_funding_rate = (
        next((e["funding"] for e in universe_snapshot if e["coin"] == final_asset), None)
        if final_asset
        else None
    )

    gate_result = evaluate_all_gates(
        judge_action=final_action,
        judge_asset=final_asset,
        judge_confidence=final_confidence,
        analyst_directions=analyst_directions,
        shortlist_coins=shortlist_coins,
        universe_whitelist=shortlist_coins,
        current_funding_rate=current_funding_rate,
        gates_cfg=settings.risk.gates.model_dump(),
        require_analyst_agreement=require_analyst_agreement,
    )

    if final_action == "flat":
        return _finish(journal_dir, last_run_path, journal_state, today_date, "flat", "ตัดสินใจ FLAT")

    if not gate_result.passed:
        return _finish(journal_dir, last_run_path, journal_state, today_date, "flat", gate_result.reason)

    # 10. execute — sizing + เปิดไม้ผ่าน broker adapter (paper ตาม MODE ปัจจุบัน; live รอ broker_hl.py ที่ P6)
    atr_pct_value = price_features_by_coin.get(final_asset, {}).get("atr_pct")
    breaker_size_mult = size_multiplier(journal_state.breaker)
    baseline_risk_mult = BASELINE_RISK_MULTIPLIER if degrade_level >= DEGRADE_LLM_OFF else 1.0

    sizing_result = compute_position_size(
        equity_usd=journal_state.equity_usd,
        atr_pct=atr_pct_value,
        risk_per_trade_pct=settings.risk.sizing.risk_per_trade_pct * breaker_size_mult * baseline_risk_mult,
        min_notional_usd=settings.risk.sizing.min_notional_usd,
        max_notional_usd=settings.risk.sizing.max_notional_usd,
        max_notional_pct_of_equity=settings.risk.sizing.max_notional_pct_of_equity,
        min_notional_override_max_risk_pct=settings.risk.sizing.min_notional_override_max_risk_pct,
        atr_multiple=settings.risk.stops.atr_multiple,
        stop_floor_pct=settings.risk.stops.stop_floor_pct,
        stop_cap_pct=settings.risk.stops.stop_cap_pct,
        reward_risk_ratio=settings.risk.stops.reward_risk_ratio,
        max_leverage=settings.risk.mode_defaults.max_leverage,
    )

    if sizing_result.decision == "FLAT":
        return _finish(journal_dir, last_run_path, journal_state, today_date, "flat", sizing_result.reason)

    mid_price = next((e["mark_px"] for e in universe_snapshot if e["coin"] == final_asset), None)
    if not mid_price:
        return _finish(
            journal_dir, last_run_path, journal_state, today_date, "flat",
            f"ไม่พบ mark price ของ {final_asset} ใน universe snapshot — fail-closed เป็น FLAT",
        )

    position = broker.open_position(
        asset=final_asset,
        side=final_action,
        notional_usd=sizing_result.notional_usd,
        mid_price=mid_price,
        stop_pct=sizing_result.stop_pct,
        take_profit_pct=sizing_result.take_profit_pct,
        now_ts=now_ts,
    )

    journal_state.open_position = asdict(position)

    return _finish(
        journal_dir, last_run_path, journal_state, today_date, f"opened_{final_action}",
        f"เปิดไม้ {final_asset} {final_action} notional={sizing_result.notional_usd:.2f} USD "
        f"stop={sizing_result.stop_pct:.2f}% tp={sizing_result.take_profit_pct:.2f}%",
    )


def _cli_main() -> None:  # pragma: no cover - เรียกจริงบน GitHub Actions เท่านั้น (ต้องมี network จริง)
    """เรียกจาก `python -m src.main` โดย GitHub Actions daily.yml (P4.4) — ประกอบ client จริงทั้งหมด
    (ไม่ inject mock เหมือน unit test) แล้วรัน pipeline 1 รอบ + ส่งสรุปผลเข้า LINE เป็นขั้นตอนสุดท้าย

    หมายเหตุ: broker_hl.py (P6) ยังไม่ implement ในเวอร์ชันนี้ — ใช้ PaperBroker เสมอไม่ว่า MODE จะเป็น
    อะไร ถ้า MODE=live จะแค่ log เตือนแล้ว fallback เป็น paper (fail-closed ตาม BUILD-SPEC.md §6:
    "ถ้า MODE=live แต่ข้อใดข้อหนึ่งไม่ผ่าน -> ระบบ fallback เป็น paper อัตโนมัติ + แจ้งเตือน")
    """
    import datetime

    from src.agents.registry import load_model_registry
    from src.data.econ_calendar import EconCalendarClient
    from src.data.macro import MacroClient
    from src.data.news import NewsClient, merge_with_cryptopanic
    from src.data.sentiment import SentimentClient
    from src.report.notify import (
        LineNotifier,
        format_daily_summary,
        notify_budget_thresholds,
    )
    from src.settings import CONFIG_DIR, REPO_ROOT, load_settings

    settings = load_settings()

    if settings.is_live():
        print("[main] MODE=live แต่ broker_hl.py (P6) ยังไม่ implement — fallback เป็น paper (fail-closed)")

    model_registry = load_model_registry(CONFIG_DIR / "models.yaml")
    hl_client = HyperliquidClient()
    llm_client = LLMClient(
        base_url=settings.secrets.litellm_base_url,
        api_keys=[k for k in [settings.secrets.litellm_key_1, settings.secrets.litellm_key_2] if k],
        input_token_cap=settings.app.raw.get("llm", {}).get("input_token_cap", 8000),
        output_token_cap=settings.app.raw.get("llm", {}).get("output_token_cap", 1500),
    )
    broker = _load_or_create_paper_broker(settings)

    # macro/sentiment/news เป็น best-effort ทั้งหมด — ล่มได้โดยไม่ทำให้ pipeline การเทรดล้ม (fail-soft)
    macro_snapshot = None
    try:
        macro_snapshot = MacroClient().get_macro_snapshot()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] macro data ดึงไม่ได้ (ไม่ critical): {exc}")

    sentiment = None
    try:
        sentiment = SentimentClient().get_fear_greed()
    except Exception as exc:  # noqa: BLE001
        print(f"[main] fear&greed ดึงไม่ได้ (ไม่ critical): {exc}")

    macro_veto_status = None
    try:
        mv_cfg = settings.risk.macro_veto
        if mv_cfg.enabled:
            macro_veto_status = EconCalendarClient().get_veto_status(
                now_ts=time.time(),
                impact_levels=mv_cfg.impact_levels,
                countries=mv_cfg.countries,
                lookahead_hours=mv_cfg.lookahead_hours,
                lookback_hours=mv_cfg.lookback_hours,
            )
            if macro_veto_status.get("data_missing"):
                print(f"[main] {macro_veto_status['reason']}")
            elif macro_veto_status.get("vetoed"):
                print(f"[main] MACRO VETO: {macro_veto_status['reason']}")
    except Exception as exc:  # noqa: BLE001 - fail-safe เหมือนแหล่งข้อมูลเสริมอื่นๆ ไม่บล็อกการเทรด
        print(f"[main] เช็คปฏิทินข่าวมหภาคไม่ได้ (ไม่ critical, ไม่บล็อกการเทรด): {exc}")

    news_headline_titles: list[str] = []
    try:
        news_cfg = settings.app["macro_sources"]
        news_snapshot = NewsClient(news_cfg.get("news_rss", [])).get_recent_headlines()
        if settings.secrets.cryptopanic_api_key:
            from src.data.cryptopanic import CryptoPanicClient

            cp_posts = CryptoPanicClient(settings.secrets.cryptopanic_api_key).get_recent_posts(
                currencies=news_cfg.get("cryptopanic_currencies", "BTC,ETH,SOL")
            )
            if cp_posts.ok:
                news_snapshot = merge_with_cryptopanic(news_snapshot, cp_posts.posts)
        news_headline_titles = [h.title for h in news_snapshot.headlines]
    except Exception as exc:  # noqa: BLE001
        print(f"[main] ข่าวดึงไม่ได้ (ไม่ critical): {exc}")

    llm_cost_records = load_jsonl(JOURNAL_DIR / "llm_cost.jsonl")
    lessons_text = ""
    lessons_path = REPO_ROOT / "state" / "lessons.md"
    if lessons_path.exists():
        lessons_text = lessons_path.read_text(encoding="utf-8")

    today_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    result = run_daily_pipeline(
        settings=settings,
        hl_client=hl_client,
        llm_client=llm_client,
        broker=broker,
        model_registry=model_registry,
        today_date=today_date,
        llm_cost_records=llm_cost_records,
        lessons_text=lessons_text,
        macro_snapshot=macro_snapshot,
        macro_veto_status=macro_veto_status,
        sentiment=sentiment,
        news_headline_titles=news_headline_titles,
    )

    print(f"[main] {result.date} action={result.action_taken} reason={result.reason} equity={result.equity_usd:.2f}")

    notifier = LineNotifier(settings.secrets.line_channel_access_token, settings.secrets.line_user_id)
    notify_result = notifier.send_text(format_daily_summary(result))
    if not notify_result.sent:
        print(f"[main] แจ้งเตือน LINE ไม่สำเร็จ (ไม่กระทบผลการเทรด): {notify_result.reason}")

    monthly_spend = compute_spend(llm_cost_records, since_ts=time.time() - 31 * 86400, until_ts=time.time())
    notify_budget_thresholds(
        notifier,
        monthly_spend_usd=monthly_spend,
        monthly_hard_stop_usd=settings.risk.llm_budget.hard_stop_usd,
        thresholds_pct=settings.risk.llm_budget.degrade_thresholds_pct,
        already_notified_pct=[],  # TODO(P4.4+): เก็บสถานะที่แจ้งไปแล้วใน journal state กันแจ้งซ้ำวันถัดไป
    )


def _load_or_create_paper_broker(settings: Settings):
    """สร้าง PaperBroker โดยดึง starting equity จาก journal state เดิมถ้ามี ไม่งั้นใช้ค่า default 28 USD
    (เลี่ยง import วนกับ broker_paper.py ตอน module load — import ในฟังก์ชันนี้เท่านั้น)
    """
    from src.execution.broker_paper import PaperBroker

    existing = load_json(JOURNAL_DIR / "state.json", default=None)
    starting_equity = existing["equity_usd"] if existing else 28.0
    return PaperBroker(
        starting_equity_usd=starting_equity,
        taker_fee_pct=settings.risk.costs.taker_fee_pct,
        slippage_pct=settings.risk.costs.assumed_slippage_pct,
    )


if __name__ == "__main__":
    _cli_main()

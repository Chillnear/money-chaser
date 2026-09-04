"""
Dashboard — สร้าง static HTML สรุปสถานะระบบจาก journal (BUILD-SPEC.md §1: "dashboard.py: static HTML")
ไม่มี JS เรียก API ใดๆ ทั้งหมด render ตอน build time จาก state/journal/*.jsonl ที่มีอยู่แล้ว

P5.1: อัปเกรดเป็น dashboard ส่วนตัวที่ครบขึ้น — เพิ่มกราฟ equity, สถิติผลงาน, งบ LLM เทียบเพดาน,
สรุปความเห็นตรงกับ judge ของแต่ละ analyst, log การตัดสินใจแบบเต็ม (พับ/กางดูได้ด้วย <details> ล้วนๆ
ไม่ต้องใช้ JavaScript เลย) และเนื้อหา lessons.md — ยังคง "ไม่มี logic ตัดสินใจใดๆ ในไฟล์นี้" เหมือนเดิม
ข้อมูลดิบทั้งหมดมาจาก journal ที่ pipeline หลัก (src/main.py) เขียนไว้แล้ว

ไฟล์นี้ยังคง commit กลับเข้า repo ทุกวันโดย GitHub Actions (daily.yml) เหมือนเดิม แต่เพราะ repo เป็น
private และผู้ใช้เลือกไม่เปิด GitHub Pages (ต้องเสียเงินสำหรับ private repo) หน้านี้จึงไม่ถูกเผยแพร่ที่ไหน
เลย — เปิดดูได้แค่จากไฟล์ในเครื่องผู้ใช้เอง (docs/index.html) ถือว่า "ส่วนตัวและไม่เสียเงิน" ตามที่ขอไว้
"""
from __future__ import annotations

import html

MAX_ROWS = 30
MAX_DECISION_LOG_ROWS = 20
SCORECARD_ROLES = ["analyst_trend", "analyst_positioning", "analyst_macro", "redteam"]


def _fmt_num(value, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else "N/A"


def render_equity_summary_html(journal_state: dict | None) -> str:
    if not journal_state:
        return "<p>ยังไม่มีข้อมูล equity (ยังไม่รันรอบแรก)</p>"

    equity = journal_state.get("equity_usd")
    peak = journal_state.get("peak_equity_usd")
    open_position = journal_state.get("open_position")
    breaker = journal_state.get("breaker", {})

    drawdown_pct = 0.0
    if peak and peak > 0 and equity is not None:
        drawdown_pct = max(0.0, (peak - equity) / peak * 100)

    position_html = "<p>ไม่มีไม้เปิดอยู่</p>"
    if open_position:
        position_html = (
            f"<p><strong>ไม้เปิดอยู่:</strong> {_esc(open_position.get('asset'))} "
            f"{_esc(open_position.get('side'))} notional={_fmt_num(open_position.get('notional_usd'))} USD, "
            f"entry={_fmt_num(open_position.get('entry_price'))}, "
            f"SL={_fmt_num(open_position.get('stop_price'))}, "
            f"TP={_fmt_num(open_position.get('take_profit_price'))}</p>"
        )

    pause_html = ""
    if breaker.get("paused_until_ts") or breaker.get("weekly_pause_needs_ack"):
        pause_html = f"<p style='color:#b45309'><strong>⏸ พักการเทรด:</strong> {_esc(breaker.get('pause_reason'))}</p>"

    return f"""
    <div class="card">
      <h2>Equity</h2>
      <p><strong>ปัจจุบัน:</strong> {_fmt_num(equity)} USD &nbsp; <strong>Peak:</strong> {_fmt_num(peak)} USD
         &nbsp; <strong>Drawdown:</strong> {_fmt_num(drawdown_pct)}%</p>
      {position_html}
      {pause_html}
    </div>
    """


def render_equity_curve_html(equity_history: list[dict] | None) -> str:
    """กราฟ equity แบบ inline SVG ล้วนๆ (ไม่มี CDN/JS) — วาดจาก state/journal/equity.jsonl
    ที่ P5.0 เพิ่งเริ่มบันทึกทุก run (ก่อนหน้านั้นไม่มีไฟล์นี้เลย เพราะเป็น gap ที่เจอและแก้ไปแล้ว)
    """
    if not equity_history:
        return "<p>ยังไม่มีข้อมูล equity history พอวาดกราฟ (ต้องรันสะสมหลายวัน)</p>"

    points = [r for r in equity_history if r.get("equity_usd") is not None]
    if len(points) < 2:
        return "<p>ยังมีข้อมูล equity history น้อยเกินไปพอวาดกราฟ (ต้องมีอย่างน้อย 2 จุด)</p>"

    values = [p["equity_usd"] for p in points]
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0

    width, height, pad = 760, 180, 20
    plot_w, plot_h = width - 2 * pad, height - 2 * pad
    n = len(values)

    def _xy(i: int, v: float) -> tuple[float, float]:
        x = pad + (i / (n - 1)) * plot_w
        y = pad + plot_h - ((v - lo) / span) * plot_h
        return x, y

    coords = [_xy(i, v) for i, v in enumerate(values)]
    polyline_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    last_x, last_y = coords[-1]
    trending_up = values[-1] >= values[0]
    line_color = "#15803d" if trending_up else "#b91c1c"

    first_date = _esc(points[0].get("date"))
    last_date = _esc(points[-1].get("date"))

    return f"""
    <div class="card">
      <h2>กราฟ Equity ({len(values)} จุด)</h2>
      <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="กราฟ equity">
        <polyline points="{polyline_points}" fill="none" stroke="{line_color}" stroke-width="2" />
        <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{line_color}" />
        <text x="{pad}" y="{height - 4}" font-size="11" fill="#6b7280">{first_date}</text>
        <text x="{width - pad}" y="{height - 4}" font-size="11" fill="#6b7280" text-anchor="end">{last_date}</text>
        <text x="{pad}" y="14" font-size="11" fill="#6b7280">{_fmt_num(hi)}</text>
        <text x="{pad}" y="{height - pad + 4}" font-size="11" fill="#6b7280">{_fmt_num(lo)}</text>
      </svg>
    </div>
    """


def render_performance_stats_html(trades: list[dict]) -> str:
    if not trades:
        return "<p>ยังไม่มีไม้ที่ปิดเลย พอคำนวณสถิติ</p>"

    pnls = [t.get("pnl_usd", 0.0) or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    profit_factor_str = "∞" if profit_factor == float("inf") else _fmt_num(profit_factor)

    return f"""
    <div class="card">
      <h2>สถิติผลงาน ({len(trades)} ไม้ที่ปิดแล้ว)</h2>
      <div class="stat-grid">
        <div><span class="stat-label">Win rate</span><br>{_fmt_num(win_rate, 1)}%</div>
        <div><span class="stat-label">PnL รวม</span><br>{_fmt_num(total_pnl)} USD</div>
        <div><span class="stat-label">กำไรเฉลี่ย/ไม้ชนะ</span><br>{_fmt_num(avg_win)} USD</div>
        <div><span class="stat-label">ขาดทุนเฉลี่ย/ไม้แพ้</span><br>{_fmt_num(avg_loss)} USD</div>
        <div><span class="stat-label">Profit factor</span><br>{profit_factor_str}</div>
      </div>
    </div>
    """


def render_llm_budget_html(llm_cost_records: list[dict] | None, llm_budget_cfg: dict | None, now_ts: float | None = None) -> str:
    """งบ LLM เทียบเพดาน (daily soft cap / monthly hard stop) + แยกต้นทุนตาม role — จาก llm_cost.jsonl
    ที่ P5.0 เพิ่งเริ่มบันทึกทุก call จริง (ก่อนหน้านั้นไฟล์นี้ไม่มีข้อมูลเลย เพราะเป็น gap ที่เจอและแก้ไปแล้ว)
    """
    if not llm_cost_records:
        return "<p>ยังไม่มีข้อมูลต้นทุน LLM บันทึกไว้</p>"

    import time as _time

    now_ts = now_ts if now_ts is not None else _time.time()
    daily_records = [r for r in llm_cost_records if now_ts - 86400 <= r.get("ts", 0) < now_ts]
    monthly_records = [r for r in llm_cost_records if now_ts - 31 * 86400 <= r.get("ts", 0) < now_ts]
    daily_spend = sum(r.get("cost_usd", 0.0) for r in daily_records)
    monthly_spend = sum(r.get("cost_usd", 0.0) for r in monthly_records)

    daily_cap = (llm_budget_cfg or {}).get("daily_soft_cap_usd")
    monthly_cap = (llm_budget_cfg or {}).get("hard_stop_usd")

    def _bar(spend: float, cap: float | None, label: str) -> str:
        if not cap or cap <= 0:
            return f"<p><strong>{label}:</strong> {_fmt_num(spend, 4)} USD (ไม่ได้ตั้งเพดาน)</p>"
        pct = min(100.0, spend / cap * 100)
        color = "#15803d" if pct < 70 else "#b45309" if pct < 90 else "#b91c1c"
        return f"""
        <p><strong>{label}:</strong> {_fmt_num(spend, 4)} / {_fmt_num(cap, 2)} USD ({_fmt_num(pct, 0)}%)</p>
        <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>
        """

    by_role: dict[str, dict] = {}
    for r in llm_cost_records:
        role = r.get("role", "?")
        acc = by_role.setdefault(role, {"calls": 0, "cost": 0.0, "latency_sum": 0.0, "abstained": 0})
        acc["calls"] += 1
        acc["cost"] += r.get("cost_usd", 0.0) or 0.0
        acc["latency_sum"] += r.get("latency_ms", 0.0) or 0.0
        if r.get("abstained"):
            acc["abstained"] += 1

    rows = []
    for role, acc in sorted(by_role.items(), key=lambda kv: -kv[1]["cost"]):
        avg_latency = acc["latency_sum"] / acc["calls"] if acc["calls"] else 0.0
        abstain_rate = acc["abstained"] / acc["calls"] * 100 if acc["calls"] else 0.0
        rows.append(
            f"<tr><td>{_esc(role)}</td><td>{acc['calls']}</td><td>{_fmt_num(acc['cost'], 4)}</td>"
            f"<td>{_fmt_num(avg_latency, 0)}</td><td>{_fmt_num(abstain_rate, 0)}%</td></tr>"
        )

    return f"""
    <div class="card">
      <h2>งบ LLM</h2>
      {_bar(daily_spend, daily_cap, "ใช้ไปวันนี้ / soft cap รายวัน")}
      {_bar(monthly_spend, monthly_cap, "ใช้ไปเดือนนี้ / hard stop รายเดือน")}
      <table>
        <thead><tr><th>Role</th><th>เรียกกี่ครั้ง</th><th>ต้นทุนรวม (USD)</th><th>latency เฉลี่ย (ms)</th><th>อัตรา abstain</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_agent_scorecard_html(decisions: list[dict]) -> str:
    """สรุปว่า analyst/redteam แต่ละตัว "เห็นตรงกับ judge" บ่อยแค่ไหน — เป็นตัวเลขที่คำนวณตรงจาก
    decisions.jsonl ที่มีอยู่แล้ว (ไม่ใช่ Brier score/EWMA weight เต็มรูปแบบตาม src/store/scoring.py
    ซึ่งยังไม่มีการเก็บ actual outcome ต่อ analyst แยกต่างหาก — เลขนี้เป็นตัวชี้วัดคร่าวๆ เพื่อดูภาพรวม
    ไม่ใช่ตัวเดียวกับที่ใช้ถ่วงน้ำหนักจริงใน judge prompt)
    """
    llm_decisions = [d for d in decisions if d.get("source") == "llm" and d.get("judge_output")]
    if not llm_decisions:
        return "<p>ยังไม่มี decision จาก LLM พอสรุปภาพรวม analyst</p>"

    stats = {role: {"n": 0, "agree": 0} for role in SCORECARD_ROLES}
    for d in llm_decisions:
        judge_output = d.get("judge_output") or {}
        judge_asset = judge_output.get("asset")
        judge_action = judge_output.get("action")
        if not judge_asset or judge_action == "flat":
            continue

        role_results = {r["role"]: r for r in d.get("analyst_results", [])}
        if d.get("redteam_result"):
            role_results["redteam"] = d["redteam_result"]

        for role in SCORECARD_ROLES:
            r = role_results.get(role)
            if not r or r.get("abstained") or not r.get("output"):
                continue
            candidates = r["output"].get("candidates", [])
            match = next((c for c in candidates if c.get("asset") == judge_asset), None)
            if match is None:
                continue
            stats[role]["n"] += 1
            if match.get("direction") == judge_action:
                stats[role]["agree"] += 1

    rows = []
    for role in SCORECARD_ROLES:
        n = stats[role]["n"]
        agree_pct = stats[role]["agree"] / n * 100 if n else 0.0
        rows.append(f"<tr><td>{_esc(role)}</td><td>{n}</td><td>{_fmt_num(agree_pct, 0)}%</td></tr>")

    return f"""
    <div class="card">
      <h2>ภาพรวม Analyst (เห็นตรงกับ judge บ่อยแค่ไหน)</h2>
      <p class="hint">* วัดจาก {len(llm_decisions)} decision ที่มีอยู่ — ยิ่งข้อมูลน้อย ยิ่งไม่ควรเชื่อเลขนี้มาก</p>
      <table>
        <thead><tr><th>Role</th><th>จำนวนที่เทียบได้</th><th>% เห็นตรงกับ judge</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_trades_table_html(trades: list[dict]) -> str:
    if not trades:
        return "<p>ยังไม่มีไม้ที่ปิดเลย</p>"

    rows = []
    for t in trades[-MAX_ROWS:][::-1]:
        pnl = t.get("pnl_usd", 0.0) or 0.0
        color = "#15803d" if pnl >= 0 else "#b91c1c"
        rows.append(
            f"<tr><td>{_esc(t.get('asset'))}</td><td>{_esc(t.get('side'))}</td>"
            f"<td>{_fmt_num(t.get('notional_usd'))}</td>"
            f"<td style='color:{color}'>{_fmt_num(pnl)}</td>"
            f"<td>{_esc(t.get('exit_reason'))}</td></tr>"
        )

    return f"""
    <div class="card">
      <h2>ไม้ที่ปิดล่าสุด ({len(trades)} รายการทั้งหมด)</h2>
      <table>
        <thead><tr><th>Asset</th><th>Side</th><th>Notional (USD)</th><th>PnL (USD)</th><th>เหตุผลปิด</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_decisions_table_html(decisions: list[dict]) -> str:
    if not decisions:
        return "<p>ยังไม่มี decision ที่บันทึกไว้</p>"

    rows = []
    for d in decisions[-MAX_ROWS:][::-1]:
        source = d.get("source", "?")
        if source == "llm":
            judge_output = d.get("judge_output") or {}
            action = judge_output.get("action", "-")
            asset = judge_output.get("asset", "-")
        else:
            baseline = d.get("baseline_decision") or {}
            action = baseline.get("action", "-")
            asset = baseline.get("asset", "-")
        rows.append(
            f"<tr><td>{_esc(d.get('date'))}</td><td>{_esc(source)}</td>"
            f"<td>{_esc(action)}</td><td>{_esc(asset)}</td><td>{_esc(d.get('degrade_level'))}</td></tr>"
        )

    return f"""
    <div class="card">
      <h2>Decision ล่าสุด ({len(decisions)} รายการทั้งหมด)</h2>
      <table>
        <thead><tr><th>วันที่</th><th>ที่มา</th><th>Action</th><th>Asset</th><th>Degrade level</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _render_candidates_list_html(candidates: list[dict]) -> str:
    if not candidates:
        return "<p class='hint'>ไม่มี candidate</p>"
    items = []
    for c in candidates:
        items.append(
            f"<li><strong>{_esc(c.get('asset'))} {_esc(c.get('direction'))}</strong> "
            f"(confidence {_esc(c.get('confidence'))}) — {_esc(c.get('thesis'))} "
            f"<em>[invalidation: {_esc(c.get('invalidation'))}]</em></li>"
        )
    return f"<ul class='candidate-list'>{''.join(items)}</ul>"


def _render_agent_block_html(role_label: str, r: dict | None) -> str:
    if not r:
        return f"<p><strong>{_esc(role_label)}:</strong> ไม่ active วันนั้น (ตัดออกตาม cost governor)</p>"
    if r.get("abstained"):
        return f"<p><strong>{_esc(role_label)} ({_esc(r.get('model'))}):</strong> abstain — {_esc(r.get('error'))}</p>"
    candidates = (r.get("output") or {}).get("candidates", [])
    return f"""
    <p><strong>{_esc(role_label)} ({_esc(r.get('model'))}):</strong>
       cost={_fmt_num(r.get('cost_usd'), 4)} USD, latency={_fmt_num(r.get('latency_ms'), 0)}ms</p>
    {_render_candidates_list_html(candidates)}
    """


def render_decision_log_html(decisions: list[dict]) -> str:
    """Log การตัดสินใจแบบเต็ม — ใช้ <details>/<summary> ล้วนๆ (ไม่มี JavaScript เลย) ให้พับ/กางดูได้
    แต่ละวันมี: candidate ที่ผ่าน screening พร้อมคะแนน, ความเห็นเต็มของ analyst ทั้ง 3 + redteam,
    และคำตัดสินสุดท้ายของ judge พร้อมเหตุผล — ตอบโจทย์ "ดู log ทุกมุม" ที่ผู้ใช้ขอไว้
    """
    if not decisions:
        return "<p>ยังไม่มี decision ที่บันทึกไว้</p>"

    blocks = []
    for d in decisions[-MAX_DECISION_LOG_ROWS:][::-1]:
        date = _esc(d.get("date"))
        source = d.get("source", "?")

        shortlist_rows = "".join(
            f"<li>{_esc(item.get('coin'))} — composite {_fmt_num(item.get('composite'))}</li>"
            for item in d.get("shortlist", []) or []
        )
        pinned = d.get("pinned_extra")
        pinned_html = (
            f"<li>{_esc(pinned.get('coin'))} — composite {_fmt_num(pinned.get('composite'))} (pinned/always_include)</li>"
            if pinned
            else ""
        )
        rest = d.get("rest_summary") or []
        rest_html = ", ".join(f"{_esc(item.get('coin'))} ({_fmt_num(item.get('composite'))})" for item in rest)

        if source == "llm":
            judge_output = d.get("judge_output") or {}
            summary_action = _esc(judge_output.get("action", "-"))
            summary_asset = _esc(judge_output.get("asset", "-"))

            role_results = {r["role"]: r for r in d.get("analyst_results", [])}
            agents_html = "".join(
                _render_agent_block_html(role, role_results.get(role))
                for role in ["analyst_trend", "analyst_positioning", "analyst_macro"]
            )
            redteam_html = _render_agent_block_html("redteam", d.get("redteam_result"))

            if d.get("judge_abstained") or not judge_output:
                judge_html = f"<p><strong>Judge:</strong> abstain — {_esc(d.get('judge_error'))}</p>"
            else:
                judge_html = f"""
                <p><strong>Judge:</strong> {_esc(judge_output.get('action'))} {_esc(judge_output.get('asset'))}
                   confidence={_esc(judge_output.get('confidence'))}
                   stop={_fmt_num(judge_output.get('stop_pct'))}% tp={_fmt_num(judge_output.get('take_profit_pct'))}%</p>
                <p class="hint">เหตุผล: {_esc(judge_output.get('reasoning'))}</p>
                <p class="hint">ทำไมเลือกตัวนี้แทนตัวอื่น: {_esc(judge_output.get('why_this_over_others'))}</p>
                <p class="hint">ตอบข้อค้านของ redteam: {_esc(judge_output.get('redteam_response'))}</p>
                """

            body = f"""
            <p><strong>Shortlist วันนั้น:</strong></p>
            <ul>{shortlist_rows}{pinned_html}</ul>
            {f"<p class='hint'>ตัวอื่นในพูล: {rest_html}</p>" if rest_html else ""}
            <hr>
            {agents_html}
            <hr>
            {redteam_html}
            <hr>
            {judge_html}
            """
        else:
            baseline = d.get("baseline_decision") or {}
            summary_action = _esc(baseline.get("action", "-"))
            summary_asset = _esc(baseline.get("asset", "-"))
            body = f"""
            <p><strong>Shortlist วันนั้น:</strong></p>
            <ul>{shortlist_rows}{pinned_html}</ul>
            <p><strong>Baseline decision</strong> (LLM ถูกปิดเพราะเกินงบเดือน):
               {_esc(baseline.get('action'))} {_esc(baseline.get('asset'))}
               confidence={_esc(baseline.get('confidence'))}</p>
            """

        blocks.append(
            f"""
            <details class="decision-entry">
              <summary>{date} — {_esc(source)} — {summary_action} {summary_asset}
                 (degrade level {_esc(d.get('degrade_level'))})</summary>
              <div class="decision-body">{body}</div>
            </details>
            """
        )

    return f"""
    <div class="card">
      <h2>Log การตัดสินใจแบบเต็ม (ล่าสุด {len(blocks)} วัน — คลิกเพื่อกาง)</h2>
      {''.join(blocks)}
    </div>
    """


def render_decision_outcomes_html(outcomes: list[dict] | None) -> str:
    if not outcomes:
        return "<p>ยังไม่มี decision outcome หลัง risk gate บันทึกไว้</p>"
    rows = []
    for item in outcomes[-MAX_ROWS:][::-1]:
        status = "ผ่าน" if item.get("passed") else "VETO"
        rows.append(
            "<tr>"
            f"<td>{_esc(item.get('date'))}</td><td>{_esc(item.get('proposed_action'))} {_esc(item.get('proposed_asset'))}</td>"
            f"<td>{_esc(item.get('final_action'))}</td><td>{_esc(item.get('stage'))}</td>"
            f"<td>{status}</td><td>{_esc(item.get('failed_gate') or '-')}</td><td>{_esc(item.get('reason'))}</td>"
            "</tr>"
        )
    return f"""
    <div class="card">
      <h2>ผลหลัง Risk Gate / Sizing</h2>
      <table><thead><tr><th>วันที่</th><th>เสนอ</th><th>ผลจริง</th><th>ขั้น</th><th>สถานะ</th><th>Gate</th><th>เหตุผล</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
    </div>
    """


def render_model_health_html(model_health: dict | None) -> str:
    if not model_health:
        return '<div class="card"><h2>Model Health</h2><p>ยังไม่มี model health report — รอบจริงจะ fallback baseline</p></div>'
    rows = []
    for item in model_health.get("results", []):
        status = "OK" if item.get("healthy") else "FAIL"
        rows.append(
            f"<tr><td>{_esc(item.get('role'))}</td><td>{_esc(item.get('model'))}</td>"
            f"<td>{status}</td><td>{_fmt_num(item.get('latency_ms'), 0)} ms</td><td>{_esc(item.get('error') or '-')}</td></tr>"
        )
    overall = "พร้อมใช้ครบ" if model_health.get("healthy") else "ไม่ครบ — รอบนี้ต้อง fallback baseline"
    return f"""
    <div class="card"><h2>Model Health</h2><p><strong>{overall}</strong></p>
    <table><thead><tr><th>Role</th><th>Model</th><th>สถานะ</th><th>Latency</th><th>Error</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
    """


def render_promotion_readiness_html(
    trades: list[dict], model_health: dict | None, grid_backtest_summary: dict | None
) -> str:
    closed_count = len(trades)
    sample_ready = closed_count >= 30
    model_ready = bool(model_health and model_health.get("healthy"))
    grid_wf = ((grid_backtest_summary or {}).get("grid_walk_forward") or {}).get("BTC") or {}
    grid_folds = len(grid_wf.get("folds", []))
    grid_oos_pnl = grid_wf.get("total_pnl_usd")
    grid_ready = grid_folds >= 3 and grid_oos_pnl is not None and grid_oos_pnl > 0
    overall = sample_ready and model_ready
    status = "พร้อมพิจารณาขั้นถัดไป" if overall else "ยังไม่พร้อมเพิ่มทุน/เปิดเงินจริง"
    return f"""
    <div class="card"><h2>Promotion Readiness</h2><p><strong>{status}</strong></p>
      <ul>
        <li>Directional paper sample: {closed_count}/30 ไม้ขั้นต่ำ (เป้าหมายที่มั่นใจกว่า 50 ไม้) — {'ผ่าน' if sample_ready else 'ยังไม่ผ่าน'}</li>
        <li>Model team health: {'ผ่าน' if model_ready else 'ยังไม่ผ่าน/ไม่มีผลล่าสุด'}</li>
        <li>Grid walk-forward: {grid_folds} folds, OOS PnL {_fmt_num(grid_oos_pnl)} USD — {'ผ่าน' if grid_ready else 'ไม่ผ่าน จึงคงไว้เฉพาะ shadow'}</li>
      </ul>
    </div>
    """


def render_lessons_html(lessons_text: str) -> str:
    if not lessons_text.strip():
        return "<p>ยังไม่มี lessons.md (reflector ยังไม่เคยรัน หรือยังไม่พบบทเรียนใหม่)</p>"
    return f"""
    <div class="card">
      <h2>บทเรียนล่าสุด (state/lessons.md)</h2>
      <pre class="lessons">{_esc(lessons_text)}</pre>
    </div>
    """


def render_dashboard_html(
    journal_state: dict | None,
    trades: list[dict],
    decisions: list[dict],
    generated_at_utc: str,
    equity_history: list[dict] | None = None,
    llm_cost_records: list[dict] | None = None,
    llm_budget_cfg: dict | None = None,
    lessons_text: str = "",
    decision_outcomes: list[dict] | None = None,
    model_health: dict | None = None,
    grid_backtest_summary: dict | None = None,
) -> str:
    """ประกอบทุก section เป็นหน้า HTML เดียวแบบ self-contained (inline CSS/SVG ไม่พึ่ง CDN — เปิดได้แน่นอน
    แบบไฟล์ในเครื่อง ไม่ต้องต่อเน็ต, ไม่ต้องเสียเงิน GitHub Pages) พารามิเตอร์ใหม่ทั้งหมด optional เพื่อให้
    ของเดิม (test เก่า) เรียกแบบ positional 4 ตัวเดิมได้เหมือนเดิมโดยไม่พัง
    """
    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>Money Chaser Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; }}
  h1 {{ font-size: 1.5rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 0; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #f1f5f9; }}
  .footer {{ color: #9ca3af; font-size: 0.8rem; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; font-size: 0.95rem; }}
  .stat-label {{ color: #6b7280; font-size: 0.8rem; }}
  .bar-track {{ background: #f1f5f9; border-radius: 4px; height: 8px; margin: 0.25rem 0 0.75rem; overflow: hidden; }}
  .bar-fill {{ height: 100%; }}
  .hint {{ color: #6b7280; font-size: 0.85rem; }}
  .candidate-list {{ margin: 0.25rem 0 0.75rem; padding-left: 1.25rem; font-size: 0.9rem; }}
  .decision-entry {{ border: 1px solid #f1f5f9; border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 0.5rem; }}
  .decision-entry summary {{ cursor: pointer; font-weight: 600; }}
  .decision-body {{ margin-top: 0.5rem; }}
  .decision-body hr {{ border: none; border-top: 1px dashed #e5e7eb; margin: 0.75rem 0; }}
  .lessons {{ white-space: pre-wrap; font-family: inherit; font-size: 0.85rem; background: #f8fafc; padding: 0.75rem; border-radius: 6px; }}
</style>
</head>
<body>
  <h1>Money Chaser — Dashboard (ส่วนตัว)</h1>
  {render_equity_summary_html(journal_state)}
  {render_equity_curve_html(equity_history)}
  {render_performance_stats_html(trades)}
  {render_llm_budget_html(llm_cost_records, llm_budget_cfg)}
  {render_model_health_html(model_health).strip()}
  {render_promotion_readiness_html(trades, model_health, grid_backtest_summary).strip()}
  {render_agent_scorecard_html(decisions)}
  {render_decision_outcomes_html(decision_outcomes).strip()}
  {render_decision_log_html(decisions)}
  {render_trades_table_html(trades)}
  {render_lessons_html(lessons_text)}
  <p class="footer">อัปเดตล่าสุด: {_esc(generated_at_utc)} UTC — สร้างโดย src/report/dashboard.py — เปิดไฟล์นี้ในเครื่องได้เลย ไม่ต้องต่อเน็ต</p>
</body>
</html>
"""


def main() -> None:  # pragma: no cover - เรียกจริงบน GitHub Actions เท่านั้น (ต้องมี state/ ที่รันแล้ว)
    import datetime

    from src.settings import REPO_ROOT, STATE_DIR, load_settings
    from src.util.io import load_json, load_jsonl

    journal_dir = STATE_DIR / "journal"
    journal_state = load_json(journal_dir / "state.json", default=None)
    trades = load_jsonl(journal_dir / "trades.jsonl")
    decisions = load_jsonl(journal_dir / "decisions.jsonl")
    equity_history = load_jsonl(journal_dir / "equity.jsonl")
    llm_cost_records = load_jsonl(journal_dir / "llm_cost.jsonl")
    decision_outcomes = load_jsonl(journal_dir / "decision_outcomes.jsonl")
    model_health = load_json(STATE_DIR / "model_health.json", default=None)
    grid_backtest_summary = load_json(STATE_DIR / "grid_farming_backtest" / "summary.json", default=None)

    lessons_text = ""
    lessons_path = REPO_ROOT / "state" / "lessons.md"
    if lessons_path.exists():
        lessons_text = lessons_path.read_text(encoding="utf-8")

    llm_budget_cfg = None
    try:
        llm_budget_cfg = load_settings().risk.llm_budget.model_dump()
    except Exception as exc:  # noqa: BLE001 - dashboard ต้องไม่ล้มแม้ settings โหลดไม่ได้ (เช่นตอน dev local)
        print(f"[dashboard] โหลด llm_budget config ไม่ได้ (แสดง budget section แบบไม่มีเพดาน): {exc}")

    html_content = render_dashboard_html(
        journal_state,
        trades,
        decisions,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        equity_history=equity_history,
        llm_cost_records=llm_cost_records,
        llm_budget_cfg=llm_budget_cfg,
        lessons_text=lessons_text,
        decision_outcomes=decision_outcomes,
        model_health=model_health,
        grid_backtest_summary=grid_backtest_summary,
    )

    output_dir = REPO_ROOT / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html_content, encoding="utf-8")
    print(f"[dashboard] เขียน {output_dir / 'index.html'} สำเร็จ")


if __name__ == "__main__":  # pragma: no cover
    main()

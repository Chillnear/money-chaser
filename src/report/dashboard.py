"""
Dashboard — สร้าง static HTML สรุปสถานะระบบจาก journal (BUILD-SPEC.md §1: "dashboard.py: static HTML
→ GitHub Pages") ไม่มี JS เรียก API ใดๆ ทั้งหมด render ตอน build time จาก state/journal/*.jsonl ที่มีอยู่แล้ว

หน้าเว็บนี้เป็นแค่ "กระจกสะท้อน" state ที่มีอยู่แล้ว ไม่มี logic ตัดสินใจใดๆ ในไฟล์นี้ — ข้อมูลดิบทั้งหมด
มาจาก journal ที่ pipeline หลัก (src/main.py) เขียนไว้แล้ว
"""
from __future__ import annotations

import html

MAX_ROWS = 30


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


def render_dashboard_html(
    journal_state: dict | None,
    trades: list[dict],
    decisions: list[dict],
    generated_at_utc: str,
) -> str:
    """ประกอบทุก section เป็นหน้า HTML เดียวแบบ self-contained (inline CSS ไม่พึ่ง CDN — ให้เปิดได้แน่นอน
    บน GitHub Pages โดยไม่มี dependency ภายนอก)
    """
    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>Money Chaser Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1f2937; }}
  h1 {{ font-size: 1.5rem; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #f1f5f9; }}
  .footer {{ color: #9ca3af; font-size: 0.8rem; }}
</style>
</head>
<body>
  <h1>Money Chaser — Dashboard</h1>
  {render_equity_summary_html(journal_state)}
  {render_trades_table_html(trades)}
  {render_decisions_table_html(decisions)}
  <p class="footer">อัปเดตล่าสุด: {_esc(generated_at_utc)} UTC — สร้างโดย src/report/dashboard.py</p>
</body>
</html>
"""


def main() -> None:  # pragma: no cover - เรียกจริงบน GitHub Actions เท่านั้น (ต้องมี state/ ที่รันแล้ว)
    import datetime

    from src.settings import REPO_ROOT, STATE_DIR
    from src.util.io import load_json, load_jsonl

    journal_dir = STATE_DIR / "journal"
    journal_state = load_json(journal_dir / "state.json", default=None)
    trades = load_jsonl(journal_dir / "trades.jsonl")
    decisions = load_jsonl(journal_dir / "decisions.jsonl")

    html_content = render_dashboard_html(
        journal_state, trades, decisions, datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    output_dir = REPO_ROOT / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html_content, encoding="utf-8")
    print(f"[dashboard] เขียน {output_dir / 'index.html'} สำเร็จ")


if __name__ == "__main__":  # pragma: no cover
    main()

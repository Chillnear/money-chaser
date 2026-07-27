"""
LINE Messaging API (push message) — แจ้งเตือนผลการเทรดรายวัน + budget/breaker/reconcile alert
ตาม BUILD-SPEC.md §1 (src/report/notify.py) และ §4.2 ("แจ้งเตือนผ่าน LINE เมื่อใช้งบถึง 70%/90% ของเดือน")

ใช้ LINE Messaging API (push message) แทน LINE Notify ที่ยุติให้บริการไปแล้วตั้งแต่มีนาคม 2025
Push message API: POST https://api.line.me/v2/bot/message/push
  headers: Authorization: Bearer <channel_access_token>, Content-Type: application/json
  body: {"to": <user_id>, "messages": [{"type": "text", "text": "..."}]}
  ข้อจำกัดของ LINE: ส่งได้สูงสุด 5 ข้อความ/ครั้ง, ข้อความยาวได้สูงสุด 5000 ตัวอักษร

หลักการสำคัญ: การแจ้งเตือนต้อง**ไม่ใช่ core trading logic** — ถ้าส่งไม่สำเร็จ (ไม่ตั้ง token, network ล่ม,
LINE ตอบ error) ต้องไม่ทำให้ pipeline การเทรดล้มตามไปด้วย ทุกฟังก์ชันในนี้จึงคืน NotifyResult เสมอ ไม่ raise
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_MESSAGES_PER_PUSH = 5
MAX_TEXT_LENGTH = 5000


@dataclass
class NotifyResult:
    sent: bool
    reason: str


class LineNotifier:
    def __init__(
        self,
        channel_access_token: str,
        user_id: str,
        session: requests.Session | None = None,
        timeout_sec: float = 10.0,
    ):
        self.channel_access_token = channel_access_token
        self.user_id = user_id
        self.session = session or requests.Session()
        self.timeout_sec = timeout_sec

    def is_configured(self) -> bool:
        return bool(self.channel_access_token and self.user_id)

    def send_text(self, text: str) -> NotifyResult:
        return self.send_texts([text])

    def send_texts(self, texts: list[str]) -> NotifyResult:
        """ส่งได้หลายข้อความในการเรียกครั้งเดียว (LINE รวมเป็น 1 push) — ตัดข้อความเกิน 5 ข้อความ/เกิน
        5000 ตัวอักษรทิ้งอัตโนมัติ กันเคส caller ส่งเกิน limit ของ LINE โดยไม่รู้ตัวแล้ว API ปฏิเสธทั้งชุด
        """
        if not self.is_configured():
            return NotifyResult(
                sent=False, reason="ยังไม่ตั้งค่า LINE_CHANNEL_ACCESS_TOKEN/LINE_USER_ID — ข้ามการแจ้งเตือน"
            )
        if not texts:
            return NotifyResult(sent=False, reason="ไม่มีข้อความให้ส่ง")

        truncated = [t[:MAX_TEXT_LENGTH] for t in texts[:MAX_MESSAGES_PER_PUSH]]
        messages = [{"type": "text", "text": t} for t in truncated]

        try:
            resp = self.session.post(
                LINE_PUSH_URL,
                json={"to": self.user_id, "messages": messages},
                headers={
                    "Authorization": f"Bearer {self.channel_access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_sec,
            )
        except requests.RequestException as exc:
            return NotifyResult(sent=False, reason=f"ส่ง LINE ไม่สำเร็จ (network/timeout): {exc}")

        if resp.status_code != 200:
            return NotifyResult(sent=False, reason=f"LINE API ตอบ {resp.status_code}: {resp.text[:300]}")

        return NotifyResult(sent=True, reason="ส่งสำเร็จ")


def format_daily_summary(run_result) -> str:
    """สร้างข้อความสรุปผลรอบเทรดรายวัน — รับ DailyRunResult จาก src/main.py แบบ duck-typed
    (ต้องมี attribute: date, action_taken, reason, equity_usd) กัน circular import กับ main.py
    """
    return (
        f"📊 Money Chaser — {run_result.date}\n"
        f"ผลลัพธ์: {run_result.action_taken}\n"
        f"เหตุผล: {run_result.reason}\n"
        f"Equity: {run_result.equity_usd:.2f} USD"
    )


def format_budget_alert(pct_used: float, monthly_spend_usd: float, monthly_hard_stop_usd: float) -> str:
    return (
        f"⚠️ Money Chaser — แจ้งเตือนงบ LLM\n"
        f"ใช้ไปแล้ว {pct_used:.0f}% ของงบเดือนนี้\n"
        f"ยอดใช้จริง: {monthly_spend_usd:.2f} / {monthly_hard_stop_usd:.2f} USD"
    )


def format_breaker_alert(reason: str) -> str:
    return f"🛑 Money Chaser — Circuit breaker ทำงาน\n{reason}"


def format_reconcile_mismatch_alert(reason: str) -> str:
    return f"❗ Money Chaser — Reconcile ไม่ตรง (หยุดเทรดวันนี้)\n{reason}"


def format_exception_alert(error_text: str) -> str:
    return f"💥 Money Chaser — Pipeline เจอ exception (fail-closed ไม่เทรด)\n{error_text[:1000]}"


def notify_budget_thresholds(
    notifier: LineNotifier,
    monthly_spend_usd: float,
    monthly_hard_stop_usd: float,
    thresholds_pct: list[int],
    already_notified_pct: list[int],
) -> list[int]:
    """เช็คว่าใช้งบข้าม threshold ใหม่หรือยัง แล้วส่งแจ้งเตือนเฉพาะ threshold ที่ยังไม่เคยแจ้ง
    (ตาม BUILD-SPEC.md §4.2: แจ้งที่ 70%/90% ของเดือน) — คืน list ของ threshold ที่แจ้งไปแล้วในรอบนี้
    caller (main.py) ต้องเก็บ list สะสมนี้ไว้ใน journal state เองเพื่อกันแจ้งซ้ำวันถัดไป
    """
    if monthly_hard_stop_usd <= 0:
        return []

    pct_used = monthly_spend_usd / monthly_hard_stop_usd * 100
    newly_notified: list[int] = []
    for threshold in sorted(thresholds_pct):
        if pct_used >= threshold and threshold not in already_notified_pct:
            notifier.send_text(format_budget_alert(pct_used, monthly_spend_usd, monthly_hard_stop_usd))
            newly_notified.append(threshold)
    return newly_notified

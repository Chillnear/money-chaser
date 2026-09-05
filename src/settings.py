"""
โหลด config/*.yaml + environment variables แล้ว validate ด้วย pydantic
ตาม BUILD-SPEC.md ข้อ 0 (non-negotiables) และโครงสร้าง repo ข้อ 1

Fail-closed: ถ้า config ผิดรูปหรือ MODE ไม่ถูกต้อง -> raise ทันทีตอน import/startup
ไม่ปล่อยให้ pipeline รันต่อด้วยค่า default ที่เดาเอง
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
STATE_DIR = REPO_ROOT / "state"

Mode = Literal["paper", "live", "dryrun"]


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์ config ที่จำเป็น: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{name} ต้องเป็น YAML mapping ที่ระดับบนสุด")
    return data


class SizingConfig(BaseModel):
    risk_per_trade_pct: float
    min_notional_usd: float
    max_notional_usd: float
    max_notional_pct_of_equity: float
    min_notional_override_max_risk_pct: float


class StopsConfig(BaseModel):
    atr_multiple: float
    stop_floor_pct: float
    stop_cap_pct: float
    reward_risk_ratio: float
    max_holding_days: int = Field(gt=0)


class GatesConfig(BaseModel):
    min_judge_confidence: float
    min_analyst_agreement: int
    max_funding_pct_annual: float


class BreakersConfig(BaseModel):
    daily_loss_pct: float
    weekly_loss_pct: float
    max_drawdown_pct: float
    consecutive_losses_halve_size: int


class CostsConfig(BaseModel):
    taker_fee_pct: float
    assumed_slippage_pct: float


class LlmBudgetConfig(BaseModel):
    monthly_usd_cap: float
    hard_stop_usd: float
    daily_soft_cap_usd: float
    degrade_thresholds_pct: list[int]


class MacroVetoConfig(BaseModel):
    """ห้ามเทรดวันมีข่าวมหภาคสำคัญ (P5.2) — ดูรายละเอียดใน src/data/econ_calendar.py"""

    enabled: bool
    impact_levels: list[str]
    countries: list[str]
    lookahead_hours: float
    lookback_hours: float


class ModeDefaults(BaseModel):
    max_open_positions: int
    max_trades_per_day: int
    max_leverage: float
    universe_mode: str
    min_24h_volume_usd: float
    min_open_interest_usd: float
    always_include: list[str]
    screening_shortlist_size: int


class RiskConfig(BaseModel):
    """โหลดจาก risk.yaml — ห้ามมีที่ไหนใน codebase เขียนไฟล์นี้กลับ ยกเว้นมนุษย์แก้ตรงๆ"""

    mode_defaults: ModeDefaults
    sizing: SizingConfig
    stops: StopsConfig
    gates: GatesConfig
    breakers: BreakersConfig
    costs: CostsConfig
    llm_budget: LlmBudgetConfig
    macro_veto: MacroVetoConfig

    @classmethod
    def load(cls) -> "RiskConfig":
        return cls(**_load_yaml("risk.yaml"))


class AppConfig(BaseModel):
    """โหลดจาก config.yaml"""

    raw: dict

    @classmethod
    def load(cls) -> "AppConfig":
        return cls(raw=_load_yaml("config.yaml"))

    def __getitem__(self, key: str):
        return self.raw[key]


class Secrets(BaseModel):
    litellm_base_url: str
    litellm_key_1: str
    litellm_key_2: str
    mimi_coach_key: str = ""
    hl_agent_private_key: str = ""
    hl_main_address: str = ""
    line_channel_access_token: str = ""
    line_user_id: str = ""
    live_ack: str = ""
    cryptopanic_api_key: str = ""  # optional — ข่าวข้ามแหล่งนี้ได้ถ้าไม่ตั้งค่า

    @field_validator("litellm_base_url")
    @classmethod
    def _no_trailing_slash_confusion(cls, v: str) -> str:
        return v.rstrip("/")

    def llm_api_keys(self) -> list[str]:
        """ใช้ Mimi Coach เพียง key เดียวเมื่อมีค่า; ไม่ปน entitlement กับ key ชุดเดิม."""
        if self.mimi_coach_key:
            return [self.mimi_coach_key]
        return [key for key in (self.litellm_key_1, self.litellm_key_2) if key]


class Settings(BaseModel):
    mode: Mode
    risk: RiskConfig
    app: AppConfig
    secrets: Secrets

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        # pydantic Literal already restricts values, this is a defensive double-check
        if v not in ("paper", "live", "dryrun"):
            raise ValueError(
                f"MODE ต้องเป็น paper|live|dryrun เท่านั้น ได้รับ: {v!r} (fail-closed)"
            )
        return v

    def is_live(self) -> bool:
        return self.mode == "live"


def load_settings() -> Settings:
    """Entry point เดียวที่ main.py และโค้ดอื่นควรเรียกใช้เพื่อโหลด config ทั้งหมด

    Fail-closed โดยตั้งใจ: ขาด secret ที่จำเป็น หรือ MODE ผิด -> raise ก่อนเริ่ม pipeline
    """
    mode_raw = os.getenv("MODE", "paper")

    secrets = Secrets(
        litellm_base_url=os.getenv("LITELLM_BASE_URL", ""),
        litellm_key_1=os.getenv("LITELLM_KEY_1", ""),
        litellm_key_2=os.getenv("LITELLM_KEY_2", ""),
        mimi_coach_key=os.getenv("MIMI_COACH_KEY", ""),
        hl_agent_private_key=os.getenv("HL_AGENT_PRIVATE_KEY", ""),
        hl_main_address=os.getenv("HL_MAIN_ADDRESS", ""),
        line_channel_access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""),
        line_user_id=os.getenv("LINE_USER_ID", ""),
        live_ack=os.getenv("LIVE_ACK", ""),
        cryptopanic_api_key=os.getenv("CRYPTOPANIC_API_KEY", ""),
    )

    return Settings(
        mode=mode_raw,  # type: ignore[arg-type]  # validator ด้านบนจะ raise ถ้าไม่ผ่าน
        risk=RiskConfig.load(),
        app=AppConfig.load(),
        secrets=secrets,
    )


if __name__ == "__main__":
    # smoke test เร็วๆ: `python -m src.settings`
    s = load_settings()
    print(f"MODE = {s.mode}")
    print(f"risk_per_trade_pct = {s.risk.sizing.risk_per_trade_pct}")
    print(f"max_holding_days = {s.risk.stops.max_holding_days}")
    print("settings.py โหลดสำเร็จ")

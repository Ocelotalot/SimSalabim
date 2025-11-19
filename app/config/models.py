"""Typed configuration models for the trading bot.

The config subsystem relies on pydantic to validate YAML files and to
provide strongly-typed objects to the rest of the runtime. See TZ.txt
and ARCHITECTURE.md §5.1 for the list of models covered here.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class BybitMode(str, Enum):
    """Supported Bybit account modes."""

    LIVE = "live"
    DEMO = "demo"


class SymbolGroup(str, Enum):
    """Universe grouping from TZ §3.2 used in risk and liquidity filters."""

    CORE = "core"
    PLUS = "plus"
    ROTATION = "rotation"


class ApiKeyPair(BaseModel):
    """API key/secret pair for Bybit REST/WebSocket clients."""

    api_key: str = Field(..., min_length=5)
    api_secret: str = Field(..., min_length=10)


class BybitCredentials(BaseModel):
    """Bybit demo/live credential sets (TZ §2.2)."""

    demo: ApiKeyPair
    live: ApiKeyPair


class TelegramCredentials(BaseModel):
    """Telegram bot token and chat id used by the Telegram interface."""

    bot_token: str = Field(..., min_length=10)
    chat_id: int


class ApiCredentialsConfig(BaseModel):
    """Secrets used by Bybit + Telegram integrations (ARCHITECTURE §5.1).

    Mirrors credentials.example.yml / secrets.yaml.
    """

    bybit: BybitCredentials
    telegram: TelegramCredentials

    model_config = ConfigDict(frozen=True)


class SymbolConfig(BaseModel):
    """Trading universe entry (TZ §3.2, ARCHITECTURE §5.1).

    Fields marked as optional inherit defaults from TZ: core/plus symbols
    enabled by default; rotation symbols disabled unless explicitly enabled.
    """

    symbol: str = Field(..., min_length=3)
    group: SymbolGroup
    enabled: Optional[bool] = None
    max_leverage: int = Field(5, ge=1)
    max_notional_usdt: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _apply_enabled_default(self) -> "SymbolConfig":
        """Set defaults per TZ §3.2 (rotation disabled by default)."""

        if self.enabled is None:
            self.enabled = self.group != SymbolGroup.ROTATION
        return self


class StrategyRuntimeConfig(BaseModel):
    """Per-strategy runtime config (TZ §4.7, ARCHITECTURE §5.1).

    Required fields: `id`, `enabled`, `priority`. Arbitrary parameters for the
    strategy live inside `parameters` so YAML remains extensible.
    """

    id: str
    enabled: bool = True
    priority: int = Field(..., ge=1)
    name: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RiskConfig(BaseModel):
    """Risk settings per TZ §4.9.

    Mandatory fields: virtual equity, per-trade risk pct, max daily loss pct
    and max concurrent positions. Cooldown/slippage can be extended later.
    """

    virtual_equity_usdt: float = Field(..., gt=0)
    per_trade_risk_pct: float = Field(..., gt=0, lt=0.1)
    max_daily_loss_pct: float = Field(..., gt=0, lt=0.5)
    max_concurrent_positions: PositiveInt
    cooldown_after_loss_min: PositiveInt = Field(20, description="Minutes to block new entries after loss")
    max_leverage: PositiveInt = Field(5, description="Default isolated leverage cap")
    max_slippage_bps: Optional[float] = Field(None, gt=0)


class RotationConfig(BaseModel):
    """Rotation scoring thresholds (TZ §4.8).

    Fields default to the TZ baseline but can be overridden via YAML.
    """

    enabled: bool = True
    check_interval_min: PositiveInt = 5
    min_score_for_new_entry: float = Field(0.55, ge=0, le=1)
    max_active_symbols: PositiveInt = Field(5, description="top_n in TZ wording")


class LiquidityThresholdConfig(BaseModel):
    """Per-group liquidity filter thresholds from TZ §3.3."""

    min_depth_usd: float = Field(..., gt=0)
    max_spread_bps: float = Field(..., gt=0)


class FiltersConfig(BaseModel):
    """Pre-trade filters (TZ §3.3, §4.2 slippage/latency)."""

    liquidity_core: LiquidityThresholdConfig
    liquidity_plus: LiquidityThresholdConfig
    liquidity_rotation: LiquidityThresholdConfig
    max_latency_ms: float = Field(250, gt=0)
    max_avg_slippage_bps: float = Field(5.0, gt=0)


class TfProfileSettings(BaseModel):
    """Definition of TF profile (TZ §4.2) used by strategies/filters."""

    trigger_tf: str
    confirm_tf: str
    slow_tf: str


class TfProfilesConfig(BaseModel):
    """Collection of TF profiles for AGGR/BAL/CONS (TZ §4.2)."""

    aggr: TfProfileSettings
    bal: TfProfileSettings
    cons: TfProfileSettings


class TelemetryConfig(BaseModel):
    """Telemetry/reporting switches referenced in TZ §2.1, §2.5."""

    stats_interval_min: PositiveInt = 60
    log_level: str = Field("INFO")
    reports_dir: str = Field("data/logs")


class BybitTradingConfig(BaseModel):
    """Bybit trading settings. Currently only mode (TZ §2.2)."""

    mode: BybitMode
    rest_endpoint: Optional[str] = None
    ws_endpoint: Optional[str] = None


class TradingConfig(BaseModel):
    """Top-level trading config (ARCHITECTURE §5.1, TZ §§2–4).

    Combines Bybit mode, scheduler parameters and sub-configs for risk,
    rotation, filters, TF profiles and telemetry.
    """

    bybit: BybitTradingConfig
    update_interval_sec: PositiveInt = 15
    timezone: str = Field("Europe/Minsk")
    risk: RiskConfig
    rotation: RotationConfig
    filters: FiltersConfig
    tf_profiles: TfProfilesConfig
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)


class AppConfig(BaseModel):
    """Runtime config composed of trading, symbols, strategies and secrets."""

    trading: TradingConfig
    symbols: List[SymbolConfig]
    strategies: Dict[str, StrategyRuntimeConfig]
    credentials: ApiCredentialsConfig

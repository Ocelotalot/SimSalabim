from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict

import pytest

from app.config.models import (
    BybitMode,
    BybitTradingConfig,
    FiltersConfig,
    LiquidityThresholdConfig,
    RiskConfig,
    RotationConfig,
    StrategyRuntimeConfig,
    SymbolConfig,
    SymbolGroup,
    TelemetryConfig,
    TfProfileSettings,
    TfProfilesConfig,
    TradingConfig,
)
from app.core.enums import Regime, StrategyId, TfProfile
from app.core.types import Symbol
from app.execution.models import ActiveOrder, OrderIntent, OrderStatus
from app.market.models import MarketState


@pytest.fixture(scope="session")
def default_trading_config() -> TradingConfig:
    return TradingConfig(
        bybit=BybitTradingConfig(mode=BybitMode.DEMO),
        update_interval_sec=15,
        risk=RiskConfig(
            virtual_equity_usdt=25_000,
            per_trade_risk_pct=0.01,
            max_daily_loss_pct=0.1,
            max_concurrent_positions=3,
            cooldown_after_loss_min=15,
            max_leverage=5,
        ),
        rotation=RotationConfig(
            enabled=True,
            check_interval_min=5,
            min_score_for_new_entry=0.55,
            max_active_symbols=5,
        ),
        filters=FiltersConfig(
            liquidity_core=LiquidityThresholdConfig(min_depth_usd=3_000_000, max_spread_bps=3.0),
            liquidity_plus=LiquidityThresholdConfig(min_depth_usd=1_500_000, max_spread_bps=5.0),
            liquidity_rotation=LiquidityThresholdConfig(min_depth_usd=750_000, max_spread_bps=8.0),
            max_latency_ms=250,
            max_avg_slippage_bps=6.0,
        ),
        tf_profiles=TfProfilesConfig(
            aggr=TfProfileSettings(trigger_tf="1m", confirm_tf="3m", slow_tf="5m"),
            bal=TfProfileSettings(trigger_tf="3m", confirm_tf="5m", slow_tf="15m"),
            cons=TfProfileSettings(trigger_tf="5m", confirm_tf="15m", slow_tf="60m"),
        ),
        telemetry=TelemetryConfig(stats_interval_min=30, log_level="INFO", reports_dir="data/logs"),
    )


@pytest.fixture(scope="session")
def symbol_configs() -> list[SymbolConfig]:
    return [
        SymbolConfig(symbol="BTCUSDT", group=SymbolGroup.CORE, enabled=True, max_leverage=5, max_notional_usdt=150_000),
        SymbolConfig(symbol="ETHUSDT", group=SymbolGroup.CORE, enabled=True, max_leverage=5, max_notional_usdt=90_000),
        SymbolConfig(symbol="SOLUSDT", group=SymbolGroup.PLUS, enabled=True, max_leverage=3, max_notional_usdt=40_000),
        SymbolConfig(symbol="DOGEUSDT", group=SymbolGroup.PLUS, enabled=True, max_leverage=2, max_notional_usdt=15_000),
        SymbolConfig(symbol="ARBUSDT", group=SymbolGroup.ROTATION, enabled=False, max_leverage=2, max_notional_usdt=10_000),
    ]


@pytest.fixture
def telemetry_tmpdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("telemetry")


@pytest.fixture
def market_state_factory() -> Callable[..., MarketState]:
    base_timestamp = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    def _factory(**overrides: object) -> MarketState:
        symbol_value = overrides.pop("symbol", Symbol("BTCUSDT"))
        symbol = Symbol(str(symbol_value))
        payload: Dict[str, object] = {
            "symbol": symbol,
            "group": overrides.pop("group", SymbolGroup.CORE),
            "timestamp": overrides.pop("timestamp", base_timestamp),
            "mid_price": overrides.pop("mid_price", 100.0),
            "spread_bps": overrides.pop("spread_bps", 5.0),
            "depth_pm1_usd": overrides.pop("depth_pm1_usd", 4_000_000.0),
            "volume_5m": overrides.pop("volume_5m", 1_000_000.0),
            "rel_volume_5m": overrides.pop("rel_volume_5m", 1.2),
            "delta_flow_1m": overrides.pop("delta_flow_1m", 50_000.0),
            "ATR_14_5m": overrides.pop("ATR_14_5m", 5.0),
            "atr_q_5m": overrides.pop("atr_q_5m", 0.6),
            "ADX_15m": overrides.pop("ADX_15m", 25.0),
            "VWAP_window": overrides.pop("VWAP_window", tuple([100 + i for i in range(5)])),
            "vwap_slope": overrides.pop("vwap_slope", 0.0005),
            "vwap_slope_raw": overrides.pop("vwap_slope_raw", 0.05),
            "vwap_mean": overrides.pop("vwap_mean", 100.5),
            "oi_delta_5m": overrides.pop("oi_delta_5m", 10_000.0),
            "price_ref_for_vwap": overrides.pop("price_ref_for_vwap", 100.4),
            "distance_to_vwap": overrides.pop("distance_to_vwap", 0.5),
            "sigma_vwap": overrides.pop("sigma_vwap", 0.8),
            "avg_slippage_bps": overrides.pop("avg_slippage_bps", 2.0),
            "latency_ms": overrides.pop("latency_ms", 80.0),
            "regime": overrides.pop("regime", Regime.TREND),
            "tf_profile": overrides.pop("tf_profile", TfProfile.BAL),
        }
        payload.update(overrides)
        return MarketState(**payload)  # type: ignore[arg-type]

    return _factory


class StrategyStateProxy:
    def __init__(self, base: MarketState, **extra: object) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_extra", extra)

    def __getattr__(self, item: str):
        extra = object.__getattribute__(self, "_extra")
        if item in extra:
            return extra[item]
        return getattr(object.__getattribute__(self, "_base"), item)


@pytest.fixture
def strategy_state_builder():
    def _wrap(state: MarketState, **extra: object) -> StrategyStateProxy:
        return StrategyStateProxy(state, **extra)

    return _wrap


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str | int, text: str) -> None:
        self.messages.append((str(chat_id), text))


@pytest.fixture
def fake_telegram_client() -> FakeTelegramClient:
    return FakeTelegramClient()


class FakeOrderGateway:
    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []
        self.cancelled: list[str] = []
        self.next_status: OrderStatus = OrderStatus.NEW
        self._counter = 0

    def submit_order(self, order: OrderIntent) -> ActiveOrder:
        self.submitted.append(order)
        self._counter += 1
        order_id = f"order-{self._counter}"
        price = float(order.price) if order.price is not None else float(order.quantity)
        qty = float(order.quantity)
        return ActiveOrder(
            order_id=order_id,
            intent_id=order.client_order_id or f"intent-{self._counter}",
            order=order,
            status=self.next_status,
            filled_qty=qty if self.next_status is OrderStatus.FILLED else 0.0,
            avg_fill_price=price,
        )

    def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)


@pytest.fixture
def fake_gateway() -> FakeOrderGateway:
    return FakeOrderGateway()


@pytest.fixture
def strategy_runtime_config() -> StrategyRuntimeConfig:
    return StrategyRuntimeConfig(id=StrategyId.STRATEGY_A.value, enabled=True, priority=1)

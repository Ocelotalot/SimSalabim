from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Mapping

from app.config.loader import load_app_config
from app.config.models import AppConfig, RiskConfig, SymbolConfig
from app.core.enums import Regime, Side, StrategyId, TfProfile
from app.core.types import Symbol, Timestamp
from app.data_feed.bybit_client import BybitClient
from app.data_feed.candles import Timeframe
from app.data_feed.orderbook import OrderBookSnapshot as FeedOrderBookSnapshot
from app.data_feed.trades import Trade as FeedTrade
from app.execution.execution_engine import ExecutionEngine, OrderGateway
from app.execution.models import ActiveOrder, ExecutionEventType, ExecutionReport, OrderIntent, OrderStatus
from app.interfaces import StatusProvider, StatusSnapshot, TelegramBotInterface
from app.market.filters import PreTradeFilters, TradeStyle
from app.market.market_state_builder import MarketStateBuilder
from app.market.models import MarketState, OrderBookLevel, OrderBookSnapshot, TradeTick
from app.market.regime_classifier import RegimeClassifier
from app.market.tf_selector import TfProfileSelector
from app.risk.models import PositionState, RiskLimits
from app.risk.risk_engine import RiskEngine
from app.rotation.models import RotationState
from app.rotation.rotation_engine import RotationEngine
from app.runtime.state import RuntimeState, RuntimeStateStore
from app.strategies.base import BaseStrategy
from app.strategies.registry import build_active_strategies
from app.telemetry import configure_logging
from app.telemetry.events import TelemetryEvent
from app.telemetry.storage import TelemetryStorage, default_storage

FETCH_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.MIN_1,
    Timeframe.MIN_3,
    Timeframe.MIN_5,
    Timeframe.MIN_15,
)
STRATEGY_STYLE_MAP: Dict[StrategyId, TradeStyle] = {
    StrategyId.STRATEGY_A: TradeStyle.TREND,
    StrategyId.STRATEGY_B: TradeStyle.BREAKOUT,
    StrategyId.STRATEGY_C: TradeStyle.BREAKOUT,
    StrategyId.STRATEGY_D: TradeStyle.MEAN_REVERSION,
    StrategyId.STRATEGY_E: TradeStyle.BREAKOUT,
}


class PaperOrderGateway(OrderGateway):
    """Lightweight order gateway that fills orders immediately at mid-price."""

    def __init__(self, price_provider: Callable[[Symbol], float], logger: logging.Logger) -> None:
        self._price_provider = price_provider
        self._logger = logger

    def submit_order(self, order: OrderIntent) -> ActiveOrder:
        price = float(order.price) if order.price is not None else self._price_provider(order.symbol)
        now = datetime.now(tz=timezone.utc)
        active = ActiveOrder(
            order_id=str(uuid.uuid4()),
            intent_id=order.client_order_id or str(uuid.uuid4()),
            order=order,
            status=OrderStatus.FILLED,
            filled_qty=float(order.quantity),
            avg_fill_price=price,
            created_at=now,
            updated_at=now,
        )
        return active

    def cancel_order(self, order_id: str) -> None:
        self._logger.info("Paper gateway cancel noop", extra={"order_id": order_id})


@dataclass(slots=True)
class MarketDataService:
    """Fetch MarketState snapshots for all enabled symbols."""

    client: BybitClient
    symbols: Iterable[SymbolConfig]
    builder: MarketStateBuilder
    logger: logging.Logger
    _symbol_configs: list[SymbolConfig] = field(init=False)
    _last_states: Dict[Symbol, MarketState] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._symbol_configs = [cfg for cfg in self.symbols if cfg.enabled is not False]
        self._last_states: Dict[Symbol, MarketState] = {}

    def refresh(self) -> dict[Symbol, MarketState]:
        states: dict[Symbol, MarketState] = {}
        for cfg in self._symbol_configs:
            symbol = Symbol(cfg.symbol)
            try:
                state = self._build_state(cfg)
                self._last_states[symbol] = state
            except Exception as exc:  # pragma: no cover - network path
                self.logger.warning("Failed to refresh %s: %s", cfg.symbol, exc)
                state = self._last_states.get(symbol) or self._placeholder_state(cfg)
            states[symbol] = state
        return states

    def last_price(self, symbol: Symbol) -> float:
        state = self._last_states.get(symbol)
        return float(state.mid_price) if state else 0.0

    def close(self) -> None:
        self.client.close()

    def _build_state(self, cfg: SymbolConfig) -> MarketState:
        candles_by_tf: dict[Timeframe, list] = {}
        latencies: list[float] = []
        for tf in FETCH_TIMEFRAMES:
            candles = self.client.fetch_candles(cfg.symbol, tf)
            candles_by_tf[tf] = list(candles.data)
            latencies.append(candles.latency_ms)
        orderbook = self.client.fetch_orderbook(cfg.symbol)
        trades = self.client.fetch_trades(cfg.symbol)
        oi_stats = self.client.fetch_open_interest(cfg.symbol)
        latencies.extend([orderbook.latency_ms, trades.latency_ms, oi_stats.latency_ms])
        snapshot = _convert_orderbook(orderbook.data)
        trade_ticks = _convert_trades(trades.data)
        timestamp = Timestamp(time.time())
        mid_override = snapshot.best_bid if snapshot.best_bid else snapshot.best_ask
        state = self.builder.build_state(
            symbol=Symbol(cfg.symbol),
            group=cfg.group,
            timestamp=timestamp,
            candles_by_tf=candles_by_tf,
            orderbook=snapshot,
            trades=trade_ticks,
            open_interest_value=oi_stats.data.open_interest,
            latency_ms=max(latencies) if latencies else 0.0,
            latest_slippage_bps=None,
            price_ref_override=mid_override,
        )
        return state

    def _placeholder_state(self, cfg: SymbolConfig) -> MarketState:
        now = datetime.now(tz=timezone.utc)
        return MarketState(
            symbol=Symbol(cfg.symbol),
            group=cfg.group,
            timestamp=now,
            mid_price=0.0,
            spread_bps=0.0,
            depth_pm1_usd=0.0,
            volume_5m=0.0,
            rel_volume_5m=0.0,
            delta_flow_1m=0.0,
            ATR_14_5m=0.0,
            atr_q_5m=0.0,
            ADX_15m=0.0,
            VWAP_window=tuple(),
            vwap_slope=0.0,
            vwap_slope_raw=0.0,
            vwap_mean=0.0,
            oi_delta_5m=0.0,
            price_ref_for_vwap=0.0,
            distance_to_vwap=0.0,
            sigma_vwap=0.0,
            avg_slippage_bps=0.0,
            latency_ms=0.0,
            regime=Regime.RANGE,
            tf_profile=TfProfile.BAL,
        )


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config_dir = project_root / "config"
    runtime_dir = project_root / "runtime"
    secrets_path = _resolve_secrets_path(config_dir)
    config = load_app_config(secrets_path=secrets_path)

    telemetry_root = (project_root / config.trading.telemetry.reports_dir).resolve()
    _telemetry_storage = default_storage(
        telemetry_root, runtime_session_stats_path=runtime_dir / "session_stats.json"
    )
    log_dir = telemetry_root / "logs"
    logger = configure_logging(log_dir=log_dir, level=config.trading.telemetry.log_level)
    logger.info("Bootstrapping bot", extra={"mode": config.trading.bybit.mode.value})

    runtime_store = RuntimeStateStore(runtime_dir)
    runtime_lock = threading.Lock()
    runtime_state = _load_runtime_state(runtime_store, config.trading.risk)
    risk_limits = _build_risk_limits(config, runtime_state)
    strategies = build_active_strategies(config.strategies)
    regime_classifier = RegimeClassifier()
    tf_selector = TfProfileSelector()
    market_builder = MarketStateBuilder(regime_classifier, tf_selector, timezone_name=config.trading.timezone)
    filters = PreTradeFilters(config.trading.filters)
    rotation_engine = RotationEngine(config.trading.rotation, config.symbols)
    bybit_client = BybitClient(config.trading.bybit, config.credentials)
    market_service = MarketDataService(bybit_client, config.symbols, market_builder, logger.getChild("market"))
    risk_engine = RiskEngine(risk_limits, config.strategies, timezone=config.trading.timezone)

    def handle_realized_pnl(pnl: float, when: datetime) -> None:
        nonlocal runtime_state
        risk_engine.record_trade_pnl(pnl, when)
        with runtime_lock:
            runtime_state.daily_pnl_usdt += pnl
            runtime_state = runtime_store.update_state(daily_pnl_usdt=runtime_state.daily_pnl_usdt)

    gateway = PaperOrderGateway(market_service.last_price, logger.getChild("paper_gateway"))
    execution_engine = ExecutionEngine(
        gateway=gateway,
        limits=risk_limits,
        trailing_callback=risk_engine.apply_trailing_stop,
        pnl_callback=handle_realized_pnl,
    )

    status_provider = _build_status_provider(
        config=config,
        runtime_store=runtime_store,
        execution_engine=execution_engine,
        risk_limits=risk_limits,
        rotation_engine=rotation_engine,
    )
    telegram_bot = TelegramBotInterface(
        token=config.credentials.telegram.bot_token,
        chat_id=config.credentials.telegram.chat_id,
        runtime_store=runtime_store,
        status_provider=status_provider,
        logger=logger.getChild("telegram"),
        state_lock=runtime_lock,
    )
    telegram_bot.start()
    telegram_bot.notify_text(
        f"Bot started in {config.trading.bybit.mode.value} mode (update={config.trading.update_interval_sec}s)."
    )

    stop_event = False

    def _request_stop(signum: int, _: object) -> None:
        nonlocal stop_event
        logger.info("Received signal", extra={"signal": signum})
        stop_event = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    update_interval = config.trading.update_interval_sec
    daily_limit_alerted = False
    cooldown_alerted = False
    rotation_state = None

    try:
        while not stop_event:
            loop_start = time.perf_counter()
            runtime_state = runtime_store.load_state()
            _sync_limits(runtime_state, risk_limits)
            try:
                market_states = market_service.refresh()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Market refresh failed", exc_info=exc)
                market_states = {}
            if not market_states:
                time.sleep(update_interval)
                continue
            try:
                rotation_state = rotation_engine.update({str(sym): state for sym, state in market_states.items()})
                _log_rotation_snapshot(_telemetry_storage, rotation_state, logger)
            except Exception as exc:  # pragma: no cover - rotation edge
                logger.warning("Rotation update failed: %s", exc)
            reports = execution_engine.on_market_snapshot(market_states)
            _dispatch_reports(reports, execution_engine, telegram_bot)
            now = datetime.now(tz=timezone.utc)
            if runtime_state.bot_running:
                signals = _collect_signals(strategies, market_states, execution_engine.positions, filters, rotation_state)
                decisions = risk_engine.assess_signals(signals, execution_engine.positions, market_states, now=now)
                for decision in decisions:
                    if decision.approved:
                        execution_engine.handle_risk_decision(decision, market_states.get(decision.symbol))
                breach = risk_engine.daily_state.breach_limit(risk_limits)
                if breach and not daily_limit_alerted:
                    telegram_bot.notify_guardian("Daily loss limit reached – blocking new entries.")
                    daily_limit_alerted = True
                elif not breach:
                    daily_limit_alerted = False
                cooldown_active = risk_engine.cooldown_until and now < risk_engine.cooldown_until
                if cooldown_active and not cooldown_alerted:
                    eta = risk_engine.cooldown_until.astimezone().strftime("%H:%M") if risk_engine.cooldown_until else ""
                    telegram_bot.notify_guardian(f"Cooldown active until {eta}.")
                    cooldown_alerted = True
                elif not cooldown_active:
                    cooldown_alerted = False
            else:
                logger.debug("bot_running is false – skipping new entries")
            loop_duration = time.perf_counter() - loop_start
            sleep_for = max(0.0, update_interval - loop_duration)
            time.sleep(sleep_for)
    except KeyboardInterrupt:  # pragma: no cover - manual exit
        logger.info("Interrupted by user")
    finally:
        telegram_bot.notify_text("Bot shutting down")
        telegram_bot.stop()
        market_service.close()
        logger.info("Shutdown complete")


def _resolve_secrets_path(config_dir: Path) -> Path:
    env_path = os.environ.get("APP_SECRETS_PATH")
    if env_path:
        return Path(env_path)
    candidate = config_dir / "secrets.yaml"
    if candidate.exists():
        return candidate
    fallback = config_dir / "credentials.example.yml"
    print(f"[bootstrap] secrets.yaml not found, using {fallback}")
    return fallback


def _load_runtime_state(store: RuntimeStateStore, risk_config: RiskConfig) -> RuntimeState:
    state_path = store.runtime_state_path
    exists = state_path.exists()
    state = store.load_state()
    if not exists:
        state.virtual_equity_usdt = risk_config.virtual_equity_usdt
        state.per_trade_risk_pct = risk_config.per_trade_risk_pct
        state.max_concurrent_positions = risk_config.max_concurrent_positions
        state.max_daily_loss_pct = risk_config.max_daily_loss_pct
        store.save_state(state)
    return state


def _build_risk_limits(config: AppConfig, state: RuntimeState) -> RiskLimits:
    symbol_caps: Dict[Symbol, float] = {}
    for cfg in config.symbols:
        if cfg.max_notional_usdt:
            symbol_caps[Symbol(cfg.symbol)] = cfg.max_notional_usdt
    risk_cfg = config.trading.risk
    return RiskLimits(
        virtual_equity_usdt=state.virtual_equity_usdt,
        per_trade_risk_pct=state.per_trade_risk_pct,
        max_daily_loss_pct=risk_cfg.max_daily_loss_pct,
        max_concurrent_positions=state.max_concurrent_positions,
        cooldown_after_loss_min=risk_cfg.cooldown_after_loss_min,
        max_leverage=risk_cfg.max_leverage,
        max_slippage_bps=risk_cfg.max_slippage_bps,
        symbol_max_notional_usdt=symbol_caps,
    )


def _sync_limits(state: RuntimeState, limits: RiskLimits) -> None:
    limits.virtual_equity_usdt = state.virtual_equity_usdt
    limits.per_trade_risk_pct = state.per_trade_risk_pct
    limits.max_concurrent_positions = state.max_concurrent_positions


def _build_status_provider(
    *,
    config: AppConfig,
    runtime_store: RuntimeStateStore,
    execution_engine: ExecutionEngine,
    risk_limits: RiskLimits,
    rotation_engine: RotationEngine,
) -> StatusProvider:
    def provider() -> StatusSnapshot:
        state = runtime_store.load_state()
        positions = {str(symbol): pos for symbol, pos in execution_engine.positions.items()}
        session_stats = runtime_store.load_session_stats()
        return StatusSnapshot(
            mode=config.trading.bybit.mode.value,
            runtime_state=state,
            positions=positions,
            risk_limits=risk_limits,
            rotation_state=rotation_engine.state,
            session_stats=session_stats,
        )

    return provider


def _convert_orderbook(snapshot: FeedOrderBookSnapshot) -> OrderBookSnapshot:
    bids = [OrderBookLevel(price=level.price, size=level.size) for level in snapshot.bids]
    asks = [OrderBookLevel(price=level.price, size=level.size) for level in snapshot.asks]
    return OrderBookSnapshot(symbol=snapshot.symbol, timestamp_ms=snapshot.timestamp_ms, bids=bids, asks=asks)


def _convert_trades(trades: Iterable[FeedTrade]) -> list[TradeTick]:
    ticks: list[TradeTick] = []
    for trade in trades:
        ticks.append(
            TradeTick(
                symbol=trade.symbol,
                price=trade.price,
                size=trade.size,
                is_buyer_maker=trade.side == Side.SHORT,
                timestamp_ms=trade.timestamp_ms,
            )
        )
    return ticks


def _collect_signals(
    strategies: Iterable[BaseStrategy],
    market_states: Mapping[Symbol, MarketState],
    positions: Mapping[Symbol, PositionState],
    filters: PreTradeFilters,
    rotation_state: RotationState | None,
) -> list:
    allowed = set(rotation_state.active_symbols) if rotation_state else None
    collected = []
    for strategy in strategies:
        raw = strategy.generate_signals(market_states, positions)
        for signal in raw:
            mkt = market_states.get(signal.symbol)
            if not mkt:
                continue
            if allowed and str(signal.symbol) not in allowed:
                continue
            style = STRATEGY_STYLE_MAP.get(signal.strategy_id, TradeStyle.TREND)
            ok, _ = filters.validate(mkt, trade_style=style)
            if not ok:
                continue
            collected.append(signal)
    return collected


def _dispatch_reports(
    reports: Iterable[ExecutionReport],
    execution_engine: ExecutionEngine,
    telegram_bot: TelegramBotInterface,
) -> None:
    for report in reports:
        if report.event is ExecutionEventType.ENTRY_FILLED:
            telegram_bot.notify_execution(report)
        elif report.event in {
            ExecutionEventType.STOP_LOSS,
            ExecutionEventType.TAKE_PROFIT,
            ExecutionEventType.TIME_STOP,
            ExecutionEventType.EXIT_FILLED,
        }:
            position = execution_engine.positions.get(report.symbol)
            remaining = float(position.size) if position else 0.0
            telegram_bot.notify_execution(report, remaining_qty=remaining)


def _log_rotation_snapshot(
    storage: TelemetryStorage,
    state: RotationState | None,
    logger: logging.Logger,
) -> None:
    if state is None:
        return
    payload = {
        "min_score": state.min_score,
        "top_n": state.top_n,
        "active_symbols": list(state.active_symbols),
        "scores": {symbol: round(score.score, 6) for symbol, score in state.scores.items()},
    }
    event = TelemetryEvent(
        timestamp=state.timestamp,
        event_type="rotation_state",
        payload=payload,
        context={"check_interval_min": state.check_interval_min},
    )
    try:
        storage.append_event(event)
    except Exception as exc:  # pragma: no cover - telemetry path
        logger.warning("Failed to log rotation snapshot: %s", exc)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level safety
        print(f"Fatal error: {exc}", file=sys.stderr)
        raise

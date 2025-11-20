"""Strategy E — Liquidity-Sweep Reversal (TZ §4.7.5)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyELiquiditySweep(BaseStrategy):
    """Fade stop-runs on BTC/ETH when price stretches far from VWAP and flow turns."""

    id = StrategyId.STRATEGY_E
    name = "Liquidity-Sweep Reversal"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.allowed_symbols = set(self.param("symbols", ["BTCUSDT", "ETHUSDT"]))
        self.lookback_bars = int(self.param("n_sweep", 15))
        self.sl_buffer_ticks = float(self.param("sl_buffer_ticks", 3))
        self.tick_size = float(self.param("tick_size", 0.5))
        self.sweep_sigma = float(self.param("sweep_sigma", 1.2))
        self.min_rel_volume = float(self.param("min_rel_volume", 0.9))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))

    def _extreme_levels(self, state: MarketState) -> tuple[float | None, float | None]:
        high = getattr(state, f"rolling_high_{self.lookback_bars}", None)
        low = getattr(state, f"rolling_low_{self.lookback_bars}", None)
        return high, low

    def _flow_reversal(self, state: MarketState, *, expect_long: bool) -> bool:
        flow = state.delta_flow_1m
        if expect_long:
            return flow >= 0
        return flow <= 0

    def _latency_ok(self, state: MarketState) -> bool:
        if state.latency_ms > 200:
            return False
        if state.avg_slippage_bps > state.spread_bps:
            return False
        return True

    def _build_tp_levels(self, side: Side, state: MarketState) -> tuple[TakeProfitLevel, ...]:
        vwap = state.vwap_mean
        range_high = getattr(state, "range_high_12", vwap + state.sigma_vwap)
        range_low = getattr(state, "range_low_12", vwap - state.sigma_vwap)
        tp2 = range_high if side == Side.LONG else range_low
        return (
            TakeProfitLevel(price=vwap, size_pct=0.5, label="tp1_vwap"),
            TakeProfitLevel(price=tp2, size_pct=0.25, label="tp2_range_edge"),
        )

    def _build_signal(self, symbol: Symbol, side: Side, state: MarketState, sl_price: float) -> Signal:
        return Signal(
            symbol=symbol,
            side=side,
            entry_type=EntryType.REVERSAL,
            strategy_id=self.id,
            entry_price=state.mid_price,
            target_risk_pct=self.param("target_risk_pct", 0.01),
            sl_price=sl_price,
            tp_levels=self._build_tp_levels(side, state),
            time_stop_bars=self.time_stop_bars,
            trailing_mode="liquidity_trail",
            trailing_params={"sigma_multiplier": 1.0},
            metadata={"sweep_lookback": self.lookback_bars},
        )

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        symbols = sorted(str(sym) for sym in market_state.keys())
        tf_profiles = sorted({state.tf_profile.value for state in market_state.values()})
        self.logger.debug(
            "generate_signals called",
            extra={
                "strategy_id": self.id.value,
                "strategy_name": self.name,
                "symbols": symbols,
                "tf_profiles": tf_profiles,
            },
        )
        for symbol, state in market_state.items():
            if symbol not in self.allowed_symbols:
                continue
            if not self._latency_ok(state):
                continue
            if state.rel_volume_5m < self.min_rel_volume:
                continue
            sigma = state.sigma_vwap
            if sigma <= 0:
                continue
            pos_side = self.position_side(position_state, symbol)
            price = state.mid_price
            distance = price - state.vwap_mean
            if (
                distance >= self.sweep_sigma * sigma
                and pos_side != Side.SHORT
                and self._flow_reversal(state, expect_long=False)
            ):
                sl_price = price + self.sl_buffer_ticks * self.tick_size
                signals.append(self._build_signal(symbol, Side.SHORT, state, sl_price))
                continue
            if (
                distance <= -self.sweep_sigma * sigma
                and pos_side != Side.LONG
                and self._flow_reversal(state, expect_long=True)
            ):
                sl_price = price - self.sl_buffer_ticks * self.tick_size
                signals.append(self._build_signal(symbol, Side.LONG, state, sl_price))
        symbols_with_signals = sorted({str(s.symbol) for s in signals})
        self.logger.debug(
            "Strategy generated signals",
            extra={
                "strategy_id": self.id.value,
                "n_signals": len(signals),
                "symbols": symbols_with_signals,
            },
        )
        return signals

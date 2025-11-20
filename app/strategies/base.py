"""Base contracts for strategies and normalized signal payloads (TZ §4.6)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.config.models import StrategyRuntimeConfig
from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Symbol
from app.market.models import MarketState

if False:  # pragma: no cover - imported lazily until models exist
    from app.risk.models import PositionState  # noqa: F401  # type: ignore


@dataclass(slots=True)
class TakeProfitLevel:
    """Single TP level definition for partial exits (TZ §4.7.6)."""

    price: Price
    size_pct: float
    label: str = ""


@dataclass(slots=True)
class Signal:
    """Normalized signal contract shared with risk/execution layers."""

    symbol: Symbol
    side: Side
    entry_type: EntryType
    strategy_id: StrategyId
    entry_price: Price
    target_risk_pct: float | None = None
    target_notional: float | None = None
    sl_price: Price | None = None
    tp_levels: Sequence[TakeProfitLevel] = field(default_factory=tuple)
    time_stop_bars: int | None = None
    trailing_mode: str | None = None
    trailing_params: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def risk_multiple(self) -> float:
        """Return distance between entry and SL in price terms."""

        if self.sl_price is None:
            return 0.0
        return abs(float(self.entry_price) - float(self.sl_price))


class BaseStrategy:
    """Abstract interface enforced for all strategies (TZ §4.6.2)."""

    id: StrategyId
    name: str

    def __init__(self, runtime_config: StrategyRuntimeConfig):
        self.runtime_config = runtime_config
        self.parameters = runtime_config.parameters or {}
        self.logger = logging.getLogger("bybit_bot.strategies")

    def param(self, key: str, default: Any) -> Any:
        """Convenience accessor returning parameter overrides when provided."""

        return self.parameters.get(key, default)

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, Any],
    ) -> list[Signal]:
        """Produce trade signals based on latest market snapshot."""

        raise NotImplementedError

    @staticmethod
    def position_side(position_state: Mapping[Symbol, Any], symbol: Symbol) -> Side | None:
        """Return open position side if known (dict or dataclass friendly)."""

        pos = position_state.get(symbol)
        if pos is None:
            return None
        side = getattr(pos, "side", None)
        if side is None and isinstance(pos, Mapping):
            side = pos.get("side")
        if isinstance(side, Side):
            return side
        if isinstance(side, str):
            try:
                return Side(side)
            except ValueError:
                return None
        return None

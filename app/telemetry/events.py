"""Structured telemetry models (events, trades, session stats)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Sequence

from app.core.enums import Side


@dataclass(slots=True)
class TelemetryEvent:
    """Generic event used by JSON-line logs under ``logs/bot_YYYYMMDD.jsonl``."""

    timestamp: datetime
    event_type: str
    level: str = "INFO"
    payload: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass(slots=True)
class TradeRecord:
    """Closed-trade ledger entry persisted to ``reports/trades_YYYYMMDD.csv``."""

    symbol: str
    strategy_id: str
    side: Side
    entry_time: datetime
    exit_time: datetime
    qty: float
    entry_price: float
    exit_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    fees_usdt: float = 0.0
    slippage_cost_usdt: float = 0.0
    gross_pnl_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    traded_notional_usdt: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds()

    def to_csv_row(self) -> Dict[str, Any]:
        return {
            "datetime_open": self.entry_time.isoformat(),
            "datetime_close": self.exit_time.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "size": self.qty,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "fees_usdt": self.fees_usdt,
            "slippage_cost_usdt": self.slippage_cost_usdt,
            "pnl_gross": self.gross_pnl_usdt,
            "pnl_net": self.net_pnl_usdt,
            "strategy_id": self.strategy_id,
            "duration_sec": self.duration_sec,
            "traded_notional_usdt": self.traded_notional_usdt,
        }


@dataclass(slots=True)
class SessionStats:
    """Aggregated telemetry for the active session/day (TZ §2.9)."""

    start_time: datetime
    end_time: datetime
    gross_pnl_usdt: float
    net_pnl_usdt: float
    execution_costs_abs: float
    traded_notional_usdt: float
    trades_count: int
    wins: int
    per_strategy_pnl: Dict[str, float] = field(default_factory=dict)
    per_symbol_pnl: Dict[str, float] = field(default_factory=dict)
    leakage_abs: float | None = None
    leakage_pct: float | None = None
    leakage_valid: bool = False
    execution_costs_pct_of_notional: float | None = None
    win_rate: float | None = None
    last_updated: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def from_trades(
        cls,
        trades: Sequence[TradeRecord],
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> "SessionStats":
        gross_pnl = sum(trade.gross_pnl_usdt for trade in trades)
        net_pnl = sum(trade.net_pnl_usdt for trade in trades)
        execution_costs = gross_pnl - net_pnl
        traded_notional = sum(trade.traded_notional_usdt for trade in trades)
        trades_count = len(trades)
        wins = sum(1 for trade in trades if trade.net_pnl_usdt > 0)
        per_strategy: Dict[str, float] = {}
        per_symbol: Dict[str, float] = {}
        for trade in trades:
            per_strategy[trade.strategy_id] = per_strategy.get(trade.strategy_id, 0.0) + trade.net_pnl_usdt
            per_symbol[trade.symbol] = per_symbol.get(trade.symbol, 0.0) + trade.net_pnl_usdt

        leakage_pct: float | None
        leakage_valid = gross_pnl > 0
        if leakage_valid:
            leakage_pct = (execution_costs / gross_pnl) * 100 if gross_pnl else None
        else:
            leakage_pct = None
        execution_costs_pct = (execution_costs / traded_notional) * 100 if traded_notional else None
        win_rate = (wins / trades_count) * 100 if trades_count else None

        return cls(
            start_time=start_time,
            end_time=end_time,
            gross_pnl_usdt=gross_pnl,
            net_pnl_usdt=net_pnl,
            execution_costs_abs=execution_costs,
            traded_notional_usdt=traded_notional,
            trades_count=trades_count,
            wins=wins,
            per_strategy_pnl=per_strategy,
            per_symbol_pnl=per_symbol,
            leakage_abs=execution_costs,
            leakage_pct=leakage_pct,
            leakage_valid=leakage_valid,
            execution_costs_pct_of_notional=execution_costs_pct,
            win_rate=win_rate,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["start_time"] = self.start_time.isoformat()
        payload["end_time"] = self.end_time.isoformat()
        payload["last_updated"] = self.last_updated.isoformat()
        return payload


__all__ = ["TelemetryEvent", "TradeRecord", "SessionStats"]

"""Runtime JSON state helpers for bot/Telegram coordination."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.core.errors import RuntimeStateError
from app.telemetry.events import SessionStats


@dataclass(slots=True)
class RuntimeState:
    """Mutable parameters persisted in ``runtime/runtime_state.json``.

    Fields mirror the requirements from ARCHITECTURE §5.4 and TZ §2.1: Telegram
    commands toggle ``bot_running`` and update risk controls; the trading loop
    reads the structure on every iteration to honor those constraints.
    """

    bot_running: bool = False
    per_trade_risk_pct: float = 0.0035
    virtual_equity_usdt: float = 150.0
    max_concurrent_positions: int = 2
    daily_pnl_usdt: float = 0.0
    max_daily_loss_pct: float = 0.015
    session_start: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_runtime_update: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_command: str | None = None
    notes: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["session_start"] = self.session_start.isoformat()
        payload["last_runtime_update"] = self.last_runtime_update.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RuntimeState":
        return cls(
            bot_running=payload.get("bot_running", False),
            per_trade_risk_pct=payload.get("per_trade_risk_pct", 0.0035),
            virtual_equity_usdt=payload.get("virtual_equity_usdt", 150.0),
            max_concurrent_positions=payload.get("max_concurrent_positions", 2),
            daily_pnl_usdt=payload.get("daily_pnl_usdt", 0.0),
            max_daily_loss_pct=payload.get("max_daily_loss_pct", 0.015),
            session_start=_parse_dt(payload.get("session_start")),
            last_runtime_update=_parse_dt(payload.get("last_runtime_update")),
            last_command=payload.get("last_command"),
            notes=payload.get("notes"),
        )


class RuntimeStateStore:
    """File-based store for runtime and session statistics."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_state_path = self.runtime_dir / "runtime_state.json"
        self.session_stats_path = self.runtime_dir / "session_stats.json"

    # Runtime state -----------------------------------------------------
    def load_state(self) -> RuntimeState:
        if not self.runtime_state_path.exists():
            return RuntimeState()
        try:
            payload = json.loads(self.runtime_state_path.read_text(encoding="utf-8"))
        except OSError as exc:  # pragma: no cover
            raise RuntimeStateError(f"Failed to read runtime_state.json: {exc}") from exc
        return RuntimeState.from_dict(payload)

    def save_state(self, state: RuntimeState) -> RuntimeState:
        state.last_runtime_update = datetime.now(tz=timezone.utc)
        data = state.to_dict()
        try:
            self.runtime_state_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            raise RuntimeStateError(f"Failed to write runtime_state.json: {exc}") from exc
        return state

    def update_state(self, **changes: Any) -> RuntimeState:
        state = self.load_state()
        for key, value in changes.items():
            if not hasattr(state, key):
                raise RuntimeStateError(f"Unknown runtime_state field: {key}")
            setattr(state, key, value)
        return self.save_state(state)

    # Session stats -----------------------------------------------------
    def load_session_stats(self) -> SessionStats | None:
        if not self.session_stats_path.exists():
            return None
        try:
            payload = json.loads(self.session_stats_path.read_text(encoding="utf-8"))
        except OSError as exc:  # pragma: no cover
            raise RuntimeStateError(f"Failed to read session_stats.json: {exc}") from exc
        start = _parse_dt(payload.get("start_time"))
        end = _parse_dt(payload.get("end_time"))
        stats = SessionStats(
            start_time=start,
            end_time=end,
            gross_pnl_usdt=payload.get("gross_pnl_usdt", 0.0),
            net_pnl_usdt=payload.get("net_pnl_usdt", 0.0),
            execution_costs_abs=payload.get("execution_costs_abs", 0.0),
            traded_notional_usdt=payload.get("traded_notional_usdt", 0.0),
            trades_count=payload.get("trades_count", 0),
            wins=payload.get("wins", 0),
            per_strategy_pnl=payload.get("per_strategy_pnl", {}),
            per_symbol_pnl=payload.get("per_symbol_pnl", {}),
            leakage_abs=payload.get("leakage_abs"),
            leakage_pct=payload.get("leakage_pct"),
            leakage_valid=payload.get("leakage_valid", False),
            execution_costs_pct_of_notional=payload.get("execution_costs_pct_of_notional"),
            win_rate=payload.get("win_rate"),
            last_updated=_parse_dt(payload.get("last_updated")),
        )
        return stats

    def save_session_stats(self, stats: SessionStats) -> SessionStats:
        data = stats.to_dict()
        try:
            self.session_stats_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            raise RuntimeStateError(f"Failed to write session_stats.json: {exc}") from exc
        return stats


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(tz=timezone.utc)
    return datetime.fromisoformat(value)


__all__ = ["RuntimeState", "RuntimeStateStore"]

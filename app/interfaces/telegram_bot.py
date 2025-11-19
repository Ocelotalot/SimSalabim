from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Mapping

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext, CommandHandler, Updater

from app.execution.models import ExecutionEventType, ExecutionReport
from app.risk.models import PositionState, RiskLimits
from app.rotation.models import RotationState
from app.runtime.state import RuntimeState, RuntimeStateStore
from app.telemetry.events import SessionStats

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class StatusSnapshot:
    """Serializable view of the runtime for Telegram `/status`."""

    mode: str
    runtime_state: RuntimeState
    positions: Mapping[str, PositionState]
    risk_limits: RiskLimits
    rotation_state: RotationState | None
    session_stats: SessionStats | None = None


StatusProvider = Callable[[], StatusSnapshot]


class TelegramBotInterface:
    """Telegram layer responsible for commands and notifications.

    The bot is intentionally thin – it only updates :class:`RuntimeState` via
    :class:`RuntimeStateStore` and asks the orchestration layer for status
    snapshots. The trading loop reloads the JSON file every iteration, so the
    `/start_bot`, `/stop_bot`, `/set_*` commands immediately influence the core
    runtime without introducing direct coupling.
    """

    def __init__(
        self,
        *,
        token: str,
        chat_id: int,
        runtime_store: RuntimeStateStore,
        status_provider: StatusProvider,
        logger: logging.Logger | None = None,
        state_lock: threading.Lock | None = None,
    ) -> None:
        self._bot = Bot(token=token)
        self._updater = Updater(bot=self._bot, use_context=True)
        self._dispatcher = self._updater.dispatcher
        self._chat_id = chat_id
        self._runtime_store = runtime_store
        self._status_provider = status_provider
        self._logger = logger or LOGGER
        self._lock = state_lock or threading.Lock()
        self._running = False
        self._register_handlers()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._updater.start_polling(drop_pending_updates=True)
        self._running = True
        self._logger.info("Telegram bot polling started")

    def stop(self) -> None:
        if not self._running:
            return
        self._updater.stop()
        self._running = False
        self._logger.info("Telegram bot polling stopped")

    # ------------------------------------------------------------------
    # Public notifications
    # ------------------------------------------------------------------
    def notify_execution(self, report: ExecutionReport, remaining_qty: float | None = None) -> None:
        """Push concise execution updates to the operator."""

        if not self._chat_id:
            return
        if report.event is ExecutionEventType.ENTRY_FILLED:
            text = (
                f"🚀 Entry filled: {report.symbol} {report.side.value.upper()} "
                f"qty={report.quantity:.4f} price={report.price:.2f}"
            )
        elif report.event in {
            ExecutionEventType.STOP_LOSS,
            ExecutionEventType.TAKE_PROFIT,
            ExecutionEventType.TIME_STOP,
            ExecutionEventType.EXIT_FILLED,
        }:
            remaining = f" remaining={remaining_qty:.4f}" if remaining_qty is not None else ""
            text = (
                f"🏁 Exit ({report.event.value}): {report.symbol} {report.side.value.upper()} "
                f"qty={report.quantity:.4f} price={report.price:.2f} reason={report.reason or '-'}{remaining}"
            )
        else:
            text = (
                f"ℹ️ Execution event: {report.event.value} {report.symbol} "
                f"{report.side.value} reason={report.reason or '-'}"
            )
        self._send_message(text)

    def notify_guardian(self, message: str) -> None:
        """Send alerts for risk guard activations (daily limit, cooldown)."""

        if not message:
            return
        self._send_message(f"⚠️ {message}")

    def notify_text(self, text: str) -> None:
        """Send arbitrary service messages (e.g. startup/shutdown)."""

        self._send_message(text)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _register_handlers(self) -> None:
        self._dispatcher.add_handler(CommandHandler("start_bot", self._wrap(self._cmd_start)))
        self._dispatcher.add_handler(CommandHandler("stop_bot", self._wrap(self._cmd_stop)))
        self._dispatcher.add_handler(CommandHandler("status", self._wrap(self._cmd_status)))
        self._dispatcher.add_handler(CommandHandler("set_risk", self._wrap(self._cmd_set_risk)))
        self._dispatcher.add_handler(CommandHandler("set_equity", self._wrap(self._cmd_set_equity)))
        self._dispatcher.add_handler(CommandHandler("set_max_positions", self._wrap(self._cmd_set_max_positions)))

    def _wrap(self, handler: Callable[[Update, CallbackContext], None]) -> Callable[[Update, CallbackContext], None]:
        def wrapped(update: Update, context: CallbackContext) -> None:
            if not self._authorize(update):
                return
            try:
                handler(update, context)
            except Exception as exc:  # pragma: no cover - defensive logging
                self._logger.exception("Telegram handler failed", exc_info=exc)
                self._reply(update, "Command failed – check logs")

        return wrapped

    def _authorize(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat is None or chat.id != self._chat_id:
            self._logger.warning("Unauthorized Telegram chat", extra={"chat_id": getattr(chat, "id", None)})
            return False
        return True

    def _cmd_start(self, update: Update, _: CallbackContext) -> None:
        state = self._update_state(bot_running=True, last_command="/start_bot")
        self._reply(update, f"Bot state: trading (per_trade_risk={state.per_trade_risk_pct:.4f})")

    def _cmd_stop(self, update: Update, _: CallbackContext) -> None:
        self._update_state(bot_running=False, last_command="/stop_bot")
        self._reply(
            update,
            "Bot state: stopped. Open positions remain under SL/TP supervision.",
        )

    def _cmd_status(self, update: Update, _: CallbackContext) -> None:
        snapshot = self._status_provider()
        text = self._format_status(snapshot)
        self._reply(update, text)

    def _cmd_set_risk(self, update: Update, context: CallbackContext) -> None:
        args = context.args
        if not args:
            self._reply(update, "Usage: /set_risk <float>")
            return
        try:
            value = float(args[0])
        except ValueError:
            self._reply(update, "Invalid number")
            return
        if value <= 0 or value >= 0.1:
            self._reply(update, "Value must be between 0 and 0.1")
            return
        state = self._update_state(per_trade_risk_pct=value, last_command="/set_risk")
        self._reply(update, f"per_trade_risk_pct updated to {state.per_trade_risk_pct:.4f}")

    def _cmd_set_equity(self, update: Update, context: CallbackContext) -> None:
        args = context.args
        if not args:
            self._reply(update, "Usage: /set_equity <value>")
            return
        try:
            value = float(args[0])
        except ValueError:
            self._reply(update, "Invalid number")
            return
        if value <= 0:
            self._reply(update, "Value must be positive")
            return
        state = self._update_state(virtual_equity_usdt=value, last_command="/set_equity")
        self._reply(update, f"virtual_equity_usdt updated to {state.virtual_equity_usdt:.2f}")

    def _cmd_set_max_positions(self, update: Update, context: CallbackContext) -> None:
        args = context.args
        if not args:
            self._reply(update, "Usage: /set_max_positions <int>")
            return
        try:
            value = int(args[0])
        except ValueError:
            self._reply(update, "Invalid integer")
            return
        if value <= 0:
            self._reply(update, "Value must be >= 1")
            return
        state = self._update_state(max_concurrent_positions=value, last_command="/set_max_positions")
        self._reply(update, f"max_concurrent_positions updated to {state.max_concurrent_positions}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _reply(self, update: Update, text: str) -> None:
        if not update.effective_chat:
            return
        try:
            update.effective_chat.send_message(text)
        except TelegramError as exc:  # pragma: no cover - depends on Telegram availability
            self._logger.warning("Failed to reply in chat", exc_info=exc)

    def _update_state(self, **changes) -> RuntimeState:
        with self._lock:
            state = self._runtime_store.update_state(**changes)
        return state

    def _format_status(self, snapshot: StatusSnapshot) -> str:
        state = snapshot.runtime_state
        limit_abs = -state.virtual_equity_usdt * state.max_daily_loss_pct
        lines = [
            f"Mode: {snapshot.mode}",
            f"Bot: {'trading' if state.bot_running else 'idle'}",
            f"per_trade_risk_pct: {state.per_trade_risk_pct:.4f}",
            f"virtual_equity_usdt: {state.virtual_equity_usdt:.2f}",
            f"max_concurrent_positions: {state.max_concurrent_positions}",
            f"daily_pnl_usdt: {state.daily_pnl_usdt:.2f} / limit {limit_abs:.2f}",
        ]
        if snapshot.rotation_state:
            active = ", ".join(snapshot.rotation_state.active_symbols)
            lines.append(f"rotation_active: {active or 'none'}")
        if snapshot.session_stats:
            stats = snapshot.session_stats
            lines.append(
                "session_stats: "
                f"net={stats.net_pnl_usdt:.2f} trades={stats.trades_count} win_rate={stats.win_rate or 0:.1f}%"
            )
        if snapshot.positions:
            lines.append("positions:")
            for symbol, pos in snapshot.positions.items():
                lines.append(
                    " - "
                    f"{symbol}: {pos.side.value.upper()} size={float(pos.size):.4f} entry={float(pos.entry_price):.2f} "
                    f"sl={float(pos.current_sl_price):.2f}"
                )
        else:
            lines.append("positions: none")
        return "\n".join(lines)

    def _send_message(self, text: str) -> None:
        if not text:
            return
        try:
            self._bot.send_message(chat_id=self._chat_id, text=text)
        except TelegramError as exc:  # pragma: no cover - depends on Telegram availability
            self._logger.warning("Failed to send Telegram message", exc_info=exc)


__all__ = ["TelegramBotInterface", "StatusSnapshot", "StatusProvider"]

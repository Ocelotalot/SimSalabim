"""Helpers for persisting telemetry artifacts (events, trades, stats)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from app.core.errors import TelemetryError
from app.telemetry.events import SessionStats, TelemetryEvent, TradeRecord


class TelemetryStorage:
    """Write structured telemetry objects to disk.

    ``TelemetryStorage`` is typically instantiated by the main runtime and passed
    to the execution engine. Strategies push `TelemetryEvent` instances for
    misc. happenings, ExecutionEngine records :class:`TradeRecord` objects when a
    position closes, and the session scheduler periodically emits
    :class:`SessionStats` snapshots.
    """

    def __init__(
        self,
        *,
        logs_dir: Path,
        reports_dir: Path,
    ) -> None:
        self._logs_dir = logs_dir
        self._reports_dir = reports_dir
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # JSON event logs
    # ------------------------------------------------------------------
    def append_event(self, event: TelemetryEvent) -> Path:
        """Append ``event`` as JSON to ``logs/bot_YYYYMMDD.jsonl``."""

        date_str = event.timestamp.strftime("%Y%m%d")
        path = self._logs_dir / f"bot_{date_str}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                json.dump(event.to_dict(), handle, ensure_ascii=False)
                handle.write("\n")
        except OSError as exc:  # pragma: no cover - filesystem errors are rare
            raise TelemetryError(f"Failed to write telemetry event: {exc}") from exc
        return path

    # ------------------------------------------------------------------
    # Trade ledger (CSV)
    # ------------------------------------------------------------------
    def append_trade(self, record: TradeRecord) -> Path:
        """Persist ``record`` into ``reports/trades_YYYYMMDD.csv``."""

        date_str = record.exit_time.strftime("%Y%m%d")
        path = self._reports_dir / f"trades_{date_str}.csv"
        row = record.to_csv_row()
        write_header = not path.exists()
        try:
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except OSError as exc:  # pragma: no cover
            raise TelemetryError(f"Failed to write trade record: {exc}") from exc
        return path

    # ------------------------------------------------------------------
    # Session stats JSON report
    # ------------------------------------------------------------------
    def write_session_stats(self, stats: SessionStats) -> Path:
        """Persist ``stats`` to ``reports/session_stats_YYYYMMDD.json``."""

        date_str = stats.end_time.strftime("%Y%m%d")
        path = self._reports_dir / f"session_stats_{date_str}.json"
        try:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(stats.to_dict(), handle, indent=2, ensure_ascii=False)
        except OSError as exc:  # pragma: no cover
            raise TelemetryError(f"Failed to write session stats: {exc}") from exc
        return path


def default_storage(base_dir: Path) -> TelemetryStorage:
    """Factory returning storage rooted under ``base_dir``."""

    logs_dir = base_dir / "logs"
    reports_dir = base_dir / "reports"
    return TelemetryStorage(logs_dir=logs_dir, reports_dir=reports_dir)


__all__ = ["TelemetryStorage", "default_storage"]

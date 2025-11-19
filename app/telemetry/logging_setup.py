"""Centralized logging configuration for the bot."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from logging import Logger
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    """Serialize LogRecord fields as JSON for ingestion-friendly logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps({key: value})
            except TypeError:
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    log_dir: Path,
    level: str = "INFO",
    logger_name: str = "bybit_bot",
) -> Logger:
    """Configure root logger with a JSON rotating file handler."""

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot_current.jsonl"
    handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger(logger_name)
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.addHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter())
    logger.addHandler(stream_handler)

    logger.propagate = False
    logger.debug("JSON logging configured", extra={"log_file": str(log_file)})
    return logger


__all__ = ["configure_logging", "JsonFormatter"]

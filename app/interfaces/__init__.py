"""External user interfaces package."""

from .telegram_bot import StatusProvider, StatusSnapshot, TelegramBotInterface

__all__ = ["TelegramBotInterface", "StatusSnapshot", "StatusProvider"]

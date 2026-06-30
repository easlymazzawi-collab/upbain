"""Bot delivery — re-export bot_manager (multi-bot P1+)."""

from research_platform.bot_manager import (
    bot_status,
    restart_bot,
    start_delivery_bot_background,
    stop_all_bots,
    stop_delivery_bot,
)

__all__ = [
    "start_delivery_bot_background",
    "stop_delivery_bot",
    "stop_all_bots",
    "restart_bot",
    "bot_status",
]

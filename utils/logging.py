"""Logging setup for the trading bot."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(level: str = "INFO") -> None:
    """Configure console and file logging."""
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/bot.log", encoding="utf-8"),
        ],
    )

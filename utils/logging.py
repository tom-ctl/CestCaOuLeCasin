"""Backward-compatible logging setup import."""

from __future__ import annotations

from utils.logger import configure_logger


def configure_logging(level: str = "INFO") -> None:
    """Configure console and file logging."""
    configure_logger(level=level, log_file="logs.txt")

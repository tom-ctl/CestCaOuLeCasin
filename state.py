"""Shared runtime state for the trading bot."""

from __future__ import annotations

from typing import Any

bot_state: dict[str, Any] = {
    "sleep_mode": False,
}

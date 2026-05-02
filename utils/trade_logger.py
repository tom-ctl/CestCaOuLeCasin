"""CSV trade logging."""

from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger


class TradeLogger:
    """Append trade events to a CSV file."""

    fieldnames = [
        "timestamp",
        "event",
        "symbol",
        "side",
        "amount",
        "price",
        "stop_loss",
        "take_profit",
        "confidence",
        "status",
        "details",
    ]

    def __init__(self, path: Path) -> None:
        self.logger = get_logger("trade_log")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=self.fieldnames)
                writer.writeheader()
            self.logger.info("Trade log created: %s", self.path)

    def log(self, event: str, payload: dict[str, Any] | Any) -> None:
        """Write a trade event to CSV."""
        data = asdict(payload) if is_dataclass(payload) else dict(payload)
        row = {field: "" for field in self.fieldnames}
        row.update(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "symbol": data.get("symbol", ""),
                "side": data.get("side", data.get("action", "")),
                "amount": data.get("amount", ""),
                "price": data.get("price", data.get("entry_price", "")),
                "stop_loss": data.get("stop_loss", ""),
                "take_profit": data.get("take_profit", ""),
                "confidence": data.get("confidence", ""),
                "status": data.get("status", ""),
                "details": data.get("details", ""),
            }
        )
        with self.path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=self.fieldnames)
            writer.writerow(row)
        self.logger.debug("Trade event logged: %s", row)

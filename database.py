"""SQLite trade storage for preprod trade analytics."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from utils.logger import get_logger

DB_PATH = Path("trades.db")

logger = get_logger("database")


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection for the trade database."""
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """Create the trade table if it does not already exist."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_open TEXT,
                timestamp_close TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl REAL,
                pnl_pct REAL,
                confidence REAL,
                ema9 REAL,
                ema21 REAL,
                rsi REAL,
                price_change REAL,
                trend_strength REAL,
                rsi_distance REAL,
                score REAL
            )
            """
        )
        _ensure_column(cursor, "rsi_distance", "REAL")
        _ensure_column(cursor, "score", "REAL")
        conn.commit()
        logger.info("SQLite trade database initialized path=%s", DB_PATH)
    except sqlite3.Error as exc:
        logger.exception("Failed to initialize SQLite database: %s", exc)
        raise
    finally:
        conn.close()


def log_trade(trade: dict[str, Any]) -> None:
    """Persist one closed trade with its signal metrics."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (
                timestamp_open,
                timestamp_close,
                symbol,
                side,
                entry_price,
                exit_price,
                pnl,
                pnl_pct,
                confidence,
                ema9,
                ema21,
                rsi,
                price_change,
                trend_strength,
                rsi_distance,
                score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["timestamp_open"],
                trade["timestamp_close"],
                trade["symbol"],
                trade["side"],
                trade["entry_price"],
                trade["exit_price"],
                trade["pnl"],
                trade["pnl_pct"],
                trade["confidence"],
                trade["ema9"],
                trade["ema21"],
                trade["rsi"],
                trade["price_change"],
                trade["trend_strength"],
                trade.get("rsi_distance", 0.0),
                trade.get("score", 0.0),
            ),
        )
        conn.commit()
        logger.info(
            "Trade logged to SQLite %s %s pnl=%.4f pnl_pct=%.2f",
            trade["symbol"],
            trade["side"],
            float(trade["pnl"]),
            float(trade["pnl_pct"]),
        )
    except sqlite3.Error as exc:
        logger.exception("Failed to log trade to SQLite: %s", exc)
        raise
    finally:
        conn.close()


def _ensure_column(cursor: sqlite3.Cursor, column_name: str, column_type: str) -> None:
    """Add a nullable column to the trades table when upgrading an existing database."""
    cursor.execute("PRAGMA table_info(trades)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}")


def get_trade_stats() -> tuple[int, float | None, float | None]:
    """Return count, average PnL, and total PnL from logged trades."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), AVG(pnl), SUM(pnl) FROM trades")
        count, avg_pnl, total_pnl = cursor.fetchone()
        return int(count), avg_pnl, total_pnl
    finally:
        conn.close()

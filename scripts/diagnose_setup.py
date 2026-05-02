"""Validate local configuration, Telegram, and Binance testnet connectivity."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telegram import Bot

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings
from exchange import BinanceClient
from utils.logger import configure_logger, get_logger

logger = get_logger("diagnose")


async def main() -> None:
    """Run non-trading setup diagnostics."""
    settings = get_settings()
    settings.validate()
    configure_logger(settings.runtime_log_level, None)

    telegram = Bot(settings.telegram_bot_token)
    me = await telegram.get_me()
    await telegram.send_message(
        chat_id=settings.telegram_chat_id,
        text="Trading bot setup test: Telegram connection OK.",
    )
    logger.info("Telegram ok: @%s", me.username)

    exchange = BinanceClient(settings)
    try:
        await exchange.load_markets()
        balance = await exchange.get_balance()
        usdt = balance.get("USDT", {})
        logger.info("Binance sandbox ok: USDT free=%s total=%s", usdt.get("free"), usdt.get("total"))
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())

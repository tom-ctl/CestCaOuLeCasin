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


async def main() -> None:
    """Run non-trading setup diagnostics."""
    settings = get_settings()
    settings.validate()

    telegram = Bot(settings.telegram_bot_token)
    me = await telegram.get_me()
    await telegram.send_message(
        chat_id=settings.telegram_chat_id,
        text="Trading bot setup test: Telegram connection OK.",
    )
    print(f"telegram ok: @{me.username}")

    exchange = BinanceClient(settings)
    try:
        await exchange.load_markets()
        balance = await exchange.get_balance()
        usdt = balance.get("USDT", {})
        print(f"binance sandbox ok: USDT free={usdt.get('free')} total={usdt.get('total')}")
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())

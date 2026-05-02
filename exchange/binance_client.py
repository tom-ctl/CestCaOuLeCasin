"""Binance exchange wrapper built on CCXT."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp.connector
import aiohttp.resolver
import ccxt.async_support as ccxt

from config import Settings
from utils.retry import async_retry


class BinanceClient:
    """Small CCXT wrapper for Binance spot trading."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(__name__)
        self._use_system_dns_resolver()
        self.exchange = ccxt.binance(
            {
                "apiKey": settings.binance_api_key,
                "secret": settings.binance_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        if settings.binance_test_mode:
            self.exchange.set_sandbox_mode(True)
            self.logger.info("Binance sandbox mode enabled")

    @staticmethod
    def _use_system_dns_resolver() -> None:
        """Force aiohttp to use the OS resolver instead of aiodns on Windows."""
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver

    async def close(self) -> None:
        """Close CCXT network resources."""
        await self.exchange.close()

    @async_retry()
    async def load_markets(self) -> dict[str, Any]:
        """Load exchange market metadata."""
        return await self.exchange.load_markets()

    @async_retry()
    async def get_balance(self) -> dict[str, Any]:
        """Return account balances."""
        return await self.exchange.fetch_balance()

    @async_retry()
    async def get_price(self, symbol: str) -> float:
        """Return the latest traded price for a symbol."""
        ticker = await self.exchange.fetch_ticker(symbol)
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise RuntimeError(f"No price available for {symbol}")
        return float(price)

    @async_retry()
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[list[float]]:
        """Fetch OHLCV candles from the exchange."""
        return await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    @async_retry()
    async def create_order(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Create a market order."""
        side_lower = side.lower()
        if side_lower not in {"buy", "sell"}:
            raise ValueError("side must be BUY or SELL")
        precise_amount = float(self.exchange.amount_to_precision(symbol, amount))
        self.logger.info("Creating %s market order for %s amount=%s", side, symbol, precise_amount)
        return await self.exchange.create_market_order(symbol, side_lower, precise_amount)

    async def close_position(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Close an open spot position using the opposite side."""
        close_side = "SELL" if side.upper() == "BUY" else "BUY"
        return await self.create_order(symbol, close_side, amount)

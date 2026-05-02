"""Binance exchange wrapper built on CCXT."""

from __future__ import annotations

from typing import Any

import aiohttp.connector
import aiohttp.resolver
import ccxt.async_support as ccxt

from config import Settings
from utils.logger import get_logger
from utils.retry import async_retry


class BinanceClient:
    """Small CCXT wrapper for Binance spot trading."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("exchange")
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
        self.logger.info("Exchange client initialized | sandbox=%s | test_mode=%s", settings.binance_test_mode, settings.test_mode)

    @property
    def symbols(self) -> list[str]:
        """Return loaded exchange symbols."""
        return list(self.exchange.symbols or [])

    @staticmethod
    def _use_system_dns_resolver() -> None:
        """Force aiohttp to use the OS resolver instead of aiodns on Windows."""
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver

    async def close(self) -> None:
        """Close CCXT network resources."""
        try:
            await self.exchange.close()
            self.logger.info("Exchange connection closed")
        except Exception as exc:
            self.logger.error("API error closing exchange connection: %s", exc)
            raise

    @async_retry()
    async def load_markets(self) -> dict[str, Any]:
        """Load exchange market metadata."""
        try:
            markets = await self.exchange.load_markets()
            self.logger.info("Exchange markets loaded | count=%s", len(markets))
            self.logger.debug("Raw markets keys sample: %s", list(markets.keys())[:10])
            return markets
        except Exception as exc:
            self.logger.error("API error loading markets: %s", exc)
            raise

    @async_retry()
    async def get_balance(self) -> dict[str, Any]:
        """Return account balances."""
        try:
            balance = await self.exchange.fetch_balance()
            self.logger.info("Balance fetched")
            self.logger.debug("Raw balance response: %s", balance)
            return balance
        except Exception as exc:
            self.logger.error("API error fetching balance: %s", exc)
            raise

    @async_retry()
    async def get_price(self, symbol: str) -> float:
        """Return the latest traded price for a symbol."""
        try:
            if not self.is_valid_symbol(symbol):
                self.logger.warning("Invalid symbol skipped: %s", symbol)
                raise ValueError(f"Invalid exchange symbol: {symbol}")
            ticker = await self.exchange.fetch_ticker(symbol)
            self.logger.debug("Raw ticker response %s: %s", symbol, ticker)
            price = ticker.get("last") or ticker.get("close")
            if price is None:
                raise RuntimeError(f"No price available for {symbol}")
            parsed_price = float(price)
            self.logger.debug("Fetched price %s: %s", symbol, parsed_price)
            return parsed_price
        except Exception as exc:
            self.logger.error("API error fetching price %s: %s", symbol, exc)
            raise

    @async_retry()
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[list[float]]:
        """Fetch OHLCV candles from the exchange."""
        try:
            if not self.is_valid_symbol(symbol):
                self.logger.warning("Invalid symbol skipped: %s", symbol)
                raise ValueError(f"Invalid exchange symbol: {symbol}")
            candles = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            self.logger.debug("Fetched OHLCV %s timeframe=%s limit=%s count=%s", symbol, timeframe, limit, len(candles))
            self.logger.debug("Latest candle %s: %s", symbol, candles[-1] if candles else None)
            return candles
        except Exception as exc:
            self.logger.error("API error fetching OHLCV %s: %s", symbol, exc)
            raise

    @async_retry()
    async def create_order(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Create a market order."""
        side_lower = side.lower()
        if side_lower not in {"buy", "sell"}:
            raise ValueError("side must be BUY or SELL")
        if not self.is_valid_symbol(symbol):
            self.logger.warning("Invalid symbol skipped: %s", symbol)
            raise ValueError(f"Invalid exchange symbol: {symbol}")
        precise_amount = float(self.exchange.amount_to_precision(symbol, amount))
        self.logger.info("Order request %s %s amount=%s", symbol, side.upper(), precise_amount)
        try:
            order = await self.exchange.create_market_order(symbol, side_lower, precise_amount)
            self.logger.info("Order response received %s %s %s status=%s", symbol, side.upper(), precise_amount, order.get("status"))
            if order.get("status") != "closed":
                self.logger.warning("Order not filled: %s status=%s", symbol, order.get("status"))
            self.logger.debug("Raw order response: %s", order)
            return order
        except Exception as exc:
            self.logger.error("Order failed %s %s amount=%s: %s", symbol, side.upper(), precise_amount, exc)
            raise

    async def close_position(self, symbol: str, side: str, amount: float) -> dict[str, Any]:
        """Close an open spot position using the opposite side."""
        close_side = "SELL" if side.upper() == "BUY" else "BUY"
        return await self.create_order(symbol, close_side, amount)

    def is_valid_symbol(self, symbol: str) -> bool:
        """Return whether a symbol is known by the loaded exchange markets."""
        symbols = self.exchange.symbols
        if symbols is None:
            self.logger.warning("Symbol validation requested before markets loaded: %s", symbol)
            return False
        return symbol in symbols

    @staticmethod
    def is_order_filled(order: dict[str, Any]) -> bool:
        """Return True only when CCXT reports the order as fully closed."""
        return order.get("status") == "closed"

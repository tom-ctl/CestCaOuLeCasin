"""Preprod simulated trading engine."""

from __future__ import annotations

import random

import pandas as pd

from config import Settings
from state import bot_state
from strategy import generate_signal
from utils.logger import get_logger
from wallet import Wallet


class TradingEngine:
    """Generate permissive strategy signals and manage simulated positions."""

    def __init__(self, wallet: Wallet, settings: Settings) -> None:
        self.wallet = wallet
        self.settings = settings
        self.logger = get_logger("preprod_engine")
        self.trading_enabled = True
        self.prices = {symbol: self._initial_price(symbol) for symbol in settings.trading_symbols}
        self.market_data = {
            symbol: self._initial_market_data(symbol, self.prices[symbol])
            for symbol in settings.trading_symbols
        }
        self._loop_count = 0

    def start_trading(self) -> None:
        """Enable simulated trade entries."""
        self.trading_enabled = True
        bot_state["sleep_mode"] = False
        self.logger.info("Preprod trading enabled")

    def stop_trading(self) -> None:
        """Disable simulated trade entries."""
        self.trading_enabled = False
        self.logger.warning("Preprod trading disabled")

    def activate_sleep(self) -> None:
        """Enter sleep mode and block new simulated entries."""
        bot_state["sleep_mode"] = True
        self.trading_enabled = False
        self.logger.warning("Preprod sleep mode activated")

    async def tick(self) -> None:
        """Run one simulated trading loop iteration."""
        self._loop_count += 1
        self._update_prices()
        self._update_positions()
        self._close_positions_if_needed()

        if bot_state["sleep_mode"] or not self.trading_enabled:
            self.logger.info(
                "Preprod loop %s | trading=%s | sleep=%s | positions=%s | equity=%.2f",
                self._loop_count,
                self.trading_enabled,
                bot_state["sleep_mode"],
                len(self.wallet.open_positions),
                self.wallet.total_equity,
            )
            return

        for symbol in self.settings.trading_symbols:
            signal = generate_signal(self.market_data[symbol])
            self.logger.info(
                "[SIGNAL] %s %s conf=%.2f reason=%s",
                symbol,
                signal["signal"],
                signal["confidence"],
                signal["reason"],
            )
            if signal["signal"] is not None and signal["confidence"] > 0.3:
                self._open_trade(symbol, signal)

        self.logger.info(
            "Preprod loop %s | trading=%s | sleep=%s | positions=%s | equity=%.2f",
            self._loop_count,
            self.trading_enabled,
            bot_state["sleep_mode"],
            len(self.wallet.open_positions),
            self.wallet.total_equity,
        )

    def status_message(self) -> str:
        """Return Telegram status text."""
        mode = "sleep" if bot_state["sleep_mode"] else "active" if self.trading_enabled else "stopped"
        return (
            f"Mode: {mode}\n"
            f"Positions: {len(self.wallet.open_positions)}\n"
            f"Equity: {self.wallet.total_equity:.2f} USDT"
        )

    def _open_trade(self, symbol: str, signal: dict) -> None:
        if len(self.wallet.open_positions) >= self.settings.preprod_max_positions:
            self.logger.debug("Signal skipped: max positions reached")
            return
        side = str(signal["signal"])
        price = self.prices[symbol]
        size = self.settings.preprod_trade_notional / price
        self.wallet.open_position(symbol, price, size, side)
        self.logger.info(
            "Preprod trade opened %s %s size=%.8f notional=%.2f confidence=%.2f",
            symbol,
            side,
            size,
            self.settings.preprod_trade_notional,
            signal["confidence"],
        )

    def _update_prices(self) -> None:
        for symbol, price in self.prices.items():
            move = random.uniform(-0.003, 0.003)
            new_price = max(price * (1 + move), 0.00000001)
            self.prices[symbol] = new_price
            high = max(price, new_price) * (1 + random.uniform(0, 0.0008))
            low = min(price, new_price) * (1 - random.uniform(0, 0.0008))
            volume = random.uniform(80, 140)
            self.market_data[symbol].loc[len(self.market_data[symbol])] = {
                "open": price,
                "high": high,
                "low": low,
                "close": new_price,
                "volume": volume,
            }
            self.market_data[symbol] = self.market_data[symbol].tail(100).reset_index(drop=True)
            self.logger.debug("Simulated price %s %.8f -> %.8f", symbol, price, new_price)

    def _update_positions(self) -> None:
        for position in list(self.wallet.open_positions):
            self.wallet.update_position_price(position, self.prices[position.symbol])
        self.logger.debug("Wallet update equity=%.2f total_pnl=%.4f", self.wallet.total_equity, self.wallet.total_pnl)

    def _close_positions_if_needed(self) -> None:
        for position in list(self.wallet.open_positions):
            pnl_pct = position.pnl_pct
            should_close = pnl_pct >= 0.6 or pnl_pct <= -0.4 or random.random() < 0.05
            if should_close:
                trade = self.wallet.close_position(position, position.current_price)
                self.logger.info("Preprod trade closed %s %s pnl=%.4f pnl_pct=%.2f", trade.symbol, trade.side, trade.pnl, pnl_pct)

    @staticmethod
    def _initial_price(symbol: str) -> float:
        base = symbol.split("/")[0]
        defaults = {
            "BTC": 100000.0,
            "ETH": 3500.0,
            "BNB": 650.0,
            "SOL": 150.0,
            "XRP": 0.6,
        }
        return defaults.get(base, 100.0)

    @staticmethod
    def _initial_market_data(symbol: str, start_price: float) -> pd.DataFrame:
        rows = []
        price = start_price
        for _ in range(25):
            move = random.uniform(-0.002, 0.002)
            close = max(price * (1 + move), 0.00000001)
            rows.append(
                {
                    "open": price,
                    "high": max(price, close) * (1 + random.uniform(0, 0.0008)),
                    "low": min(price, close) * (1 - random.uniform(0, 0.0008)),
                    "close": close,
                    "volume": random.uniform(80, 140),
                }
            )
            price = close
        return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])

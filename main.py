"""Trading bot orchestrator."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from config import get_settings
from exchange import BinanceClient
from execution import TradeExecutor
from risk_management import RiskManager
from strategy import SignalEngine
from telegram_bot import TelegramTradeBot
from utils.logging import configure_logging
from utils.trade_logger import TradeLogger


class TradingBot:
    """Coordinate exchange data, strategy signals, confirmations, and execution."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.settings.validate()
        configure_logging(self.settings.log_level)
        self.logger = logging.getLogger(__name__)
        self.exchange = BinanceClient(self.settings)
        self.signal_engine = SignalEngine(self.settings)
        self.risk_manager = RiskManager(self.settings)
        self.telegram = TelegramTradeBot(self.settings)
        self.trade_logger = TradeLogger(self.settings.trade_log_path)
        self.executor = TradeExecutor(
            self.exchange,
            self.trade_logger,
            self.settings.poll_interval_seconds,
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Run the main trading loop."""
        await self.exchange.load_markets()
        await self.telegram.start()
        self.logger.info("Trading bot started")
        try:
            while not self._stop.is_set():
                await self._scan_once()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.settings.poll_interval_seconds,
                    )
        finally:
            await self.telegram.stop()
            await self.exchange.close()

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._stop.set()

    async def _scan_once(self) -> None:
        for symbol in self.settings.trading_symbols:
            try:
                await self._process_symbol(symbol)
            except Exception as exc:  # noqa: BLE001 - keep bot alive on symbol-level failures.
                self.logger.exception("Error while processing %s: %s", symbol, exc)
                await self.telegram.send_trade_report(f"Error while processing {symbol}: {exc}")

    async def _process_symbol(self, symbol: str) -> None:
        candle_limit = max(self.settings.breakout_lookback, self.settings.volume_lookback) + 5
        candles = await self.exchange.get_ohlcv(symbol, self.settings.timeframe, candle_limit)
        signal_result = self.signal_engine.analyze(symbol, candles)
        if signal_result is None:
            self.logger.debug("No signal for %s", symbol)
            return
        if signal_result.confidence < self.settings.min_confidence:
            self.logger.info(
                "Ignoring %s signal below confidence threshold: %.2f",
                symbol,
                signal_result.confidence,
            )
            return

        balance = await self.exchange.get_balance()
        equity = self.risk_manager.estimate_usdt_equity(balance)
        plan = self.risk_manager.build_position_plan(signal_result, equity)
        accepted = await self.telegram.request_trade_confirmation(signal_result, plan)
        if not accepted:
            self.trade_logger.log("rejected", {**signal_result.__dict__, "status": "rejected"})
            return

        order = await self.executor.execute(plan)
        await self.telegram.send_trade_report(
            f"Entry submitted for {plan.symbol} {plan.side} amount={plan.amount}. Order ID: {order.get('id', 'n/a')}"
        )
        exit_result = await self.executor.monitor_position(plan)
        await self.telegram.send_trade_report(
            f"Position closed for {plan.symbol}: {exit_result['reason']} at {exit_result['price']:.8f}"
        )


async def main() -> None:
    """Program entrypoint."""
    bot = TradingBot()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, bot.stop)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())

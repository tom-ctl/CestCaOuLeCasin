"""Trading bot orchestrator."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from datetime import UTC, datetime

from config import get_settings
from exchange import BinanceClient
from execution import TradeExecutor
from position_manager import PositionManager
from risk_management import RiskManager
from state import bot_state
from strategy import SignalEngine
from telegram_bot import TelegramTradeBot
from utils.logging import configure_logging
from utils.logger import get_logger
from utils.trade_logger import TradeLogger


class TradingBot:
    """Coordinate exchange data, strategy signals, confirmations, and execution."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.settings.validate()
        configure_logging(self.settings.runtime_log_level)
        self.logger = get_logger("main")
        self.exchange = BinanceClient(self.settings)
        self.signal_engine = SignalEngine(self.settings)
        self.risk_manager = RiskManager(self.settings)
        self.telegram = TelegramTradeBot(self.settings)
        self.trade_logger = TradeLogger(self.settings.trade_log_path)
        self.position_manager = PositionManager(
            self.exchange,
            self.trade_logger,
            self.settings.test_mode,
            self.settings.sleep_stop_loss_pct,
            self.settings.sleep_take_profit_pct,
        )
        self.executor = TradeExecutor(
            self.exchange,
            self.trade_logger,
            self.position_manager,
        )
        self.telegram.set_sleep_handler(self.activate_sleep_mode)
        self._stop = asyncio.Event()
        self._sleep_exit_task: asyncio.Task[None] | None = None
        self._loop_count = 0

    async def run(self) -> None:
        """Run the main trading loop."""
        await self.exchange.load_markets()
        await self.telegram.start()
        self.logger.info(
            "Trading bot started | test_mode=%s | sandbox=%s | poll_seconds=%s | sleep_exit_seconds=%s",
            self.settings.test_mode,
            self.settings.binance_test_mode,
            self.settings.runtime_poll_interval_seconds,
            self.settings.runtime_sleep_exit_delay_seconds,
        )
        try:
            while not self._stop.is_set():
                await self._scan_once()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.settings.runtime_poll_interval_seconds,
                    )
        finally:
            if self._sleep_exit_task is not None:
                self._sleep_exit_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._sleep_exit_task
            await self.telegram.stop()
            await self.exchange.close()

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._stop.set()

    async def _scan_once(self) -> None:
        self._loop_count += 1
        positions = await self.position_manager.get_open_positions()
        self.logger.info(
            "Loop %s | time=%s | positions=%s | sleep=%s | mode=%s",
            self._loop_count,
            datetime.now(UTC).isoformat(),
            len(positions),
            bot_state["sleep_mode"],
            "sleep" if bot_state["sleep_mode"] else "normal",
        )
        if self.settings.test_mode:
            self.logger.debug("Loop position snapshot: %s", positions)

        exits = await self.position_manager.manage_positions()
        for exit_result in exits:
            await self.telegram.send_trade_report(
                f"Position closed for {exit_result['symbol']}: "
                f"{exit_result['reason']} at {exit_result['price']:.8f}"
            )

        if bot_state["sleep_mode"]:
            self.logger.info("Sleep mode active: skipping new trade generation")
            return

        for symbol in self.settings.trading_symbols:
            try:
                await self._process_symbol(symbol)
            except Exception as exc:  # noqa: BLE001 - keep bot alive on symbol-level failures.
                self.logger.exception("Error while processing %s: %s", symbol, exc)
                await self.telegram.send_trade_report(f"Error while processing {symbol}: {exc}")

    async def _process_symbol(self, symbol: str) -> None:
        if not self.exchange.is_valid_symbol(symbol):
            self.logger.warning("Invalid symbol skipped: %s", symbol)
            return
        candle_limit = max(self.settings.breakout_lookback, self.settings.volume_lookback) + 5
        candles = await self.exchange.get_ohlcv(symbol, self.settings.timeframe, candle_limit)
        signal_result = self.signal_engine.analyze(symbol, candles)
        if signal_result is None:
            self.logger.debug("No signal for %s", symbol)
            return
        if signal_result.confidence < self.settings.min_confidence:
            self.logger.warning(
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
            self.logger.warning("Skipped trade %s %s: user rejected or timeout", signal_result.symbol, signal_result.action)
            self.trade_logger.log("rejected", {**signal_result.__dict__, "status": "rejected"})
            return
        if bot_state["sleep_mode"]:
            self.logger.warning("Skipped trade %s: sleep mode activated before execution", symbol)
            self.trade_logger.log("rejected", {**signal_result.__dict__, "status": "sleep_mode"})
            await self.telegram.send_trade_report(f"Trade skipped for {symbol}: sleep mode is active.")
            return

        order = await self.executor.execute(plan)
        if order.get("status") != "closed":
            await self.telegram.send_trade_report(
                f"Entry order not filled for {plan.symbol}. Status: {order.get('status', 'unknown')}"
            )
            return
        await self.telegram.send_trade_report(
            f"Entry submitted for {plan.symbol} {plan.side} amount={plan.amount}. Order ID: {order.get('id', 'n/a')}"
        )

    async def activate_sleep_mode(self) -> None:
        """Activate sleep mode, tighten positions, and schedule full exit."""
        if bot_state["sleep_mode"]:
            await self.telegram.send_trade_report("Sleep mode is already active.")
            return

        bot_state["sleep_mode"] = True
        self.logger.warning("Sleep mode activated - no new trades")
        self.trade_logger.log("sleep_mode", {"status": "active", "details": "telegram_command"})
        if self.settings.test_mode:
            await self.telegram.send_trade_report(
                "🧪 TEST MODE ACTIVATED (10 min)\n"
                "- No new trades\n"
                "- Tight SL/TP\n"
                "- Full exit in 10 minutes"
            )
        else:
            await self.telegram.send_trade_report(
                "🛑 Sleep mode activated\n"
                "- No new trades\n"
                "- Positions tightening\n"
                "- Full exit in 2 hours"
            )
        tightened = await self.position_manager.tighten_positions()
        self.logger.info("Tightened %s open positions for sleep mode", len(tightened))
        if self._sleep_exit_task is None or self._sleep_exit_task.done():
            self.logger.warning("Sleep mode timer started seconds=%s", self.settings.runtime_sleep_exit_delay_seconds)
            self._sleep_exit_task = asyncio.create_task(self._sleep_countdown_and_exit())

    async def _sleep_countdown_and_exit(self) -> None:
        await asyncio.sleep(self.settings.runtime_sleep_exit_delay_seconds)
        self.logger.warning("Sleep mode countdown elapsed; final liquidation starting")
        try:
            await self.position_manager.close_all_positions()
            positions = await self.position_manager.get_positions()
            unresolved_positions = [position for position in positions if position.status != "closed"]
            balance = await self.exchange.get_balance()
            usdt_balance = balance.get("USDT", {})
            self.logger.info("FINAL USDT BALANCE: %s", usdt_balance)
            if unresolved_positions:
                self.logger.error("Sleep mode exit incomplete; unresolved positions remain: %s", unresolved_positions)
                self.trade_logger.log("sleep_exit", {"status": "manual_review", "details": "unresolved_positions_remain"})
                await self.telegram.send_trade_report("Sleep mode exit requires manual review: unresolved positions remain.")
                return
            self.trade_logger.log("sleep_exit", {"status": "closed", "details": "countdown_elapsed"})
            self.logger.info("Sleep mode final liquidation completed")
            if self.settings.test_mode:
                await self.telegram.send_trade_report("✅ Test complete: all positions closed")
            else:
                await self.telegram.send_trade_report("✅ All positions closed\nBot can be safely stopped")
        except Exception as exc:  # noqa: BLE001 - report final exit failures to user.
            self.logger.exception("Sleep mode final exit failed: %s", exc)
            self.trade_logger.log("sleep_exit", {"status": "failed", "details": str(exc)})
            await self.telegram.send_trade_report(f"Sleep mode exit failed: {exc}")


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

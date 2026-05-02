"""Telegram interface for signal confirmation and trade reporting."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import Settings
from risk_management.risk_manager import PositionPlan
from strategy import MarketSignal
from utils.logger import get_logger


class TelegramTradeBot:
    """Telegram bot wrapper with async confirmation futures."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("telegram")
        self.application = Application.builder().token(settings.telegram_bot_token).build()
        self.application.add_handler(CallbackQueryHandler(self._handle_callback))
        self.application.add_handler(CommandHandler("sleep", self._handle_sleep_command))
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._sleep_handler: Callable[[], Awaitable[None]] | None = None

    def set_sleep_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        """Register the application sleep-mode handler."""
        self._sleep_handler = handler

    async def start(self) -> None:
        """Start Telegram polling."""
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await self.application.updater.start_polling()
        self.logger.info("Telegram polling started chat_id=%s", self.settings.telegram_chat_id)

    async def stop(self) -> None:
        """Stop Telegram polling."""
        if self.application.updater is not None:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        self.logger.info("Telegram polling stopped")

    async def request_trade_confirmation(self, signal: MarketSignal, plan: PositionPlan) -> bool:
        """Send a signal to the configured chat and wait for ACCEPT or REJECT."""
        signal_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[signal_id] = future

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("ACCEPT", callback_data=f"accept:{signal_id}"),
                    InlineKeyboardButton("REJECT", callback_data=f"reject:{signal_id}"),
                ]
            ]
        )
        await self.application.bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=self._format_signal(signal, plan),
            reply_markup=keyboard,
        )
        self.logger.info("Signal sent to Telegram %s %s confidence=%.2f", signal.symbol, signal.action, signal.confidence)
        try:
            accepted = await asyncio.wait_for(future, timeout=self.settings.confirmation_timeout_seconds)
            self.logger.info("Telegram trade response %s %s accepted=%s", signal.symbol, signal.action, accepted)
            return accepted
        except asyncio.TimeoutError:
            self._pending.pop(signal_id, None)
            await self.application.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=f"Signal expired for {signal.symbol} {signal.action}.",
            )
            self.logger.warning("Telegram confirmation timeout %s %s", signal.symbol, signal.action)
            return False

    async def send_trade_report(self, text: str) -> None:
        """Send a trade status report to Telegram."""
        try:
            await self.application.bot.send_message(chat_id=self.settings.telegram_chat_id, text=text)
            self.logger.info("Telegram message sent: %s", text.replace("\n", " | "))
        except Exception as exc:
            self.logger.error("Telegram message failed: %s", exc)
            raise

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()
        self.logger.debug("Telegram callback received data=%s", query.data)

        if self.settings.telegram_chat_id is not None and query.message:
            if query.message.chat_id != self.settings.telegram_chat_id:
                await query.edit_message_text("Unauthorized chat.")
                self.logger.warning("Unauthorized Telegram callback chat_id=%s", query.message.chat_id)
                return

        action, _, signal_id = query.data.partition(":")
        future = self._pending.pop(signal_id, None)
        if future is None or future.done():
            await query.edit_message_text("Signal expired or already handled.")
            self.logger.warning("Telegram callback ignored signal_id=%s action=%s", signal_id, action)
            return

        accepted = action == "accept"
        future.set_result(accepted)
        await query.edit_message_text("Trade accepted." if accepted else "Trade rejected.")
        self.logger.info("Telegram callback handled signal_id=%s accepted=%s", signal_id, accepted)

    async def _handle_sleep_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        if update.effective_chat is None:
            return
        self.logger.warning("Telegram command received /sleep chat_id=%s", update.effective_chat.id)
        if self.settings.telegram_chat_id is not None and update.effective_chat.id != self.settings.telegram_chat_id:
            await update.effective_chat.send_message("Unauthorized chat.")
            self.logger.warning("Unauthorized /sleep command chat_id=%s", update.effective_chat.id)
            return
        if self._sleep_handler is None:
            await update.effective_chat.send_message("Sleep handler is not configured.")
            return
        await self._sleep_handler()

    @staticmethod
    def _format_signal(signal: MarketSignal, plan: PositionPlan) -> str:
        return (
            f"Signal: {signal.symbol}\n"
            f"Action: {signal.action}\n"
            f"Confidence: {signal.confidence}/10\n"
            f"Entry: {signal.entry_price:.8f}\n"
            f"SL: {signal.stop_loss:.8f}\n"
            f"TP: {signal.take_profit:.8f}\n"
            f"Amount: {plan.amount}\n"
            f"Risk: {plan.risk_amount} USDT\n"
            f"Reason: {signal.reason}"
        )

"""Telegram interface for signal confirmation and trade reporting."""

from __future__ import annotations

import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from config import Settings
from risk_management.risk_manager import PositionPlan
from strategy import MarketSignal


class TelegramTradeBot:
    """Telegram bot wrapper with async confirmation futures."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(__name__)
        self.application = Application.builder().token(settings.telegram_bot_token).build()
        self.application.add_handler(CallbackQueryHandler(self._handle_callback))
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def start(self) -> None:
        """Start Telegram polling."""
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await self.application.updater.start_polling()

    async def stop(self) -> None:
        """Stop Telegram polling."""
        if self.application.updater is not None:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

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
        try:
            return await asyncio.wait_for(future, timeout=self.settings.confirmation_timeout_seconds)
        except asyncio.TimeoutError:
            self._pending.pop(signal_id, None)
            await self.application.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=f"Signal expired for {signal.symbol} {signal.action}.",
            )
            return False

    async def send_trade_report(self, text: str) -> None:
        """Send a trade status report to Telegram."""
        await self.application.bot.send_message(chat_id=self.settings.telegram_chat_id, text=text)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        query = update.callback_query
        if query is None or query.data is None:
            return
        await query.answer()

        if self.settings.telegram_chat_id is not None and query.message:
            if query.message.chat_id != self.settings.telegram_chat_id:
                await query.edit_message_text("Unauthorized chat.")
                return

        action, _, signal_id = query.data.partition(":")
        future = self._pending.pop(signal_id, None)
        if future is None or future.done():
            await query.edit_message_text("Signal expired or already handled.")
            return

        accepted = action == "accept"
        future.set_result(accepted)
        await query.edit_message_text("Trade accepted." if accepted else "Trade rejected.")

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

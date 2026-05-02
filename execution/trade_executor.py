"""Trade execution and position monitoring."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from exchange import BinanceClient
from risk_management.risk_manager import PositionPlan
from utils.trade_logger import TradeLogger


class TradeExecutor:
    """Execute confirmed trades and monitor exits."""

    def __init__(
        self,
        exchange: BinanceClient,
        trade_logger: TradeLogger,
        poll_seconds: int,
    ) -> None:
        self.exchange = exchange
        self.trade_logger = trade_logger
        self.poll_seconds = poll_seconds
        self.logger = logging.getLogger(__name__)

    async def execute(self, plan: PositionPlan) -> dict:
        """Place the entry order."""
        order = await self.exchange.create_order(plan.symbol, plan.side, plan.amount)
        self.trade_logger.log(
            "entry",
            {
                **asdict(plan),
                "status": order.get("status", "submitted"),
                "details": order.get("id", ""),
            },
        )
        return order

    async def monitor_position(self, plan: PositionPlan) -> dict:
        """Poll market price and close when stop loss or take profit is reached."""
        self.logger.info("Monitoring %s %s position", plan.symbol, plan.side)
        while True:
            price = await self.exchange.get_price(plan.symbol)
            should_close, reason = self._exit_reason(plan.side, price, plan.stop_loss, plan.take_profit)
            if should_close:
                order = await self.exchange.close_position(plan.symbol, plan.side, plan.amount)
                self.trade_logger.log(
                    "exit",
                    {
                        **asdict(plan),
                        "price": price,
                        "status": order.get("status", "submitted"),
                        "details": reason,
                    },
                )
                return {"order": order, "reason": reason, "price": price}
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    def _exit_reason(side: str, price: float, stop_loss: float, take_profit: float) -> tuple[bool, str]:
        side_upper = side.upper()
        if side_upper == "BUY":
            if price <= stop_loss:
                return True, "stop_loss"
            if price >= take_profit:
                return True, "take_profit"
        else:
            if price >= stop_loss:
                return True, "stop_loss"
            if price <= take_profit:
                return True, "take_profit"
        return False, ""

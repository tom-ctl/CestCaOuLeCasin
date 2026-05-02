"""Trade execution and position monitoring."""

from __future__ import annotations

import logging
from dataclasses import asdict

from exchange import BinanceClient
from position_manager import PositionManager
from risk_management.risk_manager import PositionPlan
from utils.trade_logger import TradeLogger


class TradeExecutor:
    """Execute confirmed trades and monitor exits."""

    def __init__(
        self,
        exchange: BinanceClient,
        trade_logger: TradeLogger,
        position_manager: PositionManager,
    ) -> None:
        self.exchange = exchange
        self.trade_logger = trade_logger
        self.position_manager = position_manager
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
        await self.position_manager.add_position(plan)
        return order

"""Trade execution and position monitoring."""

from __future__ import annotations

from dataclasses import asdict

from exchange import BinanceClient
from position_manager import PositionManager
from risk_management.risk_manager import PositionPlan
from utils.logger import get_logger
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
        self.logger = get_logger("execution")

    async def execute(self, plan: PositionPlan) -> dict:
        """Place the entry order."""
        self.logger.info("Execution started %s %s amount=%s", plan.symbol, plan.side, plan.amount)
        try:
            order = await self.exchange.create_order(plan.symbol, plan.side, plan.amount)
        except Exception as exc:
            self.logger.error("Execution failed %s %s amount=%s: %s", plan.symbol, plan.side, plan.amount, exc)
            raise
        self.trade_logger.log(
            "entry",
            {
                **asdict(plan),
                "status": order.get("status", "submitted"),
                "details": order.get("id", ""),
            },
        )
        if order.get("status") != "closed":
            self.logger.warning("Order not filled: %s entry status=%s", plan.symbol, order.get("status"))
            return order
        await self.position_manager.add_position(plan)
        self.logger.info("Trade executed %s %s amount=%s order_id=%s", plan.symbol, plan.side, plan.amount, order.get("id", "n/a"))
        return order

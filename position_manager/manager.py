"""In-memory position tracking and safe exit management."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from exchange import BinanceClient
from risk_management.risk_manager import PositionPlan
from utils.logger import get_logger
from utils.trade_logger import TradeLogger


@dataclass
class OpenPosition:
    """Open spot position tracked by the bot."""

    symbol: str
    side: str
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    status: str = "open"


class PositionManager:
    """Track open positions and coordinate logic-based exits."""

    def __init__(
        self,
        exchange: BinanceClient,
        trade_logger: TradeLogger,
        test_mode: bool,
        sleep_stop_loss_pct: float,
        sleep_take_profit_pct: float,
    ) -> None:
        self.exchange = exchange
        self.trade_logger = trade_logger
        self.test_mode = test_mode
        self.sleep_stop_loss_pct = sleep_stop_loss_pct
        self.sleep_take_profit_pct = sleep_take_profit_pct
        self.logger = get_logger("positions")
        self._positions: list[OpenPosition] = []
        self._lock = asyncio.Lock()

    async def add_position(
        self,
        plan: PositionPlan | None = None,
        *,
        symbol: str | None = None,
        amount: float | None = None,
        entry_price: float | None = None,
        side: str = "BUY",
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> OpenPosition:
        """Add or replace a tracked position."""
        if plan is not None:
            symbol = plan.symbol
            side = plan.side
            entry_price = plan.entry_price
            amount = plan.amount
            stop_loss = plan.stop_loss
            take_profit = plan.take_profit
        if symbol is None or amount is None or entry_price is None:
            raise ValueError("symbol, amount, and entry_price are required")
        position = OpenPosition(
            symbol=symbol,
            side=side.upper(),
            entry_price=entry_price,
            amount=amount,
            stop_loss=stop_loss if stop_loss is not None else entry_price * 0.98,
            take_profit=take_profit if take_profit is not None else entry_price * 1.04,
            status="open",
        )
        async with self._lock:
            self._positions = [item for item in self._positions if item.symbol != position.symbol]
            self._positions.append(position)
        self.logger.info(
            "Position added %s %s entry=%s amount=%s sl=%s tp=%s",
            position.symbol,
            position.side,
            position.entry_price,
            position.amount,
            position.stop_loss,
            position.take_profit,
        )
        return position

    async def update_position(self, symbol: str, **updates: float | str) -> OpenPosition | None:
        """Update a tracked position by symbol."""
        async with self._lock:
            position = next((item for item in self._positions if item.symbol == symbol), None)
            if position is None:
                return None
            for key, value in updates.items():
                if hasattr(position, key):
                    setattr(position, key, value)
            self.logger.info("Position updated %s: %s", symbol, position)
            return position

    async def update_position_status(self, symbol: str, status: str) -> OpenPosition | None:
        """Update the lifecycle status for a tracked position."""
        return await self.update_position(symbol, status=status)

    async def get_open_positions(self) -> list[OpenPosition]:
        """Return a snapshot of open positions."""
        async with self._lock:
            return [position for position in self._positions if position.status == "open"]

    async def get_positions(self) -> list[OpenPosition]:
        """Return all tracked positions, including closed/manual-review entries."""
        async with self._lock:
            return list(self._positions)

    async def close_position(self, symbol: str) -> dict[str, Any] | None:
        """Close and untrack one position with the opposite market order."""
        async with self._lock:
            position = next((item for item in self._positions if item.symbol == symbol and item.status == "open"), None)
        if position is None:
            self.logger.warning("Close skipped: no tracked position for %s", symbol)
            return None
        if not self.exchange.is_valid_symbol(position.symbol):
            self.logger.warning("Invalid symbol skipped: %s", position.symbol)
            await self.update_position_status(position.symbol, "manual_review")
            return None

        self.logger.info("Closing position %s %s amount=%s", position.symbol, position.side, position.amount)
        try:
            order = await self._retry_order(
                position.symbol,
                "SELL" if position.side == "BUY" else "BUY",
                position.amount,
                attempts=2,
            )
        except Exception as exc:
            self.logger.error("Close failed after retries %s: %s", symbol, exc)
            await self.update_position_status(symbol, "manual_review")
            return None
        order_status = order.get("status")
        if order_status != "closed":
            self.logger.warning("Order not filled: %s close status=%s", symbol, order_status)
            if order_status == "expired":
                self.logger.error("Close order expired for %s; marking manual_review", symbol)
                await self.update_position_status(symbol, "manual_review")
            return order
        await self.update_position_status(symbol, "closed")
        self.trade_logger.log(
            "exit",
            {
                **asdict(position),
                "status": order.get("status", "submitted"),
                "details": order.get("id", ""),
            },
        )
        self.logger.info("Position closed %s order_id=%s", symbol, order.get("id", "n/a"))
        return order

    async def close_all_positions(self) -> list[dict[str, Any]]:
        """Close only positions opened and tracked by this bot."""
        self.logger.warning("Tracked-position liquidation started")
        results: list[dict[str, Any]] = []
        for position in await self.get_open_positions():
            try:
                order = await self.close_position(position.symbol)
                if order is not None:
                    results.append(order)
            except Exception as exc:  # noqa: BLE001 - continue closing other positions.
                self.logger.exception("Failed to close tracked position %s: %s", position.symbol, exc)

        self.logger.warning("Tracked-position liquidation finished orders=%s", len(results))
        return results

    async def tighten_positions(self) -> list[OpenPosition]:
        """Move SL/TP closer to current price for all tracked positions."""
        tightened: list[OpenPosition] = []
        for position in await self.get_open_positions():
            try:
                price = await self.exchange.get_price(position.symbol)
                stop_loss, take_profit = self._tightened_exits(position, price)
                self.logger.warning(
                    "Sleep tightening %s price=%s old_sl=%s old_tp=%s new_sl=%s new_tp=%s",
                    position.symbol,
                    price,
                    position.stop_loss,
                    position.take_profit,
                    stop_loss,
                    take_profit,
                )
                updated = await self.update_position(
                    position.symbol,
                    stop_loss=round(stop_loss, 8),
                    take_profit=round(take_profit, 8),
                )
                if updated is not None:
                    tightened.append(updated)
                    self.trade_logger.log(
                        "sleep_tighten",
                        {**asdict(updated), "price": price, "details": "sleep_mode"},
                    )
            except Exception as exc:  # noqa: BLE001 - continue tightening other positions.
                self.logger.exception("Failed to tighten %s: %s", position.symbol, exc)
        return tightened

    async def manage_positions(self) -> list[dict[str, Any]]:
        """Compatibility wrapper for loop-based position management."""
        return await self.monitor_open_positions()

    async def monitor_open_positions(self) -> list[dict[str, Any]]:
        """Check tracked positions and close any that hit logic-based SL/TP."""
        exits: list[dict[str, Any]] = []
        for position in await self.get_open_positions():
            try:
                price = await self.exchange.get_price(position.symbol)
                self.logger.debug(
                    "Position status %s side=%s price=%s entry=%s amount=%s sl=%s tp=%s",
                    position.symbol,
                    position.side,
                    price,
                    position.entry_price,
                    position.amount,
                    position.stop_loss,
                    position.take_profit,
                )
                should_close, reason = self._exit_reason(
                    position.side,
                    price,
                    position.stop_loss,
                    position.take_profit,
                )
                if not should_close:
                    continue
                self.logger.info("SL/TP triggered %s reason=%s price=%s", position.symbol, reason, price)
                order = await self.close_position(position.symbol)
                if order is None or order.get("status") != "closed":
                    self.logger.warning(
                        "Position close not confirmed %s reason=%s order_status=%s",
                        position.symbol,
                        reason,
                        order.get("status") if order else None,
                    )
                    continue
                exits.append({"order": order, "reason": reason, "price": price, "symbol": position.symbol})
                self.trade_logger.log(
                    "logic_exit",
                    {
                        **asdict(position),
                        "price": price,
                        "status": order.get("status", "submitted") if order else "",
                        "details": reason,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - keep monitoring other positions.
                self.logger.exception("Failed while monitoring %s: %s", position.symbol, exc)
        return exits

    async def _retry_order(self, symbol: str, side: str, amount: float, attempts: int = 2) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self.logger.info("Order attempt %s/%s %s %s amount=%s", attempt, attempts, symbol, side, amount)
                order = await self.exchange.create_order(symbol, side, amount)
                status = order.get("status")
                if status == "expired":
                    self.logger.error("Order expired %s %s amount=%s", symbol, side, amount)
                    last_error = RuntimeError(f"Order expired for {symbol}")
                    if attempt >= attempts:
                        return order
                    continue
                if status != "closed":
                    self.logger.warning("Order not filled: %s status=%s", symbol, status)
                    return order
                return order
            except Exception as exc:  # noqa: BLE001 - exchange libraries raise broad errors.
                last_error = exc
                if "does not have market symbol" in str(exc):
                    self.logger.warning("Invalid symbol skipped without retry: %s", symbol)
                    break
                self.logger.error(
                    "Order attempt %s/%s failed for %s %s amount=%s: %s",
                    attempt,
                    attempts,
                    side,
                    symbol,
                    amount,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(attempt)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _exit_reason(side: str, price: float, stop_loss: float, take_profit: float) -> tuple[bool, str]:
        if side.upper() == "BUY":
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

    def _tightened_exits(self, position: OpenPosition, current_price: float) -> tuple[float, float]:
        if self.test_mode:
            if position.side == "BUY":
                return position.entry_price * 0.999, position.entry_price * 1.005
            return position.entry_price * 1.001, position.entry_price * 0.995

        if position.side == "BUY":
            return current_price * (1 - self.sleep_stop_loss_pct), current_price * (1 + self.sleep_take_profit_pct)
        return current_price * (1 + self.sleep_stop_loss_pct), current_price * (1 - self.sleep_take_profit_pct)

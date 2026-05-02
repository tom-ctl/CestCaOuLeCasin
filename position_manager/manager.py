"""In-memory position tracking and safe exit management."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any

from exchange import BinanceClient
from risk_management.risk_manager import PositionPlan
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
        self.logger = logging.getLogger(__name__)
        self._positions: list[OpenPosition] = []
        self._lock = asyncio.Lock()

    async def add_position(self, plan: PositionPlan) -> OpenPosition:
        """Add or replace a tracked position."""
        position = OpenPosition(
            symbol=plan.symbol,
            side=plan.side.upper(),
            entry_price=plan.entry_price,
            amount=plan.amount,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
        )
        async with self._lock:
            self._positions = [item for item in self._positions if item.symbol != position.symbol]
            self._positions.append(position)
        self.logger.info("Tracking position: %s", position)
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
            self.logger.info("Updated position %s: %s", symbol, position)
            return position

    async def get_open_positions(self) -> list[OpenPosition]:
        """Return a snapshot of open positions."""
        async with self._lock:
            return list(self._positions)

    async def close_position(self, symbol: str) -> dict[str, Any] | None:
        """Close and untrack one position with the opposite market order."""
        async with self._lock:
            position = next((item for item in self._positions if item.symbol == symbol), None)
        if position is None:
            return None

        order = await self._retry_order(
            position.symbol,
            "SELL" if position.side == "BUY" else "BUY",
            position.amount,
        )
        async with self._lock:
            self._positions = [item for item in self._positions if item.symbol != symbol]
        self.trade_logger.log(
            "exit",
            {
                **asdict(position),
                "status": order.get("status", "submitted"),
                "details": order.get("id", ""),
            },
        )
        return order

    async def close_all_positions(self) -> list[dict[str, Any]]:
        """Close tracked positions and sell all non-USDT spot balances."""
        results: list[dict[str, Any]] = []
        for position in await self.get_open_positions():
            try:
                order = await self.close_position(position.symbol)
                if order is not None:
                    results.append(order)
            except Exception as exc:  # noqa: BLE001 - continue closing other positions.
                self.logger.exception("Failed to close tracked position %s: %s", position.symbol, exc)

        balance = await self.exchange.get_balance()
        total_balances = balance.get("total", {})
        for asset, raw_amount in total_balances.items():
            if asset == "USDT":
                continue
            amount = float(raw_amount or 0)
            if amount <= 0:
                continue
            symbol = f"{asset}/USDT"
            try:
                order = await self._retry_order(symbol, "SELL", amount)
                results.append(order)
                self.trade_logger.log(
                    "balance_exit",
                    {
                        "symbol": symbol,
                        "side": "SELL",
                        "amount": amount,
                        "status": order.get("status", "submitted"),
                        "details": order.get("id", ""),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - pair may not exist or amount may be too small.
                self.logger.warning("Could not convert %s balance to USDT: %s", asset, exc)
        return results

    async def tighten_positions(self) -> list[OpenPosition]:
        """Move SL/TP closer to current price for all tracked positions."""
        tightened: list[OpenPosition] = []
        for position in await self.get_open_positions():
            try:
                price = await self.exchange.get_price(position.symbol)
                stop_loss, take_profit = self._tightened_exits(position, price)
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
                should_close, reason = self._exit_reason(
                    position.side,
                    price,
                    position.stop_loss,
                    position.take_profit,
                )
                if not should_close:
                    continue
                order = await self.close_position(position.symbol)
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

    async def _retry_order(self, symbol: str, side: str, amount: float, attempts: int = 3) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self.exchange.create_order(symbol, side, amount)
            except Exception as exc:  # noqa: BLE001 - exchange libraries raise broad errors.
                last_error = exc
                self.logger.warning(
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

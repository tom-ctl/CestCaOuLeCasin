"""Risk management and position sizing."""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from strategy import MarketSignal
from utils.logger import get_logger


@dataclass(frozen=True)
class PositionPlan:
    """Order plan generated from a signal and account equity."""

    symbol: str
    side: str
    amount: float
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    risk_amount: float


class RiskManager:
    """Calculate position sizes using fixed fractional risk."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("risk")

    def estimate_usdt_equity(self, balance: dict) -> float:
        """Estimate available USDT equity from a CCXT balance response."""
        if self.settings.account_equity_override is not None:
            self.logger.debug("Using account equity override: %s", self.settings.account_equity_override)
            return self.settings.account_equity_override
        usdt = balance.get("USDT", {})
        free = usdt.get("free")
        total = usdt.get("total")
        equity = float(free if free is not None else total or 0.0)
        self.logger.debug("Estimated USDT equity free=%s total=%s equity=%s", free, total, equity)
        return equity

    def build_position_plan(self, signal: MarketSignal, usdt_equity: float) -> PositionPlan:
        """Build a position plan capped by risk and max account allocation."""
        if usdt_equity <= 0:
            raise ValueError("USDT equity must be greater than zero")

        stop_distance = abs(signal.entry_price - signal.stop_loss)
        if stop_distance <= 0:
            raise ValueError("Invalid stop loss distance")

        risk_amount = usdt_equity * self.settings.risk_per_trade
        risk_based_amount = risk_amount / stop_distance
        max_notional = usdt_equity * self.settings.max_position_pct
        allocation_based_amount = max_notional / signal.entry_price
        amount = min(risk_based_amount, allocation_based_amount)
        if self.settings.test_mode:
            amount = min(amount, self.settings.test_trade_amount)
            self.logger.debug("Test mode amount cap applied: %s", amount)

        if amount <= 0:
            self.logger.warning("Skipped trade: calculated position amount <= 0")
            raise ValueError("Calculated position amount must be greater than zero")

        self.logger.info(
            "Position plan %s %s amount=%s entry=%s sl=%s tp=%s risk=%s",
            signal.symbol,
            signal.action,
            round(amount, 8),
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit,
            round(risk_amount, 2),
        )

        return PositionPlan(
            symbol=signal.symbol,
            side=signal.action,
            amount=round(amount, 8),
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            risk_amount=round(risk_amount, 2),
        )

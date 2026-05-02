"""Virtual preprod wallet for simulated trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from utils.logger import get_logger


@dataclass
class VirtualPosition:
    """Open simulated position."""

    symbol: str
    entry_price: float
    current_price: float
    size: float
    pnl: float
    timestamp: datetime

    @property
    def pnl_pct(self) -> float:
        """Return unrealized PnL as a percentage of entry notional."""
        notional = self.entry_price * self.size
        if notional <= 0:
            return 0.0
        return (self.pnl / notional) * 100


@dataclass
class TradeRecord:
    """Closed simulated trade."""

    symbol: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    opened_at: datetime
    closed_at: datetime

    @property
    def won(self) -> bool:
        """Return whether the trade closed profitably."""
        return self.pnl > 0


@dataclass
class Wallet:
    """Virtual wallet tracking cash, open positions, and trade history."""

    initial_balance: float = 10000.0
    current_balance: float = field(init=False)
    open_positions: list[VirtualPosition] = field(default_factory=list)
    trade_history: list[TradeRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.current_balance = self.initial_balance
        self.logger = get_logger("wallet")
        self.logger.info("Virtual wallet initialized balance=%.2f USDT", self.initial_balance)

    def open_position(self, symbol: str, entry_price: float, size: float) -> VirtualPosition:
        """Open a simulated long position."""
        position = VirtualPosition(
            symbol=symbol,
            entry_price=entry_price,
            current_price=entry_price,
            size=size,
            pnl=0.0,
            timestamp=datetime.now(UTC),
        )
        self.open_positions.append(position)
        self.logger.info("Simulated trade opened %s entry=%.8f size=%.8f", symbol, entry_price, size)
        return position

    def update_position_price(self, position: VirtualPosition, current_price: float) -> None:
        """Update current price and unrealized PnL for one position."""
        position.current_price = current_price
        position.pnl = (current_price - position.entry_price) * position.size
        self.logger.debug(
            "Position updated %s entry=%.8f current=%.8f size=%.8f pnl=%.4f",
            position.symbol,
            position.entry_price,
            position.current_price,
            position.size,
            position.pnl,
        )

    def close_position(self, position: VirtualPosition, exit_price: float) -> TradeRecord:
        """Close a simulated position and realize PnL into cash balance."""
        self.update_position_price(position, exit_price)
        self.current_balance += position.pnl
        self.open_positions.remove(position)
        trade = TradeRecord(
            symbol=position.symbol,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size=position.size,
            pnl=position.pnl,
            opened_at=position.timestamp,
            closed_at=datetime.now(UTC),
        )
        self.trade_history.append(trade)
        self.logger.info("Simulated trade closed %s exit=%.8f pnl=%.4f", trade.symbol, trade.exit_price, trade.pnl)
        return trade

    def close_all_positions(self) -> list[TradeRecord]:
        """Close every simulated open position at its current price."""
        closed: list[TradeRecord] = []
        for position in list(self.open_positions):
            closed.append(self.close_position(position, position.current_price))
        return closed

    @property
    def unrealized_pnl(self) -> float:
        """Return aggregate unrealized PnL."""
        return sum(position.pnl for position in self.open_positions)

    @property
    def realized_pnl(self) -> float:
        """Return aggregate realized PnL."""
        return sum(trade.pnl for trade in self.trade_history)

    @property
    def total_pnl(self) -> float:
        """Return realized plus unrealized PnL."""
        return self.realized_pnl + self.unrealized_pnl

    @property
    def total_equity(self) -> float:
        """Return cash balance plus unrealized PnL."""
        return self.current_balance + self.unrealized_pnl

    @property
    def pnl_pct(self) -> float:
        """Return total PnL as percentage of initial balance."""
        return (self.total_pnl / self.initial_balance) * 100 if self.initial_balance else 0.0

    @property
    def number_of_trades(self) -> int:
        """Return number of closed trades."""
        return len(self.trade_history)

    @property
    def win_rate(self) -> float:
        """Return closed-trade win rate percentage."""
        if not self.trade_history:
            return 0.0
        wins = sum(1 for trade in self.trade_history if trade.won)
        return (wins / len(self.trade_history)) * 100

    def format_wallet_message(self) -> str:
        """Return a clean Telegram wallet summary."""
        pnl_sign = "+" if self.total_pnl >= 0 else ""
        lines = [
            "\U0001f4b0 WALLET",
            f"Balance: {self.current_balance:.2f} USDT",
            f"Equity: {self.total_equity:.2f} USDT",
            f"PnL: {pnl_sign}{self.total_pnl:.2f} USDT ({pnl_sign}{self.pnl_pct:.2f}%)",
            "",
            "\U0001f4ca STATS",
            f"Trades: {self.number_of_trades}",
            f"Winrate: {self.win_rate:.2f}%",
            "",
            "\U0001f4c2 POSITIONS",
        ]
        if not self.open_positions:
            lines.append("No open positions")
        else:
            for position in self.open_positions:
                pos_sign = "+" if position.pnl >= 0 else ""
                lines.extend(
                    [
                        f"- {position.symbol.replace('/', '')} | size: {position.size:.8f}",
                        f"  entry: {position.entry_price:.8f}",
                        f"  current: {position.current_price:.8f}",
                        f"  pnl: {pos_sign}{position.pnl_pct:.2f}%",
                    ]
                )
        return "\n".join(lines)

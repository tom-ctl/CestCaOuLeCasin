"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_symbols(value: str | None) -> list[str]:
    if not value:
        return ["BTC/USDT"]
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


@dataclass(frozen=True)
class Settings:
    """Runtime settings for exchange, strategy, risk, and Telegram."""

    preprod_mode: bool
    test_mode: bool
    binance_api_key: str
    binance_api_secret: str
    binance_test_mode: bool
    telegram_bot_token: str
    telegram_chat_id: int | None
    trading_symbols: list[str]
    timeframe: str
    poll_interval_seconds: int
    confirmation_timeout_seconds: int
    sleep_exit_delay_seconds: int
    test_poll_interval_seconds: int
    test_sleep_exit_delay_seconds: int
    test_trade_amount: float
    test_force_signal: bool
    virtual_initial_balance: float
    preprod_loop_interval_seconds: int
    preprod_trade_notional: float
    preprod_max_positions: int
    risk_per_trade: float
    account_equity_override: float | None
    max_position_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    sleep_stop_loss_pct: float
    sleep_take_profit_pct: float
    min_confidence: float
    breakout_lookback: int
    volume_lookback: int
    volume_spike_multiplier: float
    trade_log_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from `.env` and process environment values."""
        load_dotenv()
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        preprod_mode = _as_bool(os.getenv("PREPROD_MODE"), True)
        test_mode = _as_bool(os.getenv("TEST_MODE"), False)

        return cls(
            preprod_mode=preprod_mode,
            test_mode=test_mode,
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
            binance_test_mode=_as_bool(os.getenv("BINANCE_TEST_MODE"), True),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=int(chat_id) if chat_id else None,
            trading_symbols=_as_symbols(os.getenv("TRADING_SYMBOLS")),
            timeframe=os.getenv("TIMEFRAME", "5m"),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
            confirmation_timeout_seconds=int(os.getenv("CONFIRMATION_TIMEOUT_SECONDS", "300")),
            sleep_exit_delay_seconds=int(os.getenv("SLEEP_EXIT_DELAY_SECONDS", "7200")),
            test_poll_interval_seconds=int(os.getenv("TEST_POLL_INTERVAL_SECONDS", "10")),
            test_sleep_exit_delay_seconds=int(os.getenv("TEST_SLEEP_EXIT_DELAY_SECONDS", "600")),
            test_trade_amount=float(os.getenv("TEST_TRADE_AMOUNT", "0.001")),
            test_force_signal=_as_bool(os.getenv("TEST_FORCE_SIGNAL"), True),
            virtual_initial_balance=float(os.getenv("VIRTUAL_INITIAL_BALANCE", "10000")),
            preprod_loop_interval_seconds=int(os.getenv("PREPROD_LOOP_INTERVAL_SECONDS", "5")),
            preprod_trade_notional=float(os.getenv("PREPROD_TRADE_NOTIONAL", "100")),
            preprod_max_positions=int(os.getenv("PREPROD_MAX_POSITIONS", "3")),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
            account_equity_override=(
                float(os.getenv("ACCOUNT_EQUITY_OVERRIDE", ""))
                if os.getenv("ACCOUNT_EQUITY_OVERRIDE", "").strip()
                else None
            ),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.25")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.02")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.04")),
            sleep_stop_loss_pct=float(os.getenv("SLEEP_STOP_LOSS_PCT", "0.005")),
            sleep_take_profit_pct=float(os.getenv("SLEEP_TAKE_PROFIT_PCT", "0.015")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "7.0")),
            breakout_lookback=int(os.getenv("BREAKOUT_LOOKBACK", "20")),
            volume_lookback=int(os.getenv("VOLUME_LOOKBACK", "20")),
            volume_spike_multiplier=float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "1.8")),
            trade_log_path=Path(os.getenv("TRADE_LOG_PATH", "logs/trades.csv")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def runtime_log_level(self) -> str:
        """Return the effective log level for the current runtime mode."""
        return "DEBUG" if self.preprod_mode or self.test_mode else self.log_level

    @property
    def runtime_poll_interval_seconds(self) -> int:
        """Return the effective loop interval for the current runtime mode."""
        return self.test_poll_interval_seconds if self.test_mode else self.poll_interval_seconds

    @property
    def runtime_sleep_exit_delay_seconds(self) -> int:
        """Return the effective sleep-mode countdown for the current runtime mode."""
        return self.test_sleep_exit_delay_seconds if self.test_mode else self.sleep_exit_delay_seconds

    def validate(self) -> None:
        """Validate settings required for a runnable bot."""
        missing = []
        if not self.preprod_mode and not self.binance_api_key:
            missing.append("BINANCE_API_KEY")
        if not self.preprod_mode and not self.binance_api_secret:
            missing.append("BINANCE_API_SECRET")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if self.telegram_chat_id is None:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {joined}")
        if self.test_mode and not self.binance_test_mode:
            raise ValueError("TEST_MODE requires BINANCE_TEST_MODE=true")
        if self.virtual_initial_balance <= 0:
            raise ValueError("VIRTUAL_INITIAL_BALANCE must be greater than zero")
        if self.preprod_loop_interval_seconds <= 0:
            raise ValueError("PREPROD_LOOP_INTERVAL_SECONDS must be greater than zero")
        if self.preprod_trade_notional <= 0:
            raise ValueError("PREPROD_TRADE_NOTIONAL must be greater than zero")
        if self.preprod_max_positions <= 0:
            raise ValueError("PREPROD_MAX_POSITIONS must be greater than zero")
        if not 0 < self.risk_per_trade <= 0.02:
            raise ValueError("RISK_PER_TRADE must be between 0 and 0.02")
        if not 0 < self.max_position_pct <= 1:
            raise ValueError("MAX_POSITION_PCT must be between 0 and 1")
        if self.account_equity_override is not None and self.account_equity_override <= 0:
            raise ValueError("ACCOUNT_EQUITY_OVERRIDE must be greater than zero when set")
        if self.sleep_exit_delay_seconds <= 0:
            raise ValueError("SLEEP_EXIT_DELAY_SECONDS must be greater than zero")
        if self.test_poll_interval_seconds <= 0:
            raise ValueError("TEST_POLL_INTERVAL_SECONDS must be greater than zero")
        if self.test_sleep_exit_delay_seconds <= 0:
            raise ValueError("TEST_SLEEP_EXIT_DELAY_SECONDS must be greater than zero")
        if self.test_trade_amount <= 0:
            raise ValueError("TEST_TRADE_AMOUNT must be greater than zero")
        if not 0 < self.sleep_stop_loss_pct < self.stop_loss_pct:
            raise ValueError("SLEEP_STOP_LOSS_PCT must be greater than 0 and tighter than STOP_LOSS_PCT")
        if not 0 < self.sleep_take_profit_pct < self.take_profit_pct:
            raise ValueError("SLEEP_TAKE_PROFIT_PCT must be greater than 0 and lower than TAKE_PROFIT_PCT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings.from_env()

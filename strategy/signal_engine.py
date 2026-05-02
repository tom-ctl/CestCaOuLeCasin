"""Breakout and volume spike signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import Settings
from utils.logger import get_logger


def generate_signal(df: pd.DataFrame) -> dict[str, Any]:
    """Generate very permissive EMA/RSI/momentum scalping signals."""
    required = {"open", "high", "low", "close", "volume"}
    if df is None or not required.issubset(df.columns) or len(df) < 20:
        return {"signal": None, "confidence": 0.0, "reason": "Not enough valid data"}

    data = df.loc[:, ["open", "high", "low", "close", "volume"]].astype(float)
    close = data["close"]

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = (100 - (100 / (1 + rs))).fillna(50)

    current_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    current_ema9 = float(ema9.iloc[-1])
    current_ema21 = float(ema21.iloc[-1])
    current_rsi = float(rsi.iloc[-1])
    if any(pd.isna(value) for value in (current_close, previous_close, current_ema9, current_ema21, current_rsi)):
        return {"signal": None, "confidence": 0.0, "reason": "Indicator contains NaN"}

    buy_conditions = {
        "EMA9 > EMA21": current_ema9 > current_ema21,
        "RSI > 50": current_rsi > 50,
        "close > previous close": current_close > previous_close,
    }
    sell_conditions = {
        "EMA9 < EMA21": current_ema9 < current_ema21,
        "RSI < 50": current_rsi < 50,
        "close < previous close": current_close < previous_close,
    }

    buy_confidence = (
        (0.3 if buy_conditions["EMA9 > EMA21"] else 0.0)
        + (0.3 if buy_conditions["RSI > 50"] else 0.0)
        + (0.4 if buy_conditions["close > previous close"] else 0.0)
    )
    sell_confidence = (
        (0.3 if sell_conditions["EMA9 < EMA21"] else 0.0)
        + (0.3 if sell_conditions["RSI < 50"] else 0.0)
        + (0.4 if sell_conditions["close < previous close"] else 0.0)
    )

    if buy_confidence <= 0 and sell_confidence <= 0:
        return {"signal": None, "confidence": 0.0, "reason": "No permissive condition matched"}

    if buy_confidence > sell_confidence:
        matched = [name for name, matched_condition in buy_conditions.items() if matched_condition]
        return {
            "signal": "BUY",
            "confidence": round(min(buy_confidence, 1.0), 3),
            "reason": ", ".join(matched),
        }

    if sell_confidence > buy_confidence:
        matched = [name for name, matched_condition in sell_conditions.items() if matched_condition]
        return {
            "signal": "SELL",
            "confidence": round(min(sell_confidence, 1.0), 3),
            "reason": ", ".join(matched),
        }

    if current_close >= previous_close:
        matched = [name for name, matched_condition in buy_conditions.items() if matched_condition]
        return {
            "signal": "BUY",
            "confidence": round(min(buy_confidence, 1.0), 3),
            "reason": ", ".join(matched) or "Tie resolved by non-negative close momentum",
        }

    return {
        "signal": "SELL",
        "confidence": round(min(sell_confidence, 1.0), 3),
        "reason": ", ".join(name for name, matched_condition in sell_conditions.items() if matched_condition)
        or "Tie resolved by negative close momentum",
    }


@dataclass(frozen=True)
class MarketSignal:
    """Trading signal emitted by the strategy layer."""

    symbol: str
    action: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str


class SignalEngine:
    """Generate scalping signals from short-term momentum and breakout conditions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("strategy")

    def analyze(self, symbol: str, candles: list[list[float]]) -> MarketSignal | None:
        """Return a scalping signal when permissive EMA, RSI, or momentum conditions align."""
        if len(candles) < 20:
            self.logger.warning("Skipping signal %s: insufficient candles count=%s required=20", symbol, len(candles))
            return None

        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        result = generate_signal(df)
        self.logger.debug("Scalping signal result %s: %s", symbol, result)
        action = result["signal"]
        latest_close = float(df["close"].iloc[-1])

        if action in {"BUY", "SELL"}:
            signal = self._build_signal(
                symbol,
                action,
                latest_close,
                float(result["confidence"]) * 10,
                str(result["reason"]),
            )
            self.logger.info(
                "Signal detected %s %s confidence=%.2f reason=%s",
                signal.symbol,
                signal.action,
                signal.confidence,
                signal.reason,
            )
            return signal

        self.logger.debug("No signal %s: %s", symbol, result["reason"])
        return None

    def sentiment_placeholder(self, symbol: str) -> float:
        """Future integration point for news sentiment score."""
        _ = symbol
        return 0.0

    def _build_signal(
        self,
        symbol: str,
        action: str,
        price: float,
        confidence: float,
        reason: str,
    ) -> MarketSignal:
        if action == "BUY":
            stop_loss = price * (1 - self.settings.stop_loss_pct)
            take_profit = price * (1 + self.settings.take_profit_pct)
        else:
            stop_loss = price * (1 + self.settings.stop_loss_pct)
            take_profit = price * (1 - self.settings.take_profit_pct)

        return MarketSignal(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 2),
            entry_price=price,
            stop_loss=round(stop_loss, 8),
            take_profit=round(take_profit, 8),
            reason=reason,
        )

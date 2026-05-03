"""Breakout and volume spike signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import Settings
from utils.logger import get_logger


def generate_signal(df: pd.DataFrame) -> dict[str, Any]:
    """Generate strict EMA/RSI/volatility scalping signals."""
    def result(signal: str | None, confidence: float, reason: str, **metrics: float) -> dict[str, Any]:
        defaults = {
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 0.0,
            "price_change": 0.0,
            "trend_strength": 0.0,
            "rsi_distance": 0.0,
            "score": 0.0,
        }
        defaults.update(metrics)
        return {
            "signal": signal,
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "reason": reason,
            **defaults,
        }

    required = {"open", "high", "low", "close", "volume"}
    if df is None or not required.issubset(df.columns) or len(df) < 20:
        return result(None, 0.0, "Not enough valid data")

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
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.fillna(50)

    current_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    current_ema9 = float(ema9.iloc[-1])
    current_ema21 = float(ema21.iloc[-1])
    current_rsi = float(rsi.iloc[-1])
    if any(pd.isna(value) for value in (current_close, previous_close, current_ema9, current_ema21, current_rsi)):
        return result(None, 0.0, "Indicator contains NaN")
    if previous_close <= 0 or current_close <= 0:
        return result(None, 0.0, "Invalid close price")

    price_change = abs(current_close - previous_close) / previous_close
    trend_strength = abs(current_ema9 - current_ema21) / current_close
    rsi_distance = abs(current_rsi - 50) / 50
    score = (0.5 * trend_strength) + (0.3 * rsi_distance) + (0.2 * price_change)
    metrics = {
        "ema9": current_ema9,
        "ema21": current_ema21,
        "rsi": current_rsi,
        "price_change": price_change,
        "trend_strength": trend_strength,
        "rsi_distance": rsi_distance,
        "score": score,
    }

    if 45 < current_rsi < 55:
        return result(None, 0.0, "RSI range filter", **metrics)
    if price_change < 0.001:
        return result(None, 0.0, "Low volatility filter", **metrics)
    if trend_strength < 0.0015:
        return result(None, 0.0, "No trend filter", **metrics)
    if score < 0.001:
        return result(None, 0.0, "Low score filter", **metrics)

    confidence = min(score / 0.01, 1.0)
    if confidence < 0.8:
        return result(None, confidence, "Low confidence filter", **metrics)

    if current_ema9 > current_ema21 and current_rsi > 55 and trend_strength > 0.0015 and price_change > 0.001:
        return result(
            "BUY",
            confidence,
            "EMA9 > EMA21, RSI > 55, trend strong, volatility confirmed",
            **metrics,
        )

    if current_ema9 < current_ema21 and current_rsi < 45 and trend_strength > 0.0015 and price_change > 0.001:
        return result(
            "SELL",
            confidence,
            "EMA9 < EMA21, RSI < 45, trend strong, volatility confirmed",
            **metrics,
        )

    return result(None, confidence, "Strict entry conditions not met", **metrics)


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
        """Return a scalping signal when strict EMA, RSI, and volatility conditions align."""
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

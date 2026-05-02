"""Breakout and volume spike signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import Settings
from utils.logger import get_logger


def generate_signal(df: pd.DataFrame) -> dict[str, Any]:
    """Generate aggressive EMA/RSI/volume/breakout scalping signals."""
    required = {"open", "high", "low", "close", "volume"}
    if df is None or not required.issubset(df.columns) or len(df) < 35:
        return {"signal": None, "confidence": 0.0, "reason": "Not enough valid data"}

    data = df.loc[:, ["open", "high", "low", "close", "volume"]].astype(float)
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

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
    current_volume = float(volume.iloc[-1])
    current_ema9 = float(ema9.iloc[-1])
    current_ema21 = float(ema21.iloc[-1])
    current_rsi = float(rsi.iloc[-1])
    avg_volume_20 = float(volume.iloc[-21:-1].mean())
    recent_high = float(high.iloc[-11:-1].max())
    recent_low = float(low.iloc[-11:-1].min())

    if pd.isna(avg_volume_20) or avg_volume_20 <= 0:
        return {"signal": None, "confidence": 0.0, "reason": "Invalid volume baseline"}

    volume_spike = current_volume > 1.5 * avg_volume_20
    bullish_momentum = current_ema9 > current_ema21
    bearish_momentum = current_ema9 < current_ema21
    bullish_rsi = 40 <= current_rsi <= 70
    bearish_rsi = 30 <= current_rsi <= 60
    bullish_breakout = current_close > recent_high
    bearish_breakout = current_close < recent_low

    ema_gap = abs(current_ema9 - current_ema21) / current_close if current_close else 0.0
    volume_strength = min(current_volume / (1.5 * avg_volume_20), 2.0) / 2.0

    if bullish_momentum and bullish_rsi and volume_spike and bullish_breakout:
        breakout_strength = min((current_close - recent_high) / recent_high, 0.01) / 0.01 if recent_high else 0.0
        rsi_score = max(1 - abs(current_rsi - 55) / 30, 0.0)
        confidence = (
            0.30
            + min(ema_gap / 0.003, 1.0) * 0.25
            + rsi_score * 0.20
            + volume_strength * 0.15
            + breakout_strength * 0.10
        )
        return {
            "signal": "BUY",
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "reason": "Bullish EMA momentum, healthy RSI, volume spike, and upside breakout",
        }

    if bearish_momentum and bearish_rsi and volume_spike and bearish_breakout:
        breakout_strength = min((recent_low - current_close) / recent_low, 0.01) / 0.01 if recent_low else 0.0
        rsi_score = max(1 - abs(current_rsi - 45) / 30, 0.0)
        confidence = (
            0.30
            + min(ema_gap / 0.003, 1.0) * 0.25
            + rsi_score * 0.20
            + volume_strength * 0.15
            + breakout_strength * 0.10
        )
        return {
            "signal": "SELL",
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "reason": "Bearish EMA momentum, valid RSI, volume spike, and downside breakout",
        }

    return {
        "signal": None,
        "confidence": 0.0,
        "reason": (
            "No complete scalping setup detected "
            f"(ema9={current_ema9:.8f}, ema21={current_ema21:.8f}, rsi={current_rsi:.2f}, "
            f"volume_spike={volume_spike}, close={current_close:.8f}, "
            f"recent_high={recent_high:.8f}, recent_low={recent_low:.8f})"
        ),
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
        """Return a scalping signal when EMA, RSI, volume, and breakout align."""
        if len(candles) < 35:
            self.logger.warning("Skipping signal %s: insufficient candles count=%s required=35", symbol, len(candles))
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

        if self.settings.test_mode and self.settings.test_force_signal:
            signal = self._build_signal(symbol, "BUY", latest_close, 9.0, "forced test-mode signal")
            self.logger.info("Signal detected %s %s confidence=%.2f reason=%s", signal.symbol, signal.action, signal.confidence, signal.reason)
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

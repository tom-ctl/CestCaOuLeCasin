"""Breakout and volume spike signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from config import Settings
from utils.logger import get_logger


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
    """Detect recent high/low breakouts confirmed by volume spikes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("strategy")

    def analyze(self, symbol: str, candles: list[list[float]]) -> MarketSignal | None:
        """Return a signal when breakout and volume criteria are satisfied."""
        lookback = self.settings.breakout_lookback
        volume_lookback = self.settings.volume_lookback
        minimum = max(lookback, volume_lookback) + 2
        if len(candles) < minimum:
            self.logger.warning("Skipping signal %s: insufficient candles count=%s required=%s", symbol, len(candles), minimum)
            return None

        recent_closed = candles[-(lookback + 1) : -1]
        volume_closed = candles[-(volume_lookback + 1) : -1]
        latest = candles[-1]

        latest_close = float(latest[4])
        latest_volume = float(latest[5])
        recent_high = max(float(candle[2]) for candle in recent_closed)
        recent_low = min(float(candle[3]) for candle in recent_closed)
        average_volume = mean(float(candle[5]) for candle in volume_closed)

        volume_ratio = latest_volume / average_volume if average_volume else 0.0
        has_volume_spike = volume_ratio >= self.settings.volume_spike_multiplier
        high_breakout = latest_close > recent_high
        low_breakdown = latest_close < recent_low

        self.logger.debug(
            "Signal calc %s | close=%s recent_high=%s recent_low=%s volume=%s avg_volume=%s volume_ratio=%.4f high_breakout=%s low_breakdown=%s volume_spike=%s",
            symbol,
            latest_close,
            recent_high,
            recent_low,
            latest_volume,
            average_volume,
            volume_ratio,
            high_breakout,
            low_breakdown,
            has_volume_spike,
        )

        if high_breakout and has_volume_spike:
            confidence = self._confidence(latest_close, recent_high, volume_ratio)
            signal = self._build_signal(symbol, "BUY", latest_close, confidence, "high breakout with volume spike")
            self.logger.info("Signal detected %s %s confidence=%.2f reason=%s", signal.symbol, signal.action, signal.confidence, signal.reason)
            return signal

        if low_breakdown and has_volume_spike:
            confidence = self._confidence(recent_low, latest_close, volume_ratio)
            signal = self._build_signal(symbol, "SELL", latest_close, confidence, "low breakdown with volume spike")
            self.logger.info("Signal detected %s %s confidence=%.2f reason=%s", signal.symbol, signal.action, signal.confidence, signal.reason)
            return signal

        if self.settings.test_mode and self.settings.test_force_signal:
            signal = self._build_signal(symbol, "BUY", latest_close, 9.0, "forced test-mode signal")
            self.logger.info("Signal detected %s %s confidence=%.2f reason=%s", signal.symbol, signal.action, signal.confidence, signal.reason)
            return signal

        self.logger.debug("No signal %s | breakout=%s breakdown=%s volume_spike=%s", symbol, high_breakout, low_breakdown, has_volume_spike)
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

    @staticmethod
    def _confidence(price_a: float, price_b: float, volume_ratio: float) -> float:
        breakout_strength = abs(price_a - price_b) / price_b if price_b else 0.0
        score = 6.0 + min(breakout_strength * 100, 2.0) + min(volume_ratio - 1.0, 2.0)
        return min(score, 10.0)

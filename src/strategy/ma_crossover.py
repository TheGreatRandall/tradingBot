"""
Moving Average Crossover Strategy.
"""
from datetime import datetime
from typing import Optional
import pandas as pd
from loguru import logger

from .base import BaseStrategy, Signal, SignalType


class MACrossoverStrategy(BaseStrategy):
    """
    Simple Moving Average Crossover Strategy.

    Generates BUY signal when fast MA crosses above slow MA.
    Generates SELL signal when fast MA crosses below slow MA.
    """

    def __init__(
        self,
        fast_period: int = 10,
        slow_period: int = 20,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.05,
    ):
        super().__init__(
            name="MA Crossover",
            params={
                "fast_period": fast_period,
                "slow_period": slow_period,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
            }
        )
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate fast and slow moving averages."""
        df = df.copy()
        df["sma_fast"] = df["close"].rolling(window=self.fast_period).mean()
        df["sma_slow"] = df["close"].rolling(window=self.slow_period).mean()

        # Calculate crossover signals
        df["ma_diff"] = df["sma_fast"] - df["sma_slow"]
        df["ma_diff_prev"] = df["ma_diff"].shift(1)

        # Crossover detection
        df["cross_above"] = (df["ma_diff"] > 0) & (df["ma_diff_prev"] <= 0)
        df["cross_below"] = (df["ma_diff"] < 0) & (df["ma_diff_prev"] >= 0)

        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str) -> Signal:
        """Generate trading signal based on MA crossover."""
        if len(df) < self.slow_period + 1:
            logger.warning(f"Not enough data for {symbol}")
            return Signal(
                symbol=symbol,
                signal_type=SignalType.HOLD,
                strength=0.0,
                price=df["close"].iloc[-1] if len(df) > 0 else 0,
                timestamp=datetime.now(),
            )

        # Calculate indicators if not already present
        if "sma_fast" not in df.columns:
            df = self.calculate_indicators(df)

        latest = df.iloc[-1]
        current_price = latest["close"]

        # Determine signal
        if latest["cross_above"]:
            # Bullish crossover - BUY signal
            signal_type = SignalType.BUY
            strength = self._calculate_strength(df)
            stop_loss = current_price * (1 - self.stop_loss_pct)
            take_profit = current_price * (1 + self.take_profit_pct)
        elif latest["cross_below"]:
            # Bearish crossover - SELL signal
            signal_type = SignalType.SELL
            strength = self._calculate_strength(df)
            stop_loss = None
            take_profit = None
        else:
            # No crossover - HOLD
            signal_type = SignalType.HOLD
            strength = 0.0
            stop_loss = None
            take_profit = None

        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            strength=strength,
            price=current_price,
            timestamp=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "sma_fast": latest["sma_fast"],
                "sma_slow": latest["sma_slow"],
                "ma_diff": latest["ma_diff"],
            }
        )

    def _calculate_strength(self, df: pd.DataFrame) -> float:
        """
        Calculate signal strength based on:
        - Momentum of the crossover
        - Volume confirmation
        - Trend alignment
        """
        latest = df.iloc[-1]

        # Base strength from MA difference magnitude
        ma_diff_pct = abs(latest["ma_diff"]) / latest["close"]
        momentum_strength = min(ma_diff_pct * 10, 0.5)  # Cap at 0.5

        # Volume confirmation (if volume is above average)
        avg_volume = df["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = latest["volume"] / avg_volume if avg_volume > 0 else 1
        volume_strength = min((volume_ratio - 1) * 0.25, 0.25) if volume_ratio > 1 else 0

        # Trend strength (how far price is from slow MA)
        trend_pct = (latest["close"] - latest["sma_slow"]) / latest["sma_slow"]
        trend_strength = min(abs(trend_pct) * 2, 0.25)

        total_strength = 0.5 + momentum_strength + volume_strength + trend_strength
        return min(total_strength, 1.0)

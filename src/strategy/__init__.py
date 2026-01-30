"""
Trading strategies.
"""
from .base import BaseStrategy, Signal, SignalType
from .ma_crossover import MACrossoverStrategy
from .opening_range_breakout import OpeningRangeBreakoutStrategy

__all__ = [
    "BaseStrategy",
    "Signal",
    "SignalType",
    "MACrossoverStrategy",
    "OpeningRangeBreakoutStrategy",
]

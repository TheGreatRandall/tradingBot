"""
Trading strategies.
"""
from .base import BaseStrategy, Signal, SignalType
from .ma_crossover import MACrossoverStrategy

__all__ = ["BaseStrategy", "Signal", "SignalType", "MACrossoverStrategy"]

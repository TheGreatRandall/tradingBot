"""
Historical data loading and management.
"""
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
from loguru import logger

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    ALPACA_DATA_AVAILABLE = True
except ImportError:
    ALPACA_DATA_AVAILABLE = False

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


class HistoricalDataLoader:
    """Load historical OHLCV data from various sources."""

    TIMEFRAME_MAP = {
        "1Min": (1, TimeFrameUnit.Minute) if ALPACA_DATA_AVAILABLE else None,
        "5Min": (5, TimeFrameUnit.Minute) if ALPACA_DATA_AVAILABLE else None,
        "15Min": (15, TimeFrameUnit.Minute) if ALPACA_DATA_AVAILABLE else None,
        "1H": (1, TimeFrameUnit.Hour) if ALPACA_DATA_AVAILABLE else None,
        "1D": (1, TimeFrameUnit.Day) if ALPACA_DATA_AVAILABLE else None,
    }

    def __init__(self, api_key: str = "", secret_key: str = ""):
        self.api_key = api_key
        self.secret_key = secret_key
        self.alpaca_client: Optional[StockHistoricalDataClient] = None

        if ALPACA_DATA_AVAILABLE and api_key and secret_key:
            self.alpaca_client = StockHistoricalDataClient(
                api_key=api_key,
                secret_key=secret_key,
            )

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1D",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol.

        Returns DataFrame with columns: open, high, low, close, volume
        """
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=365)

        # Try Alpaca first
        if self.alpaca_client and timeframe in self.TIMEFRAME_MAP:
            df = self._get_alpaca_bars(symbol, timeframe, start, end, limit)
            if df is not None and not df.empty:
                return df

        # Fallback to yfinance
        if YFINANCE_AVAILABLE:
            return self._get_yfinance_bars(symbol, timeframe, start, end)

        logger.error("No data source available")
        return pd.DataFrame()

    def _get_alpaca_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Optional[pd.DataFrame]:
        """Get bars from Alpaca."""
        try:
            tf_amount, tf_unit = self.TIMEFRAME_MAP[timeframe]
            tf = TimeFrame(tf_amount, tf_unit)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
            )
            bars = self.alpaca_client.get_stock_bars(request)
            df = bars.df

            if df.empty:
                return None

            # Reset multi-index if present
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)

            # Standardize column names
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "vwap": "vwap",
            })

            return df[["open", "high", "low", "close", "volume"]]

        except Exception as e:
            logger.warning(f"Failed to get Alpaca data for {symbol}: {e}")
            return None

    def _get_yfinance_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Get bars from Yahoo Finance."""
        try:
            # Map timeframe to yfinance interval
            interval_map = {
                "1Min": "1m",
                "5Min": "5m",
                "15Min": "15m",
                "1H": "1h",
                "1D": "1d",
            }
            interval = interval_map.get(timeframe, "1d")

            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start, end=end, interval=interval)

            if df.empty:
                logger.warning(f"No yfinance data for {symbol}")
                return pd.DataFrame()

            # Standardize column names
            df = df.rename(columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })

            return df[["open", "high", "low", "close", "volume"]]

        except Exception as e:
            logger.error(f"Failed to get yfinance data for {symbol}: {e}")
            return pd.DataFrame()

    def get_multiple_bars(
        self,
        symbols: list[str],
        timeframe: str = "1D",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict[str, pd.DataFrame]:
        """Get historical bars for multiple symbols."""
        result = {}
        for symbol in symbols:
            df = self.get_bars(symbol, timeframe, start, end)
            if not df.empty:
                result[symbol] = df
        return result

"""
Real-time market data client.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable
from loguru import logger

try:
    from alpaca.data.live import StockDataStream
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_DATA_AVAILABLE = True
except ImportError:
    ALPACA_DATA_AVAILABLE = False


@dataclass
class Quote:
    """Real-time quote data."""
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: int
    ask_size: int
    timestamp: datetime


@dataclass
class Bar:
    """OHLCV bar data."""
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    vwap: Optional[float] = None


class MarketDataClient:
    """Client for fetching real-time and streaming market data."""

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.stream: Optional[StockDataStream] = None
        self.historical_client: Optional[StockHistoricalDataClient] = None
        self._quote_handlers: list[Callable[[Quote], None]] = []
        self._bar_handlers: list[Callable[[Bar], None]] = []

    def connect(self) -> bool:
        """Initialize data clients."""
        if not ALPACA_DATA_AVAILABLE:
            logger.error("Alpaca data SDK not available")
            return False

        try:
            self.historical_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
            self.stream = StockDataStream(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
            logger.info("Market data client initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize market data client: {e}")
            return False

    def get_latest_quote(self, symbol: str) -> Optional[Quote]:
        """Get the latest quote for a symbol."""
        if not self.historical_client:
            logger.error("Historical client not initialized")
            return None

        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.historical_client.get_stock_latest_quote(request)
            quote = quotes[symbol]
            return Quote(
                symbol=symbol,
                bid_price=float(quote.bid_price),
                ask_price=float(quote.ask_price),
                bid_size=quote.bid_size,
                ask_size=quote.ask_size,
                timestamp=quote.timestamp,
            )
        except Exception as e:
            logger.error(f"Failed to get quote for {symbol}: {e}")
            return None

    def get_latest_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get latest quotes for multiple symbols."""
        if not self.historical_client:
            logger.error("Historical client not initialized")
            return {}

        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbols)
            quotes = self.historical_client.get_stock_latest_quote(request)
            return {
                symbol: Quote(
                    symbol=symbol,
                    bid_price=float(q.bid_price),
                    ask_price=float(q.ask_price),
                    bid_size=q.bid_size,
                    ask_size=q.ask_size,
                    timestamp=q.timestamp,
                )
                for symbol, q in quotes.items()
            }
        except Exception as e:
            logger.error(f"Failed to get quotes: {e}")
            return {}

    def subscribe_quotes(self, symbols: list[str], handler: Callable[[Quote], None]) -> None:
        """Subscribe to real-time quotes."""
        if not self.stream:
            logger.error("Stream not initialized")
            return

        self._quote_handlers.append(handler)

        async def quote_callback(data):
            quote = Quote(
                symbol=data.symbol,
                bid_price=float(data.bid_price),
                ask_price=float(data.ask_price),
                bid_size=data.bid_size,
                ask_size=data.ask_size,
                timestamp=data.timestamp,
            )
            for h in self._quote_handlers:
                h(quote)

        self.stream.subscribe_quotes(quote_callback, *symbols)

    def subscribe_bars(self, symbols: list[str], handler: Callable[[Bar], None]) -> None:
        """Subscribe to real-time bars."""
        if not self.stream:
            logger.error("Stream not initialized")
            return

        self._bar_handlers.append(handler)

        async def bar_callback(data):
            bar = Bar(
                symbol=data.symbol,
                open=float(data.open),
                high=float(data.high),
                low=float(data.low),
                close=float(data.close),
                volume=data.volume,
                timestamp=data.timestamp,
                vwap=float(data.vwap) if data.vwap else None,
            )
            for h in self._bar_handlers:
                h(bar)

        self.stream.subscribe_bars(bar_callback, *symbols)

    def start_streaming(self) -> None:
        """Start the streaming connection."""
        if self.stream:
            logger.info("Starting market data stream")
            self.stream.run()

    def stop_streaming(self) -> None:
        """Stop the streaming connection."""
        if self.stream:
            self.stream.stop()
            logger.info("Stopped market data stream")

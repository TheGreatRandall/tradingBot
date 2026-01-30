"""
Base broker interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Represents a trading order."""
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    status: OrderStatus
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    filled_quantity: float = 0
    filled_price: Optional[float] = None
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None


@dataclass
class Position:
    """Represents a portfolio position."""
    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float


@dataclass
class Account:
    """Represents broker account info."""
    account_id: str
    cash: float
    portfolio_value: float
    buying_power: float
    equity: float
    last_equity: float
    daytrading_buying_power: float
    pattern_day_trader: bool


class BaseBroker(ABC):
    """Abstract base class for broker implementations."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the broker API."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the broker API."""
        pass

    @abstractmethod
    def get_account(self) -> Account:
        """Get account information."""
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        pass

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> Order:
        """Submit a new order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        pass

    @abstractmethod
    def get_open_orders(self) -> list[Order]:
        """Get all open orders."""
        pass

    @abstractmethod
    def close_position(self, symbol: str) -> Optional[Order]:
        """Close a position for a symbol."""
        pass

    @abstractmethod
    def close_all_positions(self) -> list[Order]:
        """Close all open positions."""
        pass

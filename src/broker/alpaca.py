"""
Alpaca broker implementation.
"""
from typing import Optional
from loguru import logger

from .base import (
    BaseBroker,
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    Account,
)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest,
        LimitOrderRequest,
        StopOrderRequest,
        StopLimitOrderRequest,
    )
    from alpaca.trading.enums import OrderSide as AlpacaOrderSide
    from alpaca.trading.enums import TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logger.warning("alpaca-trade-api not installed. Run: pip install alpaca-trade-api")


class AlpacaBroker(BaseBroker):
    """Alpaca Markets broker implementation."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.client: Optional[TradingClient] = None

    def connect(self) -> bool:
        """Connect to Alpaca API."""
        if not ALPACA_AVAILABLE:
            logger.error("Alpaca SDK not available")
            return False

        try:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper,
            )
            # Test connection by getting account
            self.client.get_account()
            logger.info(f"Connected to Alpaca ({'paper' if self.paper else 'live'})")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Alpaca: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from Alpaca API."""
        self.client = None
        logger.info("Disconnected from Alpaca")

    def _ensure_connected(self) -> None:
        """Ensure we have an active connection."""
        if self.client is None:
            raise ConnectionError("Not connected to Alpaca. Call connect() first.")

    def get_account(self) -> Account:
        """Get account information."""
        self._ensure_connected()
        acc = self.client.get_account()
        return Account(
            account_id=acc.id,
            cash=float(acc.cash),
            portfolio_value=float(acc.portfolio_value),
            buying_power=float(acc.buying_power),
            equity=float(acc.equity),
            last_equity=float(acc.last_equity),
            daytrading_buying_power=float(acc.daytrading_buying_power),
            pattern_day_trader=acc.pattern_day_trader,
        )

    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        self._ensure_connected()
        positions = self.client.get_all_positions()
        return [self._convert_position(p) for p in positions]

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        self._ensure_connected()
        try:
            pos = self.client.get_open_position(symbol)
            return self._convert_position(pos)
        except Exception:
            return None

    def _convert_position(self, pos) -> Position:
        """Convert Alpaca position to our Position model."""
        return Position(
            symbol=pos.symbol,
            quantity=float(pos.qty),
            avg_entry_price=float(pos.avg_entry_price),
            current_price=float(pos.current_price),
            market_value=float(pos.market_value),
            unrealized_pl=float(pos.unrealized_pl),
            unrealized_pl_pct=float(pos.unrealized_plpc) * 100,
        )

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
        self._ensure_connected()

        alpaca_side = AlpacaOrderSide.BUY if side == OrderSide.BUY else AlpacaOrderSide.SELL

        if order_type == OrderType.MARKET:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                time_in_force=TimeInForce.DAY,
            )
        elif order_type == OrderType.LIMIT:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY,
            )
        elif order_type == OrderType.STOP:
            request = StopOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                stop_price=stop_price,
                time_in_force=TimeInForce.DAY,
            )
        elif order_type == OrderType.STOP_LIMIT:
            request = StopLimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=alpaca_side,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=TimeInForce.DAY,
            )
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        order = self.client.submit_order(request)
        logger.info(f"Submitted {side.value} order for {quantity} {symbol}")
        return self._convert_order(order)

    def _convert_order(self, order) -> Order:
        """Convert Alpaca order to our Order model."""
        status_map = {
            "new": OrderStatus.PENDING,
            "accepted": OrderStatus.ACCEPTED,
            "filled": OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }
        return Order(
            id=str(order.id),
            symbol=order.symbol,
            side=OrderSide.BUY if order.side.value == "buy" else OrderSide.SELL,
            order_type=OrderType(order.type.value),
            quantity=float(order.qty),
            status=status_map.get(order.status.value, OrderStatus.PENDING),
            limit_price=float(order.limit_price) if order.limit_price else None,
            stop_price=float(order.stop_price) if order.stop_price else None,
            filled_quantity=float(order.filled_qty) if order.filled_qty else 0,
            filled_price=float(order.filled_avg_price) if order.filled_avg_price else None,
            created_at=order.created_at,
            filled_at=order.filled_at,
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        self._ensure_connected()
        try:
            self.client.cancel_order_by_id(order_id)
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        self._ensure_connected()
        try:
            order = self.client.get_order_by_id(order_id)
            return self._convert_order(order)
        except Exception:
            return None

    def get_open_orders(self) -> list[Order]:
        """Get all open orders."""
        self._ensure_connected()
        orders = self.client.get_orders()
        return [self._convert_order(o) for o in orders]

    def close_position(self, symbol: str) -> Optional[Order]:
        """Close a position for a symbol."""
        self._ensure_connected()
        try:
            order = self.client.close_position(symbol)
            logger.info(f"Closed position for {symbol}")
            return self._convert_order(order)
        except Exception as e:
            logger.error(f"Failed to close position for {symbol}: {e}")
            return None

    def close_all_positions(self) -> list[Order]:
        """Close all open positions."""
        self._ensure_connected()
        try:
            responses = self.client.close_all_positions()
            logger.info("Closed all positions")
            return [self._convert_order(r) for r in responses if hasattr(r, 'id')]
        except Exception as e:
            logger.error(f"Failed to close all positions: {e}")
            return []

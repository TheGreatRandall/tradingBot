"""
Order execution and management.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from loguru import logger

from src.broker.base import BaseBroker, Order, OrderSide, OrderType, OrderStatus
from src.strategy.base import Signal, SignalType
from src.risk.manager import RiskManager


@dataclass
class ExecutedTrade:
    """Record of an executed trade."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: datetime
    signal_strength: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class OrderManager:
    """Manages order execution and tracking."""

    def __init__(self, broker: BaseBroker, risk_manager: RiskManager):
        self.broker = broker
        self.risk_manager = risk_manager
        self.pending_orders: dict[str, Order] = {}
        self.trade_history: list[ExecutedTrade] = []

    def execute_signal(self, signal: Signal) -> Optional[Order]:
        """Execute a trading signal."""
        account = self.broker.get_account()
        positions = self.broker.get_positions()

        if signal.signal_type == SignalType.BUY:
            return self._execute_buy(signal, account, positions)
        elif signal.signal_type == SignalType.SELL:
            return self._execute_sell(signal, positions)
        else:
            return None

    def _execute_buy(self, signal: Signal, account, positions) -> Optional[Order]:
        """Execute a buy order."""
        # Validate against risk limits
        is_valid, reason, position_size = self.risk_manager.validate_signal(
            signal=signal,
            account=account,
            positions=positions,
            current_price=signal.price,
        )

        if not is_valid:
            logger.warning(f"Buy signal rejected for {signal.symbol}: {reason}")
            return None

        # Calculate shares to buy
        shares = self.risk_manager.calculate_shares(position_size, signal.price)

        if shares < 1:
            logger.warning(f"Cannot buy {signal.symbol}: calculated shares < 1")
            return None

        try:
            order = self.broker.submit_order(
                symbol=signal.symbol,
                side=OrderSide.BUY,
                quantity=shares,
                order_type=OrderType.MARKET,
            )

            self.pending_orders[order.id] = order

            # Record the trade
            self.trade_history.append(ExecutedTrade(
                order_id=order.id,
                symbol=signal.symbol,
                side="buy",
                quantity=shares,
                price=signal.price,
                timestamp=datetime.now(),
                signal_strength=signal.strength,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            ))

            logger.info(
                f"BUY order submitted: {shares} shares of {signal.symbol} "
                f"@ ~${signal.price:.2f} (strength: {signal.strength:.2f})"
            )

            return order

        except Exception as e:
            logger.error(f"Failed to submit buy order for {signal.symbol}: {e}")
            return None

    def _execute_sell(self, signal: Signal, positions) -> Optional[Order]:
        """Execute a sell order (close position)."""
        position = next((p for p in positions if p.symbol == signal.symbol), None)

        if not position:
            logger.debug(f"No position to sell for {signal.symbol}")
            return None

        try:
            order = self.broker.close_position(signal.symbol)

            if order:
                self.pending_orders[order.id] = order

                self.trade_history.append(ExecutedTrade(
                    order_id=order.id,
                    symbol=signal.symbol,
                    side="sell",
                    quantity=position.quantity,
                    price=signal.price,
                    timestamp=datetime.now(),
                    signal_strength=signal.strength,
                ))

                logger.info(
                    f"SELL order submitted: {position.quantity} shares of {signal.symbol} "
                    f"@ ~${signal.price:.2f} (P/L: {position.unrealized_pl_pct:.2f}%)"
                )

            return order

        except Exception as e:
            logger.error(f"Failed to close position for {signal.symbol}: {e}")
            return None

    def check_stop_loss_take_profit(self, positions) -> list[Order]:
        """Check positions against stop loss and take profit levels."""
        orders = []

        for position in positions:
            # Find the original trade for this position
            original_trade = next(
                (t for t in reversed(self.trade_history)
                 if t.symbol == position.symbol and t.side == "buy"),
                None
            )

            if not original_trade:
                continue

            current_price = position.current_price

            # Check stop loss
            if original_trade.stop_loss and current_price <= original_trade.stop_loss:
                logger.warning(
                    f"STOP LOSS triggered for {position.symbol}: "
                    f"${current_price:.2f} <= ${original_trade.stop_loss:.2f}"
                )
                order = self.broker.close_position(position.symbol)
                if order:
                    orders.append(order)

            # Check take profit
            elif original_trade.take_profit and current_price >= original_trade.take_profit:
                logger.info(
                    f"TAKE PROFIT triggered for {position.symbol}: "
                    f"${current_price:.2f} >= ${original_trade.take_profit:.2f}"
                )
                order = self.broker.close_position(position.symbol)
                if order:
                    orders.append(order)

        return orders

    def update_pending_orders(self) -> None:
        """Update status of pending orders."""
        for order_id in list(self.pending_orders.keys()):
            order = self.broker.get_order(order_id)

            if order:
                self.pending_orders[order_id] = order

                if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
                    del self.pending_orders[order_id]
                    logger.info(f"Order {order_id} completed with status: {order.status.value}")

    def cancel_all_pending(self) -> int:
        """Cancel all pending orders."""
        cancelled = 0
        for order_id in list(self.pending_orders.keys()):
            if self.broker.cancel_order(order_id):
                cancelled += 1
        return cancelled

    def get_trade_summary(self) -> dict:
        """Get summary of trading activity."""
        if not self.trade_history:
            return {"total_trades": 0}

        buys = [t for t in self.trade_history if t.side == "buy"]
        sells = [t for t in self.trade_history if t.side == "sell"]

        return {
            "total_trades": len(self.trade_history),
            "buy_trades": len(buys),
            "sell_trades": len(sells),
            "pending_orders": len(self.pending_orders),
            "symbols_traded": list(set(t.symbol for t in self.trade_history)),
        }

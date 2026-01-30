"""
Risk management system.
"""
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional
from loguru import logger

from src.broker.base import Account, Position
from src.strategy.base import Signal, SignalType


@dataclass
class RiskLimits:
    """Risk management limits."""
    max_position_size_pct: float = 0.05  # 5% of portfolio per position
    max_positions: int = 10
    max_portfolio_pct: float = 0.80  # 80% max allocation
    max_daily_loss_pct: float = 0.05  # 5% daily loss limit
    max_weekly_loss_pct: float = 0.10  # 10% weekly loss limit
    max_drawdown_pct: float = 0.15  # 15% max drawdown
    default_stop_loss_pct: float = 0.02  # 2% stop loss
    default_take_profit_pct: float = 0.05  # 5% take profit


@dataclass
class RiskStatus:
    """Current risk status."""
    can_trade: bool
    daily_pnl: float
    daily_pnl_pct: float
    weekly_pnl: float
    weekly_pnl_pct: float
    current_drawdown_pct: float
    positions_count: int
    portfolio_allocation_pct: float
    blocked_reason: Optional[str] = None


class RiskManager:
    """Manages risk limits and position sizing."""

    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.peak_equity: float = 0
        self.daily_starting_equity: float = 0
        self.weekly_starting_equity: float = 0
        self.last_daily_reset: Optional[date] = None
        self.last_weekly_reset: Optional[date] = None
        self.kill_switch_active: bool = False

    def update_equity_tracking(self, current_equity: float) -> None:
        """Update equity tracking for PnL calculations."""
        today = date.today()

        # Reset daily tracking
        if self.last_daily_reset != today:
            self.daily_starting_equity = current_equity
            self.last_daily_reset = today

        # Reset weekly tracking (Monday)
        if self.last_weekly_reset is None or (today.weekday() == 0 and self.last_weekly_reset != today):
            self.weekly_starting_equity = current_equity
            self.last_weekly_reset = today

        # Update peak for drawdown calculation
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

    def get_risk_status(
        self,
        account: Account,
        positions: list[Position],
    ) -> RiskStatus:
        """Get current risk status."""
        self.update_equity_tracking(account.equity)

        # Calculate PnL
        daily_pnl = account.equity - self.daily_starting_equity
        daily_pnl_pct = daily_pnl / self.daily_starting_equity if self.daily_starting_equity > 0 else 0

        weekly_pnl = account.equity - self.weekly_starting_equity
        weekly_pnl_pct = weekly_pnl / self.weekly_starting_equity if self.weekly_starting_equity > 0 else 0

        # Calculate drawdown
        drawdown = (self.peak_equity - account.equity) / self.peak_equity if self.peak_equity > 0 else 0

        # Calculate portfolio allocation
        total_position_value = sum(p.market_value for p in positions)
        allocation_pct = total_position_value / account.portfolio_value if account.portfolio_value > 0 else 0

        # Determine if we can trade
        can_trade = True
        blocked_reason = None

        if self.kill_switch_active:
            can_trade = False
            blocked_reason = "Kill switch is active"
        elif daily_pnl_pct <= -self.limits.max_daily_loss_pct:
            can_trade = False
            blocked_reason = f"Daily loss limit reached ({daily_pnl_pct:.2%})"
        elif weekly_pnl_pct <= -self.limits.max_weekly_loss_pct:
            can_trade = False
            blocked_reason = f"Weekly loss limit reached ({weekly_pnl_pct:.2%})"
        elif drawdown >= self.limits.max_drawdown_pct:
            can_trade = False
            blocked_reason = f"Max drawdown reached ({drawdown:.2%})"
            self.kill_switch_active = True
        elif len(positions) >= self.limits.max_positions:
            can_trade = False
            blocked_reason = f"Max positions reached ({len(positions)})"
        elif allocation_pct >= self.limits.max_portfolio_pct:
            can_trade = False
            blocked_reason = f"Max portfolio allocation reached ({allocation_pct:.2%})"

        return RiskStatus(
            can_trade=can_trade,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            weekly_pnl=weekly_pnl,
            weekly_pnl_pct=weekly_pnl_pct,
            current_drawdown_pct=drawdown,
            positions_count=len(positions),
            portfolio_allocation_pct=allocation_pct,
            blocked_reason=blocked_reason,
        )

    def validate_signal(
        self,
        signal: Signal,
        account: Account,
        positions: list[Position],
        current_price: float,
    ) -> tuple[bool, Optional[str], float]:
        """
        Validate a trading signal against risk limits.

        Returns:
            Tuple of (is_valid, rejection_reason, position_size_dollars)
        """
        risk_status = self.get_risk_status(account, positions)

        if not risk_status.can_trade:
            return False, risk_status.blocked_reason, 0

        # Only validate BUY signals for new positions
        if signal.signal_type != SignalType.BUY:
            return True, None, 0

        # Check if we already have a position in this symbol
        existing_position = next((p for p in positions if p.symbol == signal.symbol), None)
        if existing_position:
            return False, f"Already have position in {signal.symbol}", 0

        # Calculate position size
        max_position_value = account.portfolio_value * self.limits.max_position_size_pct
        position_size = min(max_position_value, account.buying_power)

        # Adjust by signal strength
        position_size *= signal.strength

        if position_size < 1:
            return False, "Position size too small", 0

        logger.info(
            f"Signal validated for {signal.symbol}: "
            f"${position_size:.2f} ({position_size/account.portfolio_value:.2%} of portfolio)"
        )

        return True, None, position_size

    def calculate_shares(self, position_size: float, price: float) -> int:
        """Calculate number of shares to buy."""
        if price <= 0:
            return 0
        shares = int(position_size / price)
        return max(shares, 0)

    def get_stop_loss_price(self, entry_price: float, side: str = "long") -> float:
        """Calculate stop loss price."""
        if side == "long":
            return entry_price * (1 - self.limits.default_stop_loss_pct)
        else:
            return entry_price * (1 + self.limits.default_stop_loss_pct)

    def get_take_profit_price(self, entry_price: float, side: str = "long") -> float:
        """Calculate take profit price."""
        if side == "long":
            return entry_price * (1 + self.limits.default_take_profit_pct)
        else:
            return entry_price * (1 - self.limits.default_take_profit_pct)

    def reset_kill_switch(self) -> None:
        """Manually reset the kill switch."""
        self.kill_switch_active = False
        logger.warning("Kill switch has been manually reset")

    def activate_kill_switch(self, reason: str) -> None:
        """Activate kill switch to stop all trading."""
        self.kill_switch_active = True
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

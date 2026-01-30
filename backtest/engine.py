"""
Backtesting engine.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger

from src.strategy.base import BaseStrategy, SignalType


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    strategy_name: str
    symbol: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    num_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


@dataclass
class BacktestTrade:
    """Record of a backtested trade."""
    entry_date: datetime
    entry_price: float
    exit_date: Optional[datetime]
    exit_price: Optional[float]
    quantity: float
    side: str
    pnl: float
    pnl_pct: float
    exit_reason: str


class BacktestEngine:
    """Engine for backtesting trading strategies."""

    def __init__(
        self,
        initial_capital: float = 100000,
        commission_pct: float = 0.001,  # 0.1%
        slippage_pct: float = 0.001,  # 0.1%
    ):
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

    def run(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        symbol: str,
        position_size_pct: float = 0.05,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.05,
    ) -> BacktestResult:
        """
        Run a backtest on historical data.

        Args:
            strategy: Trading strategy to test
            data: DataFrame with OHLCV data
            symbol: Symbol being tested
            position_size_pct: Position size as % of capital
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage

        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"Starting backtest for {strategy.name} on {symbol}")

        # Calculate indicators
        data = strategy.calculate_indicators(data)

        # Initialize tracking variables
        capital = self.initial_capital
        position = None
        trades = []
        equity_curve = []

        for i in range(len(data)):
            row = data.iloc[i]
            current_price = row["close"]
            timestamp = data.index[i]

            # Track equity
            if position:
                current_value = capital + (current_price - position["entry_price"]) * position["quantity"]
            else:
                current_value = capital
            equity_curve.append({"timestamp": timestamp, "equity": current_value})

            # Check stop loss / take profit for open position
            if position:
                exit_price = None
                exit_reason = None

                if current_price <= position["stop_loss"]:
                    exit_price = position["stop_loss"] * (1 - self.slippage_pct)
                    exit_reason = "stop_loss"
                elif current_price >= position["take_profit"]:
                    exit_price = position["take_profit"] * (1 - self.slippage_pct)
                    exit_reason = "take_profit"

                if exit_price:
                    pnl = (exit_price - position["entry_price"]) * position["quantity"]
                    pnl -= exit_price * position["quantity"] * self.commission_pct
                    capital += position["quantity"] * position["entry_price"] + pnl

                    trades.append(BacktestTrade(
                        entry_date=position["entry_date"],
                        entry_price=position["entry_price"],
                        exit_date=timestamp,
                        exit_price=exit_price,
                        quantity=position["quantity"],
                        side="long",
                        pnl=pnl,
                        pnl_pct=pnl / (position["entry_price"] * position["quantity"]),
                        exit_reason=exit_reason,
                    ))
                    position = None
                    continue

            # Generate signal from strategy
            df_slice = data.iloc[:i+1]
            if len(df_slice) < 20:  # Need minimum data
                continue

            signal = strategy.generate_signal(df_slice, symbol)

            # Execute signals
            if signal.signal_type == SignalType.BUY and position is None:
                # Open long position
                position_value = capital * position_size_pct
                entry_price = current_price * (1 + self.slippage_pct)
                quantity = (position_value * (1 - self.commission_pct)) / entry_price

                position = {
                    "entry_date": timestamp,
                    "entry_price": entry_price,
                    "quantity": quantity,
                    "stop_loss": entry_price * (1 - stop_loss_pct),
                    "take_profit": entry_price * (1 + take_profit_pct),
                }
                capital -= position_value

            elif signal.signal_type == SignalType.SELL and position is not None:
                # Close position on signal
                exit_price = current_price * (1 - self.slippage_pct)
                pnl = (exit_price - position["entry_price"]) * position["quantity"]
                pnl -= exit_price * position["quantity"] * self.commission_pct
                capital += position["quantity"] * position["entry_price"] + pnl

                trades.append(BacktestTrade(
                    entry_date=position["entry_date"],
                    entry_price=position["entry_price"],
                    exit_date=timestamp,
                    exit_price=exit_price,
                    quantity=position["quantity"],
                    side="long",
                    pnl=pnl,
                    pnl_pct=pnl / (position["entry_price"] * position["quantity"]),
                    exit_reason="signal",
                ))
                position = None

        # Close any remaining position at end
        if position:
            exit_price = data.iloc[-1]["close"]
            pnl = (exit_price - position["entry_price"]) * position["quantity"]
            capital += position["quantity"] * position["entry_price"] + pnl

            trades.append(BacktestTrade(
                entry_date=position["entry_date"],
                entry_price=position["entry_price"],
                exit_date=data.index[-1],
                exit_price=exit_price,
                quantity=position["quantity"],
                side="long",
                pnl=pnl,
                pnl_pct=pnl / (position["entry_price"] * position["quantity"]),
                exit_reason="end_of_data",
            ))

        # Calculate metrics
        return self._calculate_metrics(
            strategy_name=strategy.name,
            symbol=symbol,
            start_date=data.index[0],
            end_date=data.index[-1],
            trades=trades,
            equity_curve=equity_curve,
        )

    def _calculate_metrics(
        self,
        strategy_name: str,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        trades: list[BacktestTrade],
        equity_curve: list[dict],
    ) -> BacktestResult:
        """Calculate performance metrics from trades."""
        if not equity_curve:
            return BacktestResult(
                strategy_name=strategy_name,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                initial_capital=self.initial_capital,
                final_capital=self.initial_capital,
                total_return=0,
                total_return_pct=0,
                num_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0,
                avg_win=0,
                avg_loss=0,
                profit_factor=0,
                max_drawdown=0,
                max_drawdown_pct=0,
                sharpe_ratio=0,
            )

        equity_df = pd.DataFrame(equity_curve)
        final_capital = equity_df["equity"].iloc[-1]
        total_return = final_capital - self.initial_capital
        total_return_pct = total_return / self.initial_capital

        # Trade statistics
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl <= 0]
        win_rate = len(winning_trades) / len(trades) if trades else 0
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = abs(np.mean([t.pnl for t in losing_trades])) if losing_trades else 0
        total_wins = sum(t.pnl for t in winning_trades)
        total_losses = abs(sum(t.pnl for t in losing_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # Drawdown
        equity_df["peak"] = equity_df["equity"].cummax()
        equity_df["drawdown"] = equity_df["peak"] - equity_df["equity"]
        equity_df["drawdown_pct"] = equity_df["drawdown"] / equity_df["peak"]
        max_drawdown = equity_df["drawdown"].max()
        max_drawdown_pct = equity_df["drawdown_pct"].max()

        # Sharpe ratio (assuming 252 trading days, 0% risk-free rate)
        equity_df["returns"] = equity_df["equity"].pct_change()
        sharpe_ratio = (
            equity_df["returns"].mean() / equity_df["returns"].std() * np.sqrt(252)
            if equity_df["returns"].std() > 0 else 0
        )

        return BacktestResult(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            total_return_pct=total_return_pct,
            num_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            trades=trades,
            equity_curve=equity_curve,
        )

    def print_results(self, result: BacktestResult) -> None:
        """Print formatted backtest results."""
        print("\n" + "=" * 60)
        print(f"BACKTEST RESULTS: {result.strategy_name}")
        print("=" * 60)
        print(f"Symbol: {result.symbol}")
        print(f"Period: {result.start_date.date()} to {result.end_date.date()}")
        print("-" * 60)
        print(f"Initial Capital:    ${result.initial_capital:,.2f}")
        print(f"Final Capital:      ${result.final_capital:,.2f}")
        print(f"Total Return:       ${result.total_return:,.2f} ({result.total_return_pct:.2%})")
        print("-" * 60)
        print(f"Total Trades:       {result.num_trades}")
        print(f"Winning Trades:     {result.winning_trades}")
        print(f"Losing Trades:      {result.losing_trades}")
        print(f"Win Rate:           {result.win_rate:.2%}")
        print(f"Avg Win:            ${result.avg_win:,.2f}")
        print(f"Avg Loss:           ${result.avg_loss:,.2f}")
        print(f"Profit Factor:      {result.profit_factor:.2f}")
        print("-" * 60)
        print(f"Max Drawdown:       ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2%})")
        print(f"Sharpe Ratio:       {result.sharpe_ratio:.2f}")
        print("=" * 60 + "\n")

"""
Opening Range Breakout (ORB) Strategy - 开盘区间突破策略

交易规则:
- 09:30-09:35: 不交易（噪音）
- 09:35-09:45: 计算开盘区间 OR（Opening Range）
- 09:45-11:00: 允许开仓
- 11:00-15:55: 只管理仓位，不开新仓
- 15:55: 强制平仓（不隔夜）

信号条件 (Long only):
1. close(1m) > OR_high（收盘突破）
2. volume(当前1m) > 1.5 * avg_volume(过去20根1m)
3. OR_range / OR_low >= 0.002（约 0.2%）

止损止盈:
- stop = entry - 1.0 * OR_range
- take_profit = entry + 2.0 * (entry - stop)（2R）
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
import pandas as pd
from loguru import logger

from .base import BaseStrategy, Signal, SignalType


# 美东时区
ET = ZoneInfo("America/New_York")


@dataclass
class OpeningRange:
    """开盘区间数据"""
    symbol: str
    date: datetime
    high: float
    low: float
    range: float
    avg_volume: float
    is_valid: bool  # 区间是否有效（满足最小波动要求）


@dataclass
class ORBPosition:
    """ORB 持仓信息"""
    symbol: str
    entry_price: float
    entry_time: datetime
    shares: int
    stop_loss: float
    take_profit: float
    or_high: float  # 用于假突破检测


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """
    Opening Range Breakout 开盘区间突破策略

    适用于美股日内交易，使用1分钟K线
    """

    # 交易时间窗口 (美东时间)
    MARKET_OPEN = time(9, 30)
    OR_CALC_START = time(9, 35)      # 开始计算OR
    OR_CALC_END = time(9, 45)        # OR计算结束
    ENTRY_WINDOW_END = time(11, 0)   # 开仓窗口结束
    FORCE_CLOSE_TIME = time(15, 55)  # 强制平仓时间
    MARKET_CLOSE = time(16, 0)

    # 默认参数
    DEFAULT_SYMBOLS = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
        "AMZN", "META", "TSLA", "AMD", "GOOGL",
        "AVGO", "JPM", "XLF", "XLK", "IWM",
        "TLT", "GLD", "COST", "NFLX", "ORCL"
    ]

    def __init__(
        self,
        risk_pct: float = 0.0025,           # 每笔风险 0.25%
        daily_max_loss_pct: float = 0.02,   # 日最大亏损 2%
        volume_multiplier: float = 1.5,     # 成交量倍数要求
        min_or_range_pct: float = 0.002,    # 最小OR区间 0.2%
        risk_reward_ratio: float = 2.0,     # 风险收益比 2R
        max_positions: int = 1,             # 最大同时持仓数
        check_false_breakout: bool = True,  # 是否检测假突破
    ):
        super().__init__(
            name="Opening Range Breakout",
            params={
                "risk_pct": risk_pct,
                "daily_max_loss_pct": daily_max_loss_pct,
                "volume_multiplier": volume_multiplier,
                "min_or_range_pct": min_or_range_pct,
                "risk_reward_ratio": risk_reward_ratio,
                "max_positions": max_positions,
            }
        )

        self.risk_pct = risk_pct
        self.daily_max_loss_pct = daily_max_loss_pct
        self.volume_multiplier = volume_multiplier
        self.min_or_range_pct = min_or_range_pct
        self.risk_reward_ratio = risk_reward_ratio
        self.max_positions = max_positions
        self.check_false_breakout = check_false_breakout

        # 每日状态（需要在每个交易日重置）
        self.opening_ranges: dict[str, OpeningRange] = {}  # symbol -> OR
        self.positions: dict[str, ORBPosition] = {}        # symbol -> position
        self.daily_pnl: float = 0.0
        self.daily_starting_equity: float = 0.0
        self.last_reset_date: Optional[datetime] = None
        self.is_daily_stopped: bool = False  # 日风控触发

    def reset_daily_state(self, equity: float) -> None:
        """重置每日状态 - 每个交易日开盘时调用"""
        self.opening_ranges = {}
        self.positions = {}
        self.daily_pnl = 0.0
        self.daily_starting_equity = equity
        self.last_reset_date = datetime.now(ET).date()
        self.is_daily_stopped = False
        logger.info(f"ORB策略日状态已重置，起始资金: ${equity:,.2f}")

    def get_current_et_time(self) -> datetime:
        """获取当前美东时间"""
        return datetime.now(ET)

    def get_trading_phase(self, current_time: Optional[datetime] = None) -> str:
        """
        获取当前交易阶段

        Returns:
            "pre_market"    - 盘前
            "noise"         - 09:30-09:35 噪音期
            "calc_or"       - 09:35-09:45 计算OR
            "entry_window"  - 09:45-11:00 开仓窗口
            "manage_only"   - 11:00-15:55 只管理仓位
            "force_close"   - 15:55-16:00 强制平仓
            "after_hours"   - 盘后
        """
        if current_time is None:
            current_time = self.get_current_et_time()

        t = current_time.time()

        if t < self.MARKET_OPEN:
            return "pre_market"
        elif t < self.OR_CALC_START:
            return "noise"
        elif t < self.OR_CALC_END:
            return "calc_or"
        elif t < self.ENTRY_WINDOW_END:
            return "entry_window"
        elif t < self.FORCE_CLOSE_TIME:
            return "manage_only"
        elif t < self.MARKET_CLOSE:
            return "force_close"
        else:
            return "after_hours"

    def calculate_opening_range(
        self,
        symbol: str,
        bars_1m: pd.DataFrame
    ) -> Optional[OpeningRange]:
        """
        计算开盘区间 (09:35-09:45 的高低点)

        Args:
            symbol: 股票代码
            bars_1m: 1分钟K线数据，需要包含 09:35-09:45 的数据

        Returns:
            OpeningRange 对象，如果数据不足返回 None
        """
        if bars_1m.empty:
            return None

        # 确保索引是时间戳
        if not isinstance(bars_1m.index, pd.DatetimeIndex):
            return None

        # 转换到美东时间
        try:
            bars_et = bars_1m.copy()
            if bars_et.index.tz is None:
                bars_et.index = bars_et.index.tz_localize("UTC")
            bars_et.index = bars_et.index.tz_convert(ET)
        except Exception as e:
            logger.warning(f"时区转换失败 {symbol}: {e}")
            return None

        # 获取今天的日期
        today = datetime.now(ET).date()

        # 筛选 09:35-09:45 的数据
        or_start = datetime.combine(today, self.OR_CALC_START).replace(tzinfo=ET)
        or_end = datetime.combine(today, self.OR_CALC_END).replace(tzinfo=ET)

        or_bars = bars_et[(bars_et.index >= or_start) & (bars_et.index < or_end)]

        if len(or_bars) < 5:  # 至少需要5根1分钟K线
            logger.warning(f"{symbol} OR数据不足: {len(or_bars)} bars")
            return None

        # 计算OR高低点
        or_high = or_bars["high"].max()
        or_low = or_bars["low"].min()
        or_range = or_high - or_low

        # 计算过去20根K线的平均成交量
        avg_volume = bars_et["volume"].tail(20).mean()

        # 检查是否满足最小波动要求
        is_valid = (or_range / or_low) >= self.min_or_range_pct

        opening_range = OpeningRange(
            symbol=symbol,
            date=today,
            high=or_high,
            low=or_low,
            range=or_range,
            avg_volume=avg_volume,
            is_valid=is_valid,
        )

        # 缓存
        self.opening_ranges[symbol] = opening_range

        logger.info(
            f"{symbol} OR计算完成: "
            f"High=${or_high:.2f}, Low=${or_low:.2f}, "
            f"Range=${or_range:.2f} ({or_range/or_low*100:.2f}%), "
            f"Valid={is_valid}"
        )

        return opening_range

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算指标 - ORB策略主要依赖实时数据，这里只做基本处理"""
        return df

    def check_entry_signal(
        self,
        symbol: str,
        current_bar: pd.Series,
        opening_range: OpeningRange,
    ) -> bool:
        """
        检查是否满足入场条件

        条件:
        1. close > OR_high（收盘突破）
        2. volume > 1.5 * avg_volume
        3. OR区间有效（满足最小波动）
        """
        if not opening_range.is_valid:
            return False

        close = current_bar["close"]
        volume = current_bar["volume"]

        # 条件1: 收盘突破OR高点
        breakout = close > opening_range.high

        # 条件2: 成交量确认
        volume_confirm = volume > (self.volume_multiplier * opening_range.avg_volume)

        if breakout and volume_confirm:
            logger.info(
                f"{symbol} 突破信号: "
                f"Close=${close:.2f} > OR_High=${opening_range.high:.2f}, "
                f"Vol={volume:,.0f} > {self.volume_multiplier}x Avg={opening_range.avg_volume:,.0f}"
            )
            return True

        return False

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        equity: float,
    ) -> int:
        """
        计算仓位大小

        shares = floor(risk_dollars / (entry - stop))
        risk_dollars = equity * risk_pct
        """
        risk_dollars = equity * self.risk_pct
        risk_per_share = entry_price - stop_loss

        if risk_per_share <= 0:
            logger.warning(f"{symbol} 止损价格无效: entry={entry_price}, stop={stop_loss}")
            return 0

        shares = int(risk_dollars / risk_per_share)

        # 确保至少买1股
        shares = max(shares, 0)

        logger.info(
            f"{symbol} 仓位计算: "
            f"Risk=${risk_dollars:.2f}, "
            f"Entry=${entry_price:.2f}, Stop=${stop_loss:.2f}, "
            f"Risk/Share=${risk_per_share:.2f}, "
            f"Shares={shares}"
        )

        return shares

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str,
        equity: float = 100000,
    ) -> Signal:
        """
        生成交易信号

        Args:
            df: 1分钟K线数据
            symbol: 股票代码
            equity: 当前账户权益
        """
        current_time = self.get_current_et_time()
        phase = self.get_trading_phase(current_time)

        # 检查是否需要重置日状态
        today = current_time.date()
        if self.last_reset_date != today:
            self.reset_daily_state(equity)

        # 检查日风控
        if self.is_daily_stopped:
            return self._hold_signal(symbol, df)

        # 检查日亏损
        if self.daily_starting_equity > 0:
            daily_loss_pct = self.daily_pnl / self.daily_starting_equity
            if daily_loss_pct <= -self.daily_max_loss_pct:
                logger.warning(
                    f"日风控触发! 日亏损: {daily_loss_pct:.2%} <= -{self.daily_max_loss_pct:.2%}"
                )
                self.is_daily_stopped = True
                # 返回强平信号
                if symbol in self.positions:
                    return self._sell_signal(symbol, df, "daily_risk_stop")
                return self._hold_signal(symbol, df)

        # 根据交易阶段处理
        if phase == "pre_market" or phase == "noise":
            return self._hold_signal(symbol, df)

        elif phase == "calc_or":
            # 计算开盘区间
            if symbol not in self.opening_ranges:
                self.calculate_opening_range(symbol, df)
            return self._hold_signal(symbol, df)

        elif phase == "entry_window":
            # 开仓窗口
            return self._process_entry_window(symbol, df, equity)

        elif phase == "manage_only":
            # 只管理仓位
            return self._process_manage_only(symbol, df)

        elif phase == "force_close":
            # 强制平仓
            if symbol in self.positions:
                return self._sell_signal(symbol, df, "force_close_eod")
            return self._hold_signal(symbol, df)

        else:  # after_hours
            return self._hold_signal(symbol, df)

    def _process_entry_window(
        self,
        symbol: str,
        df: pd.DataFrame,
        equity: float,
    ) -> Signal:
        """处理开仓窗口逻辑"""
        # 检查是否已有持仓
        if len(self.positions) >= self.max_positions:
            return self._hold_signal(symbol, df)

        if symbol in self.positions:
            # 已有此symbol持仓，检查止损止盈
            return self._check_exit_conditions(symbol, df)

        # 检查是否有有效的OR
        opening_range = self.opening_ranges.get(symbol)
        if not opening_range or not opening_range.is_valid:
            return self._hold_signal(symbol, df)

        # 获取当前K线
        if df.empty:
            return self._hold_signal(symbol, df)
        current_bar = df.iloc[-1]

        # 检查入场信号
        if self.check_entry_signal(symbol, current_bar, opening_range):
            return self._buy_signal(symbol, df, opening_range, equity)

        return self._hold_signal(symbol, df)

    def _process_manage_only(self, symbol: str, df: pd.DataFrame) -> Signal:
        """只管理仓位，不开新仓"""
        if symbol in self.positions:
            return self._check_exit_conditions(symbol, df)
        return self._hold_signal(symbol, df)

    def _check_exit_conditions(self, symbol: str, df: pd.DataFrame) -> Signal:
        """检查止损止盈条件"""
        position = self.positions.get(symbol)
        if not position:
            return self._hold_signal(symbol, df)

        if df.empty:
            return self._hold_signal(symbol, df)

        current_price = df.iloc[-1]["close"]

        # 检查止损
        if current_price <= position.stop_loss:
            logger.info(
                f"{symbol} 止损触发: "
                f"Price=${current_price:.2f} <= Stop=${position.stop_loss:.2f}"
            )
            return self._sell_signal(symbol, df, "stop_loss")

        # 检查止盈
        if current_price >= position.take_profit:
            logger.info(
                f"{symbol} 止盈触发: "
                f"Price=${current_price:.2f} >= TP=${position.take_profit:.2f}"
            )
            return self._sell_signal(symbol, df, "take_profit")

        # 检查假突破（可选）
        if self.check_false_breakout and current_price < position.or_high:
            logger.info(
                f"{symbol} 假突破: "
                f"Price=${current_price:.2f} < OR_High=${position.or_high:.2f}"
            )
            return self._sell_signal(symbol, df, "false_breakout")

        return self._hold_signal(symbol, df)

    def _buy_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        opening_range: OpeningRange,
        equity: float,
    ) -> Signal:
        """生成买入信号"""
        current_price = df.iloc[-1]["close"]

        # 计算止损止盈
        stop_loss = current_price - opening_range.range
        take_profit = current_price + (self.risk_reward_ratio * opening_range.range)

        # 计算仓位
        shares = self.calculate_position_size(
            symbol, current_price, stop_loss, equity
        )

        if shares <= 0:
            return self._hold_signal(symbol, df)

        # 记录持仓（实际下单后需要更新）
        self.positions[symbol] = ORBPosition(
            symbol=symbol,
            entry_price=current_price,
            entry_time=self.get_current_et_time(),
            shares=shares,
            stop_loss=stop_loss,
            take_profit=take_profit,
            or_high=opening_range.high,
        )

        return Signal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strength=1.0,
            price=current_price,
            timestamp=self.get_current_et_time(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                "strategy": "ORB",
                "or_high": opening_range.high,
                "or_low": opening_range.low,
                "or_range": opening_range.range,
                "shares": shares,
                "reason": "breakout",
            }
        )

    def _sell_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        reason: str,
    ) -> Signal:
        """生成卖出信号"""
        current_price = df.iloc[-1]["close"] if not df.empty else 0

        # 计算盈亏
        position = self.positions.get(symbol)
        if position:
            pnl = (current_price - position.entry_price) * position.shares
            self.daily_pnl += pnl
            # 移除持仓记录
            del self.positions[symbol]
            logger.info(f"{symbol} 平仓: PnL=${pnl:.2f}, 原因={reason}")

        return Signal(
            symbol=symbol,
            signal_type=SignalType.SELL,
            strength=1.0,
            price=current_price,
            timestamp=self.get_current_et_time(),
            metadata={
                "strategy": "ORB",
                "reason": reason,
            }
        )

    def _hold_signal(self, symbol: str, df: pd.DataFrame) -> Signal:
        """生成持有信号"""
        current_price = df.iloc[-1]["close"] if not df.empty else 0
        return Signal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            price=current_price,
            timestamp=self.get_current_et_time(),
        )

    def get_status(self) -> dict:
        """获取策略状态"""
        return {
            "phase": self.get_trading_phase(),
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": (
                self.daily_pnl / self.daily_starting_equity
                if self.daily_starting_equity > 0 else 0
            ),
            "is_daily_stopped": self.is_daily_stopped,
            "positions": list(self.positions.keys()),
            "opening_ranges": {
                sym: {
                    "high": or_.high,
                    "low": or_.low,
                    "range": or_.range,
                    "valid": or_.is_valid,
                }
                for sym, or_ in self.opening_ranges.items()
            },
        }

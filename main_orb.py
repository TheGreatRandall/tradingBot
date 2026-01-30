"""
Opening Range Breakout (ORB) Trading Bot - 开盘区间突破交易机器人

专门用于ORB日内策略的主程序
使用1分钟K线数据，严格遵循交易时间窗口

运行: python3 main_orb.py
"""
import time
import signal
import sys
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from loguru import logger

from config.settings import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    IS_PAPER,
    LOG_LEVEL,
    LOG_FILE,
    DISCORD_WEBHOOK_URL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from src.utils.logger import setup_logger
from src.utils.notifications import NotificationManager
from src.broker.alpaca import AlpacaBroker
from src.data.historical import HistoricalDataLoader
from src.strategy.opening_range_breakout import OpeningRangeBreakoutStrategy
from src.risk.manager import RiskManager, RiskLimits


# 美东时区
ET = ZoneInfo("America/New_York")

# ORB 策略标的
ORB_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
    "AMZN", "META", "TSLA", "AMD", "GOOGL",
    "AVGO", "JPM", "XLF", "XLK", "IWM",
    "TLT", "GLD", "COST", "NFLX", "ORCL"
]


class ORBTradingBot:
    """ORB 日内交易机器人"""

    def __init__(self):
        self.running = False
        self.broker: Optional[AlpacaBroker] = None
        self.data_loader: Optional[HistoricalDataLoader] = None
        self.strategy: Optional[OpeningRangeBreakoutStrategy] = None
        self.risk_manager: Optional[RiskManager] = None
        self.notifications: Optional[NotificationManager] = None
        self.symbols = ORB_SYMBOLS

    def initialize(self) -> bool:
        """初始化所有组件"""
        logger.info("正在初始化 ORB 交易机器人...")

        # 验证 API 密钥
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            logger.error("缺少 Alpaca API 凭证")
            return False

        # 初始化 Broker
        self.broker = AlpacaBroker(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=IS_PAPER,
        )

        if not self.broker.connect():
            logger.error("连接 Broker 失败")
            return False

        # 初始化数据加载器
        self.data_loader = HistoricalDataLoader(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

        # 初始化 ORB 策略
        self.strategy = OpeningRangeBreakoutStrategy(
            risk_pct=0.0025,           # 每笔风险 0.25%
            daily_max_loss_pct=0.02,   # 日最大亏损 2%
            volume_multiplier=1.5,     # 成交量倍数
            min_or_range_pct=0.002,    # 最小OR区间 0.2%
            risk_reward_ratio=2.0,     # 2R
            max_positions=1,           # 最多1个持仓
            check_false_breakout=True, # 检测假突破
        )

        # 初始化风控
        self.risk_manager = RiskManager(
            limits=RiskLimits(
                max_position_size_pct=0.10,  # ORB单笔最大10%
                max_daily_loss_pct=0.02,
                max_drawdown_pct=0.05,
            )
        )

        # 初始化通知
        self.notifications = NotificationManager(
            discord_webhook_url=DISCORD_WEBHOOK_URL,
            telegram_bot_token=TELEGRAM_BOT_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT_ID,
        )

        logger.info("ORB 交易机器人初始化成功")
        return True

    def get_et_now(self) -> datetime:
        """获取当前美东时间"""
        return datetime.now(ET)

    def is_market_day(self) -> bool:
        """检查今天是否是交易日（简化版：周一到周五）"""
        now = self.get_et_now()
        return now.weekday() < 5  # 0=周一, 4=周五

    def wait_for_market_open(self) -> None:
        """等待开盘"""
        while self.running:
            now = self.get_et_now()
            market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)

            if now >= market_open:
                return

            wait_seconds = (market_open - now).total_seconds()
            if wait_seconds > 60:
                logger.info(f"距离开盘还有 {wait_seconds/60:.1f} 分钟，等待中...")
                time.sleep(60)
            else:
                logger.info(f"距离开盘还有 {wait_seconds:.0f} 秒")
                time.sleep(max(1, wait_seconds))

    def fetch_1m_bars(self, symbol: str, limit: int = 30) -> Optional[dict]:
        """获取1分钟K线数据"""
        try:
            df = self.data_loader.get_bars(
                symbol=symbol,
                timeframe="1Min",
                limit=limit,
            )
            return df if not df.empty else None
        except Exception as e:
            logger.warning(f"获取 {symbol} 数据失败: {e}")
            return None

    def execute_signal(self, signal) -> bool:
        """执行交易信号"""
        from src.strategy.base import SignalType
        from src.broker.base import OrderSide, OrderType

        if signal.signal_type == SignalType.HOLD:
            return False

        try:
            if signal.signal_type == SignalType.BUY:
                shares = signal.metadata.get("shares", 0)
                if shares <= 0:
                    return False

                order = self.broker.submit_order(
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    quantity=shares,
                    order_type=OrderType.MARKET,
                )

                logger.info(
                    f"买入订单已提交: {shares} 股 {signal.symbol} "
                    f"@ ~${signal.price:.2f}"
                )

                # 发送通知
                self.notifications.send_trade_alert(
                    symbol=signal.symbol,
                    side="buy",
                    quantity=shares,
                    price=signal.price,
                )

                return True

            elif signal.signal_type == SignalType.SELL:
                # 平仓
                order = self.broker.close_position(signal.symbol)

                if order:
                    reason = signal.metadata.get("reason", "unknown")
                    logger.info(
                        f"卖出订单已提交: {signal.symbol} "
                        f"@ ~${signal.price:.2f}, 原因: {reason}"
                    )

                    self.notifications.send_trade_alert(
                        symbol=signal.symbol,
                        side="sell",
                        quantity=0,  # 全部平仓
                        price=signal.price,
                    )

                    return True

        except Exception as e:
            logger.error(f"执行信号失败 {signal.symbol}: {e}")
            self.notifications.send_error_alert(str(e), f"execute_{signal.symbol}")

        return False

    def run_trading_loop(self) -> None:
        """运行交易循环"""
        logger.info("=" * 60)
        logger.info("ORB 交易循环启动")
        logger.info(f"标的数量: {len(self.symbols)}")
        logger.info(f"标的: {', '.join(self.symbols[:10])}...")
        logger.info("=" * 60)

        self.running = True
        loop_interval = 10  # 每10秒检查一次

        # 获取账户信息
        account = self.broker.get_account()
        equity = account.equity
        logger.info(f"账户权益: ${equity:,.2f}")

        # 重置策略日状态
        self.strategy.reset_daily_state(equity)

        while self.running:
            try:
                now = self.get_et_now()
                phase = self.strategy.get_trading_phase(now)

                # 盘前等待
                if phase == "pre_market":
                    logger.info(f"[{now.strftime('%H:%M:%S')}] 盘前，等待开盘...")
                    time.sleep(60)
                    continue

                # 盘后结束
                if phase == "after_hours":
                    logger.info("盘后，交易日结束")
                    self._print_daily_summary()
                    break

                # 噪音期
                if phase == "noise":
                    logger.debug(f"[{now.strftime('%H:%M:%S')}] 噪音期，跳过")
                    time.sleep(loop_interval)
                    continue

                # 获取最新账户信息
                account = self.broker.get_account()
                equity = account.equity

                # 遍历所有标的
                for symbol in self.symbols:
                    if not self.running:
                        break

                    # 获取1分钟数据
                    df = self.fetch_1m_bars(symbol, limit=30)
                    if df is None:
                        continue

                    # 生成信号
                    signal = self.strategy.generate_signal(df, symbol, equity)

                    # 执行信号
                    if signal.signal_type.value != "hold":
                        self.execute_signal(signal)

                # 打印状态
                status = self.strategy.get_status()
                logger.info(
                    f"[{now.strftime('%H:%M:%S')}] "
                    f"阶段={phase}, "
                    f"日PnL=${status['daily_pnl']:.2f} ({status['daily_pnl_pct']:.2%}), "
                    f"持仓={status['positions']}"
                )

                time.sleep(loop_interval)

            except KeyboardInterrupt:
                logger.info("收到中断信号")
                break
            except Exception as e:
                logger.error(f"交易循环错误: {e}")
                self.notifications.send_error_alert(str(e), "trading_loop")
                time.sleep(loop_interval)

        self.shutdown()

    def _print_daily_summary(self) -> None:
        """打印日度总结"""
        status = self.strategy.get_status()
        account = self.broker.get_account()

        logger.info("=" * 60)
        logger.info("日度交易总结")
        logger.info("=" * 60)
        logger.info(f"日盈亏: ${status['daily_pnl']:.2f} ({status['daily_pnl_pct']:.2%})")
        logger.info(f"账户权益: ${account.equity:,.2f}")
        logger.info(f"日风控触发: {status['is_daily_stopped']}")
        logger.info("=" * 60)

        # 发送通知
        self.notifications.send_daily_summary(
            pnl=status['daily_pnl'],
            pnl_pct=status['daily_pnl_pct'],
            trades=0,  # TODO: 实现交易计数
            positions=len(status['positions']),
        )

    def force_close_all(self) -> None:
        """强制平仓所有持仓"""
        logger.warning("强制平仓所有持仓...")
        try:
            orders = self.broker.close_all_positions()
            logger.info(f"已平仓 {len(orders)} 个持仓")
        except Exception as e:
            logger.error(f"强制平仓失败: {e}")

    def shutdown(self) -> None:
        """关闭机器人"""
        logger.info("正在关闭 ORB 交易机器人...")
        self.running = False

        # 检查是否需要平仓
        phase = self.strategy.get_trading_phase()
        if phase in ["entry_window", "manage_only", "force_close"]:
            self.force_close_all()

        if self.broker:
            self.broker.disconnect()

        logger.info("ORB 交易机器人已停止")


def main():
    """主入口"""
    # 设置日志
    setup_logger(log_level=LOG_LEVEL, log_file=LOG_FILE.replace(".log", "_orb.log"))

    logger.info("=" * 60)
    logger.info("ORB 开盘区间突破交易机器人")
    logger.info(f"模式: {'模拟盘' if IS_PAPER else '实盘'}")
    logger.info(f"启动时间: {datetime.now(ET)}")
    logger.info("=" * 60)

    if not IS_PAPER:
        logger.warning("实盘交易模式 - 真金白银!")
        response = input("输入 'CONFIRM' 继续: ")
        if response != "CONFIRM":
            logger.info("未确认，退出")
            return

    # 创建机器人
    bot = ORBTradingBot()

    # 信号处理
    def signal_handler(signum, frame):
        logger.info("收到关闭信号")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 初始化
    if not bot.initialize():
        logger.error("初始化失败")
        sys.exit(1)

    # 检查是否是交易日
    if not bot.is_market_day():
        logger.warning("今天不是交易日（周末）")
        return

    # 等待开盘
    bot.wait_for_market_open()

    # 运行交易循环
    bot.run_trading_loop()


if __name__ == "__main__":
    main()

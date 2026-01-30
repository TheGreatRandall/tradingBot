"""
Opening Range Breakout (ORB) v2 - 简化版

参考 ChatGPT 实现，整合到我们的框架中
使用 Bracket Order 自动管理止损止盈
"""
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from loguru import logger

from config.settings import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    IS_PAPER,
    LOG_LEVEL,
)
from src.utils.logger import setup_logger
from src.broker.alpaca import AlpacaBroker

# ============================================================
# 配置参数
# ============================================================
NY = ZoneInfo("America/New_York")

SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA",
    "AMZN", "META", "TSLA", "AMD", "GOOGL",
    "AVGO", "JPM", "XLF", "XLK", "IWM",
    "TLT", "GLD", "COST", "NFLX", "ORCL"
]

RISK_PCT = 0.0025           # 每笔风险 0.25%
DAILY_MAX_LOSS_PCT = 0.02   # 日最大亏损 2%
MAX_TRADES_PER_DAY = 3      # 每日最大交易次数
VOLUME_MULTIPLIER = 1.5     # 成交量倍数要求
MIN_OR_RANGE_PCT = 0.002    # 最小 OR 区间 0.2%


# ============================================================
# 工具函数
# ============================================================
def now_et() -> datetime:
    """获取当前美东时间"""
    return datetime.now(tz=NY)


def in_window(t: dtime, start: dtime, end: dtime) -> bool:
    """检查时间是否在窗口内"""
    return start <= t < end


def get_1m_bars(broker: AlpacaBroker, symbol: str, limit: int = 60):
    """获取1分钟K线数据"""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

        end = datetime.now(NY)
        start = end - __import__('datetime').timedelta(minutes=limit + 30)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start,
            end=end,
            limit=limit,
        )

        bars = client.get_stock_bars(request)
        df = bars.df

        if df.empty:
            return None

        # 处理 MultiIndex
        if isinstance(df.index, __import__('pandas').MultiIndex):
            df = df.reset_index(level=0, drop=True)

        # 转换到美东时间
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(NY)

        return df

    except Exception as e:
        logger.warning(f"获取 {symbol} 数据失败: {e}")
        return None


def calc_opening_range(bars, start_time: dtime, end_time: dtime):
    """
    计算开盘区间 (OR)

    Args:
        bars: DataFrame with OHLCV
        start_time: OR 开始时间 (09:35)
        end_time: OR 结束时间 (09:45)

    Returns:
        (or_high, or_low) or (None, None)
    """
    if bars is None or bars.empty:
        return None, None

    today = now_et().date()

    # 筛选 OR 时间段的数据
    or_bars = bars[
        (bars.index.date == today) &
        (bars.index.time >= start_time) &
        (bars.index.time < end_time)
    ]

    if len(or_bars) < 3:
        return None, None

    or_high = or_bars["high"].max()
    or_low = or_bars["low"].min()

    return or_high, or_low


def calc_avg_volume(bars, n: int = 20) -> float:
    """计算过去 n 根 K 线的平均成交量"""
    if bars is None or len(bars) < n:
        return 0
    return bars["volume"].tail(n).mean()


def should_enter_long(bars, or_high, or_low, avg_vol) -> bool:
    """
    检查是否满足做多条件

    条件:
    1. 最新收盘价 > OR_high（突破）
    2. 当前成交量 > 1.5 * 平均成交量
    3. OR 区间有效（>= 0.2%）
    """
    if bars is None or bars.empty:
        return False

    if or_high is None or or_low is None:
        return False

    latest = bars.iloc[-1]
    close = latest["close"]
    volume = latest["volume"]
    or_range = or_high - or_low

    # 条件1: 突破
    breakout = close > or_high

    # 条件2: 成交量确认
    volume_confirm = volume > (VOLUME_MULTIPLIER * avg_vol) if avg_vol > 0 else False

    # 条件3: OR 区间有效
    range_valid = (or_range / or_low) >= MIN_OR_RANGE_PCT if or_low > 0 else False

    if breakout and volume_confirm and range_valid:
        logger.info(
            f"入场信号: Close=${close:.2f} > OR_High=${or_high:.2f}, "
            f"Vol={volume:,.0f} > {VOLUME_MULTIPLIER}x {avg_vol:,.0f}, "
            f"Range={or_range/or_low*100:.2f}%"
        )
        return True

    return False


def calc_position_size(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    """
    计算仓位大小

    shares = floor(risk_dollars / risk_per_share)
    """
    risk_dollars = equity * risk_pct
    risk_per_share = entry - stop

    if risk_per_share <= 0:
        return 0

    shares = int(risk_dollars / risk_per_share)
    return max(shares, 0)


def should_stop_for_day(pnl: float, equity: float, max_loss_pct: float) -> bool:
    """检查是否触发日风控"""
    if equity <= 0:
        return True
    return (pnl / equity) <= -max_loss_pct


# ============================================================
# 主程序
# ============================================================
def main():
    # 初始化日志
    setup_logger(log_level=LOG_LEVEL, log_file="logs/orb_v2.log")

    logger.info("=" * 60)
    logger.info("ORB v2 开盘区间突破策略")
    logger.info(f"模式: {'模拟盘' if IS_PAPER else '实盘'}")
    logger.info(f"标的: {len(SYMBOLS)} 个")
    logger.info(f"风险参数: 每笔 {RISK_PCT:.2%}, 日最大亏损 {DAILY_MAX_LOSS_PCT:.2%}")
    logger.info("=" * 60)

    # 初始化 Broker
    broker = AlpacaBroker(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=IS_PAPER)
    if not broker.connect():
        logger.error("连接 Broker 失败")
        return

    # 日状态
    trades_today = 0
    stopped_for_day = False
    opening_ranges = {}  # symbol -> (or_high, or_low)
    or_calculated = False  # OR 是否已计算
    starting_equity = broker.get_account().equity

    logger.info(f"账户权益: ${starting_equity:,.2f}")

    # 主循环
    while True:
        try:
            t = now_et()
            tod = t.time()

            # 盘前等待
            if tod < dtime(9, 30):
                logger.info(f"[{t.strftime('%H:%M:%S')}] 盘前等待...")
                time.sleep(60)
                continue

            # 15:55 强制平仓并退出
            if tod >= dtime(15, 55):
                logger.info("15:55 强制平仓")
                broker.close_all_positions()
                break

            # 盘后退出
            if tod >= dtime(16, 0):
                logger.info("盘后，交易日结束")
                break

            # 获取账户信息
            account = broker.get_account()
            equity = account.equity
            pnl_today = equity - starting_equity

            # 检查日风控
            if should_stop_for_day(pnl_today, starting_equity, DAILY_MAX_LOSS_PCT):
                if not stopped_for_day:
                    logger.warning(
                        f"日风控触发! PnL=${pnl_today:.2f} "
                        f"({pnl_today/starting_equity:.2%})"
                    )
                    broker.close_all_positions()
                    stopped_for_day = True

            if stopped_for_day:
                logger.info(f"[{t.strftime('%H:%M:%S')}] 日风控已触发，等待收盘...")
                time.sleep(60)
                continue

            # ============================================================
            # 09:35-09:45: 计算开盘区间
            # ============================================================
            if in_window(tod, dtime(9, 35), dtime(9, 45)):
                if not or_calculated:
                    logger.info("计算开盘区间 (OR)...")
                    for sym in SYMBOLS:
                        bars = get_1m_bars(broker, sym, limit=60)
                        or_high, or_low = calc_opening_range(
                            bars,
                            start_time=dtime(9, 35),
                            end_time=dtime(9, 45)
                        )
                        if or_high and or_low:
                            opening_ranges[sym] = (or_high, or_low)
                            or_range = or_high - or_low
                            logger.info(
                                f"  {sym}: High=${or_high:.2f}, "
                                f"Low=${or_low:.2f}, Range={or_range/or_low*100:.2f}%"
                            )

                # 最后一分钟标记完成
                if tod >= dtime(9, 44):
                    or_calculated = True
                    logger.info(f"OR 计算完成: {len(opening_ranges)} 个标的")

            # ============================================================
            # 09:45-11:00: 开仓窗口
            # ============================================================
            if in_window(tod, dtime(9, 45), dtime(11, 0)):
                positions = broker.get_positions()

                # 只允许单持仓 & 未达交易次数上限
                if len(positions) == 0 and trades_today < MAX_TRADES_PER_DAY:
                    for sym in SYMBOLS:
                        or_data = opening_ranges.get(sym)
                        if not or_data:
                            continue

                        or_high, or_low = or_data
                        bars = get_1m_bars(broker, sym, limit=30)

                        if bars is None:
                            continue

                        avg_vol = calc_avg_volume(bars, n=20)

                        if should_enter_long(bars, or_high, or_low, avg_vol):
                            entry = bars.iloc[-1]["close"]
                            or_range = or_high - or_low
                            stop = entry - or_range
                            tp = entry + 2 * or_range  # 2R

                            shares = calc_position_size(equity, RISK_PCT, entry, stop)

                            if shares > 0:
                                logger.info(
                                    f"开仓 {sym}: {shares}股 @ ${entry:.2f}, "
                                    f"止损=${stop:.2f}, 止盈=${tp:.2f}"
                                )

                                order = broker.place_bracket_order(
                                    symbol=sym,
                                    qty=shares,
                                    stop_loss=stop,
                                    take_profit=tp,
                                )

                                if order:
                                    trades_today += 1
                                    logger.info(f"订单已提交: {order.id}")
                                    break  # 开了仓就不继续扫别的

            # ============================================================
            # 11:00-15:55: 只管理仓位
            # ============================================================
            if in_window(tod, dtime(11, 0), dtime(15, 55)):
                positions = broker.get_positions()
                if positions:
                    for pos in positions:
                        logger.info(
                            f"[{t.strftime('%H:%M:%S')}] 持仓 {pos.symbol}: "
                            f"{pos.quantity}股, P/L: {pos.unrealized_pl_pct:.2f}%"
                        )

            # 状态日志
            logger.info(
                f"[{t.strftime('%H:%M:%S')}] "
                f"权益=${equity:,.2f}, 日PnL=${pnl_today:+.2f}, "
                f"交易次数={trades_today}/{MAX_TRADES_PER_DAY}"
            )

            # 每分钟检查一次
            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("收到中断信号")
            broker.close_all_positions()
            break
        except Exception as e:
            logger.error(f"主循环错误: {e}")
            time.sleep(60)

    # 收盘总结
    account = broker.get_account()
    final_pnl = account.equity - starting_equity
    logger.info("=" * 60)
    logger.info("交易日总结")
    logger.info(f"起始权益: ${starting_equity:,.2f}")
    logger.info(f"结束权益: ${account.equity:,.2f}")
    logger.info(f"日盈亏: ${final_pnl:+.2f} ({final_pnl/starting_equity:+.2%})")
    logger.info(f"交易次数: {trades_today}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

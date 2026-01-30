"""
Auto Trading Bot - Main Entry Point
"""
import time
import signal
import sys
from datetime import datetime
from typing import Optional
from loguru import logger

from config.settings import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    IS_PAPER,
    LOG_LEVEL,
    LOG_FILE,
    TRADING_SYMBOLS,
    MAX_POSITION_SIZE_PCT,
    MAX_DAILY_LOSS_PCT,
    MAX_DRAWDOWN_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DISCORD_WEBHOOK_URL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from src.utils.logger import setup_logger
from src.utils.notifications import NotificationManager
from src.broker.alpaca import AlpacaBroker
from src.data.historical import HistoricalDataLoader
from src.data.market_data import MarketDataClient
from src.strategy.ma_crossover import MACrossoverStrategy
from src.risk.manager import RiskManager, RiskLimits
from src.execution.order_manager import OrderManager


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self):
        self.running = False
        self.broker: Optional[AlpacaBroker] = None
        self.data_loader: Optional[HistoricalDataLoader] = None
        self.market_data: Optional[MarketDataClient] = None
        self.strategy: Optional[MACrossoverStrategy] = None
        self.risk_manager: Optional[RiskManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.notifications: Optional[NotificationManager] = None

    def initialize(self) -> bool:
        """Initialize all components."""
        logger.info("Initializing trading bot...")

        # Validate API keys
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            logger.error("Missing Alpaca API credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
            return False

        # Initialize broker
        self.broker = AlpacaBroker(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=IS_PAPER,
        )

        if not self.broker.connect():
            logger.error("Failed to connect to broker")
            return False

        # Initialize data clients
        self.data_loader = HistoricalDataLoader(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

        self.market_data = MarketDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        self.market_data.connect()

        # Initialize strategy
        self.strategy = MACrossoverStrategy(
            fast_period=10,
            slow_period=20,
            stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
            take_profit_pct=DEFAULT_TAKE_PROFIT_PCT,
        )

        # Initialize risk manager
        self.risk_manager = RiskManager(
            limits=RiskLimits(
                max_position_size_pct=MAX_POSITION_SIZE_PCT,
                max_daily_loss_pct=MAX_DAILY_LOSS_PCT,
                max_drawdown_pct=MAX_DRAWDOWN_PCT,
                default_stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
                default_take_profit_pct=DEFAULT_TAKE_PROFIT_PCT,
            )
        )

        # Initialize order manager
        self.order_manager = OrderManager(
            broker=self.broker,
            risk_manager=self.risk_manager,
        )

        # Initialize notifications
        self.notifications = NotificationManager(
            discord_webhook_url=DISCORD_WEBHOOK_URL,
            telegram_bot_token=TELEGRAM_BOT_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT_ID,
        )

        logger.info("Trading bot initialized successfully")
        return True

    def run_trading_loop(self, interval_seconds: int = 60) -> None:
        """Run the main trading loop."""
        logger.info(f"Starting trading loop with {interval_seconds}s interval")
        logger.info(f"Trading symbols: {TRADING_SYMBOLS}")

        self.running = True

        while self.running:
            try:
                self._trading_iteration()
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt")
                break
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                self.notifications.send_error_alert(str(e), "trading_loop")
                time.sleep(interval_seconds)

        self.shutdown()

    def _trading_iteration(self) -> None:
        """Single iteration of the trading loop."""
        logger.debug("Running trading iteration...")

        # Get account and positions
        account = self.broker.get_account()
        positions = self.broker.get_positions()

        # Check risk status
        risk_status = self.risk_manager.get_risk_status(account, positions)

        if not risk_status.can_trade:
            logger.warning(f"Trading blocked: {risk_status.blocked_reason}")
            return

        # Check stop loss / take profit for existing positions
        self.order_manager.check_stop_loss_take_profit(positions)

        # Generate and execute signals for each symbol
        for symbol in TRADING_SYMBOLS:
            try:
                # Get historical data for signal generation
                df = self.data_loader.get_bars(symbol, timeframe="1D", limit=100)

                if df.empty:
                    logger.warning(f"No data available for {symbol}")
                    continue

                # Generate signal
                signal = self.strategy.generate_signal(df, symbol)

                if signal.signal_type.value != "hold":
                    logger.info(
                        f"{symbol}: {signal.signal_type.value.upper()} signal "
                        f"(strength: {signal.strength:.2f})"
                    )

                    # Execute signal
                    order = self.order_manager.execute_signal(signal)

                    if order:
                        self.notifications.send_trade_alert(
                            symbol=symbol,
                            side=signal.signal_type.value,
                            quantity=order.quantity,
                            price=signal.price,
                        )

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")

        # Update pending orders
        self.order_manager.update_pending_orders()

    def shutdown(self) -> None:
        """Gracefully shutdown the bot."""
        logger.info("Shutting down trading bot...")
        self.running = False

        if self.broker:
            # Optionally cancel pending orders
            # self.order_manager.cancel_all_pending()
            self.broker.disconnect()

        logger.info("Trading bot stopped")


def main():
    """Main entry point."""
    # Setup logging
    setup_logger(log_level=LOG_LEVEL, log_file=LOG_FILE)

    logger.info("=" * 60)
    logger.info("AUTO TRADING BOT")
    logger.info(f"Mode: {'PAPER' if IS_PAPER else 'LIVE'}")
    logger.info(f"Started at: {datetime.now()}")
    logger.info("=" * 60)

    if not IS_PAPER:
        logger.warning("LIVE TRADING MODE - Real money at risk!")
        response = input("Type 'CONFIRM' to continue with live trading: ")
        if response != "CONFIRM":
            logger.info("Live trading not confirmed. Exiting.")
            return

    # Create and run bot
    bot = TradingBot()

    # Handle signals for graceful shutdown
    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not bot.initialize():
        logger.error("Failed to initialize bot")
        sys.exit(1)

    # Run trading loop
    bot.run_trading_loop(interval_seconds=60)


if __name__ == "__main__":
    main()

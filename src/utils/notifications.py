"""
Notification system for alerts.
"""
from typing import Optional
from loguru import logger
import httpx


class NotificationManager:
    """Manages sending notifications through various channels."""

    def __init__(
        self,
        discord_webhook_url: str = "",
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.discord_webhook_url = discord_webhook_url
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id

    def send_discord(self, message: str, username: str = "Trading Bot") -> bool:
        """Send a Discord notification."""
        if not self.discord_webhook_url:
            return False

        try:
            response = httpx.post(
                self.discord_webhook_url,
                json={"content": message, "username": username},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")
            return False

    def send_telegram(self, message: str) -> bool:
        """Send a Telegram notification."""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            response = httpx.post(
                url,
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    def send_all(self, message: str) -> dict[str, bool]:
        """Send notification to all configured channels."""
        results = {}

        if self.discord_webhook_url:
            results["discord"] = self.send_discord(message)

        if self.telegram_bot_token:
            results["telegram"] = self.send_telegram(message)

        return results

    def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        pnl: Optional[float] = None,
    ) -> None:
        """Send a trade execution alert."""
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        message = f"{emoji} **{side.upper()}** {quantity} {symbol} @ ${price:.2f}"

        if pnl is not None:
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            message += f"\n{pnl_emoji} P/L: ${pnl:.2f}"

        self.send_all(message)

    def send_error_alert(self, error: str, context: str = "") -> None:
        """Send an error alert."""
        message = f"🚨 **ERROR**\n{error}"
        if context:
            message += f"\nContext: {context}"

        self.send_all(message)

    def send_daily_summary(
        self,
        pnl: float,
        pnl_pct: float,
        trades: int,
        positions: int,
    ) -> None:
        """Send daily performance summary."""
        emoji = "📈" if pnl >= 0 else "📉"
        message = (
            f"📊 **Daily Summary**\n"
            f"{emoji} P/L: ${pnl:.2f} ({pnl_pct:+.2%})\n"
            f"📈 Trades: {trades}\n"
            f"📋 Open Positions: {positions}"
        )

        self.send_all(message)

"""Telegram messaging service."""
import requests
from typing import Optional

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Telegram rejects messages longer than this (sendMessage "text" limit).
TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit``, preferring line breaks."""
    if text is None:
        return []
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramService:
    """Service for sending messages via Telegram Bot API."""

    REQUEST_TIMEOUT_SECONDS = 10

    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize Telegram service.

        Args:
            bot_token: Telegram bot token (defaults to settings.TELEGRAM_BOT_TOKEN)
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.base_url = settings.TELEGRAM_API_BASE_URL.format(token=self.bot_token)
        self.session = requests.session()

    def send_message(self, chat_id: int, text: str) -> bool:
        """
        Send a text message to a Telegram chat.

        Long messages are split into several Telegram messages.

        Args:
            chat_id: Telegram chat ID
            text: Message text to send

        Returns:
            True if message was sent successfully, False otherwise
        """
        url = f"{self.base_url}/sendMessage"
        try:
            for chunk in split_message(text):
                response = self.session.post(
                    url,
                    json={"chat_id": chat_id, "text": chunk},
                    timeout=self.REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            logger.info(f"Message sent to chat_id={chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to chat_id={chat_id}: {e}")
            return False

    def send_notification(self, chat_id: int, text: str) -> bool:
        """
        Send an important notification (reservation result, payment deadline...).

        For the plain Telegram service this is the same as ``send_message``; the
        composite ``NotificationService`` additionally mirrors it to KakaoTalk.
        """
        return self.send_message(chat_id, text)

    def send_to_multiple(self, chat_ids: list[int], text: str) -> int:
        """
        Send a message to multiple chats.

        Args:
            chat_ids: List of Telegram chat IDs
            text: Message text to send

        Returns:
            Number of successful sends
        """
        success_count = 0
        for chat_id in chat_ids:
            if self.send_message(chat_id, text):
                success_count += 1
        return success_count


# MessageTemplates class has been deprecated and replaced by Messages class
# Import Messages from telegramBot.messages for all message templates
from telegramBot.messages import Messages as MessageTemplates

# For backward compatibility, create an alias
# This allows existing code to continue using MessageTemplates.method_name()
# while actually calling the centralized Messages class

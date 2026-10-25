"""Composite messenger: Telegram first, KakaoTalk mirror for important notifications."""
from typing import Optional

from config.settings import settings
from services.kakao_service import KakaoService, KakaoAuthError, KakaoError
from services.telegram_service import TelegramService
from telegramBot.messages import Messages
from utils.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    """Drop-in replacement for ``TelegramService`` that also mirrors notifications.

    - ``send_message``: Telegram only (conversation prompts, periodic reminders).
    - ``send_notification``: Telegram + KakaoTalk "나에게 보내기" when the chat linked
      a Kakao account (reservation results, payment deadline, errors).
    - ``send_to_multiple``: Telegram only (admin/subscriber broadcasts).
    """

    def __init__(self, telegram: TelegramService, kakao: Optional[KakaoService], storage):
        self.telegram = telegram
        self.kakao = kakao
        self.storage = storage

    def send_message(self, chat_id: int, text: str) -> bool:
        return self.telegram.send_message(chat_id, text)

    def send_to_multiple(self, chat_ids: list[int], text: str) -> int:
        return self.telegram.send_to_multiple(chat_ids, text)

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        return self.telegram.delete_message(chat_id, message_id)

    def send_notification(self, chat_id: int, text: str) -> bool:
        sent = self.telegram.send_message(chat_id, text)
        self.mirror_to_kakao(chat_id, text)
        return sent

    def mirror_to_kakao(self, chat_id: int, text: str) -> bool:
        """Best effort: never raises, never blocks the Telegram delivery."""
        if not self.kakao or not self.kakao.enabled:
            return False
        try:
            if not self.kakao.is_linked(chat_id):
                return False
            self.kakao.send_memo(chat_id, text, link_url=settings.KORAIL_PAYMENT_URL)
            return True
        except KakaoAuthError as e:
            logger.warning(f"Kakao link unusable for chat_id={chat_id}: {e}")
            try:
                self.kakao.unlink(chat_id)
            except Exception:  # pragma: no cover - storage hiccup
                pass
            self.telegram.send_message(chat_id, Messages.KAKAO_LINK_EXPIRED)
        except KakaoError as e:
            logger.error(f"Kakao delivery failed for chat_id={chat_id}: {e}")
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Unexpected Kakao error for chat_id={chat_id}: {e}", exc_info=True)
        return False

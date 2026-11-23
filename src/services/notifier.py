"""User notifications: console/log always, Telegram (paired chats) and KakaoTalk (if linked)."""
from typing import Optional

from config.settings import settings
from services.kakao_service import KakaoService, KakaoAuthError, KakaoError
from utils.logger import get_logger

logger = get_logger(__name__)


class Notifier:
    """Delivers worker notifications (reservation result, payment deadline, errors)."""

    def __init__(self, storage, kakao: Optional[KakaoService] = None, user_id: int = settings.LOCAL_USER_ID,
                 telegram=None):
        self.storage = storage
        self.kakao = kakao if kakao is not None else KakaoService(storage)
        self.user_id = user_id
        self._telegram = telegram

    @property
    def telegram(self):
        if self._telegram is None and settings.telegram_enabled():
            from telegram_bot.api import TelegramApi
            self._telegram = TelegramApi(settings.TELEGRAM_BOT_TOKEN)
        return self._telegram

    def notify(self, text: str, link_url: Optional[str] = None) -> bool:
        """Log ``text`` and deliver it to Telegram and KakaoTalk. True when at least one delivery succeeded."""
        logger.info("📣 " + text.strip().replace("\n", " | ")[:500])
        delivered = self._notify_telegram(text)
        delivered = self._notify_kakao(text, link_url) or delivered
        return delivered

    def _notify_telegram(self, text: str) -> bool:
        api = self.telegram
        if api is None:
            return False
        chat_ids = list(self.storage.get_paired_users()) + list(settings.TELEGRAM_ALLOWED_CHAT_IDS)
        if not chat_ids:
            logger.debug("No paired Telegram chats; run `korailbot pair`")
            return False
        delivered = False
        for chat_id in dict.fromkeys(chat_ids):  # unique, keep order
            try:
                if api.send_message(chat_id, text):
                    delivered = True
            except Exception as e:  # pragma: no cover - defensive
                logger.error(f"Telegram delivery failed for chat {chat_id}: {e}")
        return delivered

    def _notify_kakao(self, text: str, link_url: Optional[str]) -> bool:
        if not self.kakao.enabled:
            return False
        try:
            if not self.kakao.is_linked(self.user_id):
                return False
            self.kakao.send_memo(self.user_id, text, link_url=link_url)
            return True
        except KakaoAuthError as e:
            logger.warning(f"KakaoTalk link unusable, removing it: {e}")
            try:
                self.kakao.unlink(self.user_id)
            except Exception:  # pragma: no cover - storage hiccup
                pass
        except KakaoError as e:
            logger.error(f"KakaoTalk delivery failed: {e}")
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Unexpected KakaoTalk error: {e}", exc_info=True)
        return False

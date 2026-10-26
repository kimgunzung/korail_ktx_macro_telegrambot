"""User notifications: console/log always, KakaoTalk "나에게 보내기" when linked."""
from typing import Optional

from config.settings import settings
from services.kakao_service import KakaoService, KakaoAuthError, KakaoError
from utils.logger import get_logger

logger = get_logger(__name__)


class Notifier:
    """Delivers worker notifications (reservation result, payment deadline, errors)."""

    def __init__(self, storage, kakao: Optional[KakaoService] = None, user_id: int = settings.LOCAL_USER_ID):
        self.storage = storage
        self.kakao = kakao if kakao is not None else KakaoService(storage)
        self.user_id = user_id

    def notify(self, text: str, link_url: Optional[str] = None) -> bool:
        """Log ``text`` and mirror it to KakaoTalk. Returns True when Kakao delivery succeeded."""
        logger.info("📣 " + text.strip().replace("\n", " | ")[:500])
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

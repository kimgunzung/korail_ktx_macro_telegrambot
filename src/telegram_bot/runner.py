"""Telegram long-polling loop: `korailbot telegram`.

No webhook, no public URL: the bot repeatedly calls ``getUpdates`` and answers each
text message through the transport-neutral ``ChatBot``. Reply keyboards render the
quick replies; ``/start``, ``/help`` and slash commands map onto the Korean commands.
"""
import time
from typing import Optional

from chatbot.logic import ChatBot
from config.settings import settings
from telegram_bot.api import TelegramApi, TelegramApiError
from utils.logger import get_logger

logger = get_logger(__name__)

SLASH_COMMANDS = {
    "/start": "도움말", "/help": "도움말", "/reserve": "예약", "/status": "상태", "/cancel": "취소",
    "/paid": "결제완료", "/card": "카드", "/reset": "처음부터", "/search": "조회", "/reservations": "예약목록",
}


def build_bot(storage) -> ChatBot:
    from services import KakaoService, ReservationService
    return ChatBot(storage, ReservationService(storage, settings.LOCAL_USER_ID), KakaoService(storage))


def translate(text: str) -> str:
    """``/status@MyBot`` -> ``상태``; other text unchanged."""
    stripped = text.strip()
    if stripped.startswith("/"):
        head, _, rest = stripped.partition(" ")
        command = head.split("@", 1)[0].lower()
        mapped = SLASH_COMMANDS.get(command)
        if mapped:
            return f"{mapped} {rest}".strip()
    return stripped


class TelegramRunner:
    """Processes updates one by one; testable without a network."""

    def __init__(self, api: TelegramApi, bot: ChatBot, poll_timeout: int = settings.TELEGRAM_POLL_TIMEOUT):
        self.api = api
        self.bot = bot
        self.poll_timeout = poll_timeout
        self.offset: Optional[int] = None

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")
        if chat_id is None or not isinstance(text, str):
            return  # photos, stickers, member updates...
        if chat.get("type") not in (None, "private"):
            logger.debug(f"Ignoring message from non-private chat {chat_id}")
            return
        reply = self.bot.handle(str(chat_id), translate(text))
        self.api.send_message(chat_id, reply.full_text, quick_replies=reply.quick_replies or None,
                              remove_keyboard=not reply.quick_replies)

    def poll_once(self) -> int:
        """One getUpdates round. Returns the number of updates handled."""
        updates = self.api.get_updates(offset=self.offset, timeout=self.poll_timeout)
        for update in updates:
            self.offset = update["update_id"] + 1
            try:
                self.handle_update(update)
            except Exception as e:  # never stop polling because of one message
                logger.error(f"Error handling update {update.get('update_id')}: {e}", exc_info=True)
        return len(updates)

    def run_forever(self) -> None:
        backoff = 1
        while True:
            try:
                self.poll_once()
                backoff = 1
            except TelegramApiError as e:
                logger.warning(f"Telegram polling error, retrying in {backoff}s: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


def run_polling(storage) -> None:
    api = TelegramApi(settings.TELEGRAM_BOT_TOKEN)
    try:
        me = api.get_me()
        logger.info(f"Telegram bot @{me.get('username')} connected; polling for messages")
        print(f"   봇: @{me.get('username')}  — 텔레그램에서 이 봇에게 메시지를 보내세요.")
    except TelegramApiError as e:
        raise SystemExit(f"❌ 텔레그램 봇 토큰 확인 실패: {e}")
    TelegramRunner(api, build_bot(storage)).run_forever()

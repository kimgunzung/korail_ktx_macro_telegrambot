"""Minimal Telegram Bot API client (requests only): long polling + sendMessage."""
from typing import List, Optional, Tuple

import requests

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """Split ``text`` into chunks no longer than ``limit``, preferring line breaks."""
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
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


class TelegramApiError(Exception):
    """Telegram returned ok=false or the request failed."""


class TelegramApi:
    """Thin wrapper over https://api.telegram.org/bot<token>/<method>."""

    REQUEST_TIMEOUT = 15

    def __init__(self, token: str, session: Optional[requests.Session] = None):
        self.base_url = settings.TELEGRAM_API_BASE_URL.format(token=token)
        self.session = session or requests.Session()

    def _call(self, method: str, request_timeout: Optional[float] = None, **params) -> dict:
        """POST ``params`` as JSON. ``request_timeout`` is the HTTP timeout (a Telegram
        parameter named ``timeout`` can still be passed through ``params``)."""
        try:
            response = self.session.post(
                f"{self.base_url}/{method}", json=params, timeout=request_timeout or self.REQUEST_TIMEOUT
            )
        except requests.RequestException as e:
            raise TelegramApiError(f"{method}: {e}") from e
        try:
            payload = response.json()
        except ValueError as e:
            raise TelegramApiError(f"{method}: non-JSON response (HTTP {response.status_code})") from e
        if not payload.get("ok"):
            raise TelegramApiError(f"{method}: {payload.get('description') or 'HTTP ' + str(response.status_code)}")
        return payload.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[dict]:
        """Long poll for new updates (blocks up to ``timeout`` seconds server side)."""
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", request_timeout=timeout + 10, **params) or []

    def send_message(self, chat_id, text: str, quick_replies: Optional[List[Tuple[str, str]]] = None,
                     remove_keyboard: bool = False) -> bool:
        """Send ``text`` (split if needed). Quick replies become a one-time reply keyboard."""
        chunks = split_message(text)
        if not chunks:
            return False
        try:
            for index, chunk in enumerate(chunks):
                params = {"chat_id": chat_id, "text": chunk}
                if index == len(chunks) - 1:
                    if quick_replies:
                        params["reply_markup"] = {
                            "keyboard": _keyboard_rows(quick_replies),
                            "resize_keyboard": True,
                            "one_time_keyboard": True,
                        }
                    elif remove_keyboard:
                        params["reply_markup"] = {"remove_keyboard": True}
                self._call("sendMessage", **params)
            return True
        except TelegramApiError as e:
            logger.error(f"Telegram sendMessage failed for chat {chat_id}: {e}")
            return False


def _keyboard_rows(quick_replies: List[Tuple[str, str]], per_row: int = 3) -> List[List[dict]]:
    """Reply keyboards send the button text back, so the button shows the message text itself."""
    buttons = [{"text": message or label} for label, message in quick_replies]
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]

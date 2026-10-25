"""KakaoTalk integration ("나에게 보내기", Kakao REST API).

Each Telegram chat can link a Kakao account through OAuth (``/kakao``). The bot then
mirrors important notifications (reservation result, payment deadline, ...) to the
user's KakaoTalk "나와의 채팅" room using the ``talk_message`` scope.

Kakao app prerequisites (https://developers.kakao.com):
- 카카오 로그인 활성화, Redirect URI 등록 (``KAKAO_REDIRECT_URI``)
- 동의항목: ``talk_message`` (카카오톡 메시지 전송)
- ``KAKAO_REST_API_KEY`` (+ ``KAKAO_CLIENT_SECRET`` if the app enforces it)
"""
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import requests

from config.settings import settings
from models import KakaoTokens
from utils.logger import get_logger

logger = get_logger(__name__)


class KakaoError(Exception):
    """A Kakao API call failed. ``str(e)`` is a user-presentable reason."""


class KakaoAuthError(KakaoError):
    """The stored tokens are invalid/expired and cannot be refreshed."""


def split_memo_text(text: str, limit: int = settings.KAKAO_MEMO_TEXT_LIMIT) -> list[str]:
    """Split a message into chunks that fit Kakao's text template limit.

    Prefers paragraph/line boundaries; collapses blank lines first because the
    limit is tight (200 characters).
    """
    if not text:
        return []
    text = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class KakaoService:
    """OAuth linking and "나에게 보내기" delivery for KakaoTalk."""

    REQUEST_TIMEOUT_SECONDS = 10
    MAX_MEMOS_PER_MESSAGE = 5
    # Refresh the access token this many seconds before Kakao says it expires
    ACCESS_TOKEN_SAFETY_MARGIN = 120

    def __init__(self, storage, session: Optional[requests.Session] = None):
        """
        Args:
            storage: Storage with ``get_kakao_tokens``/``save_kakao_tokens``/``delete_kakao_tokens``
            session: Optional requests session (tests inject a fake one)
        """
        self.storage = storage
        self.session = session or requests.Session()
        self._refresh_lock = threading.Lock()

    # ------------------------------------------------------------------ config

    @property
    def enabled(self) -> bool:
        """True when the server has a Kakao app configured."""
        return settings.kakao_enabled()

    def is_linked(self, chat_id: int) -> bool:
        """True when ``chat_id`` has stored Kakao tokens."""
        return self.storage.get_kakao_tokens(chat_id) is not None

    # ------------------------------------------------------------------- oauth

    @staticmethod
    def _state_secret() -> bytes:
        # Derived from the bot token: nobody else can forge a state for a chat id.
        return hashlib.sha256(("kakao-state:" + settings.TELEGRAM_BOT_TOKEN).encode()).digest()

    def build_state(self, chat_id: int) -> str:
        """Opaque, tamper-evident OAuth ``state`` carrying the Telegram chat id."""
        digest = hmac.new(self._state_secret(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:32]
        return f"{chat_id}.{digest}"

    def parse_state(self, state: Optional[str]) -> Optional[int]:
        """Return the chat id encoded in ``state`` or None when it was tampered with."""
        if not state or "." not in state:
            return None
        chat_part, _, digest = state.partition(".")
        try:
            chat_id = int(chat_part)
        except ValueError:
            return None
        expected = hmac.new(self._state_secret(), str(chat_id).encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expected, digest):
            return None
        return chat_id

    def get_authorize_url(self, chat_id: int) -> str:
        """Kakao login URL the user opens to grant ``talk_message``."""
        params = {
            "client_id": settings.KAKAO_REST_API_KEY,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "response_type": "code",
            "scope": "talk_message",
            "state": self.build_state(chat_id),
        }
        return f"{settings.KAKAO_AUTH_BASE_URL}/oauth/authorize?{urlencode(params)}"

    def _token_request(self, data: dict) -> dict:
        data = dict(data, client_id=settings.KAKAO_REST_API_KEY)
        if settings.KAKAO_CLIENT_SECRET:
            data["client_secret"] = settings.KAKAO_CLIENT_SECRET
        try:
            response = self.session.post(
                f"{settings.KAKAO_AUTH_BASE_URL}/oauth/token",
                data=data,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise KakaoError(f"카카오 인증 서버 연결 실패: {e}") from e

        payload = self._json(response)
        if response.status_code != 200 or "access_token" not in payload:
            error = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
            logger.warning(f"Kakao token request failed: {error}")
            raise KakaoAuthError(str(error))
        return payload

    def exchange_code(self, chat_id: int, code: str) -> KakaoTokens:
        """Exchange an authorization code for tokens and store them for ``chat_id``."""
        code = (code or "").strip()
        if not code:
            raise KakaoAuthError("인증 코드가 비어 있습니다")

        payload = self._token_request({
            "grant_type": "authorization_code",
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "code": code,
        })
        if "talk_message" not in (payload.get("scope") or "talk_message"):
            raise KakaoAuthError("'카카오톡 메시지 전송' 권한에 동의하지 않았습니다")

        tokens = KakaoTokens.from_token_response(chat_id, payload)
        self.storage.save_kakao_tokens(tokens)
        logger.info(f"Kakao account linked for chat_id={chat_id}")
        return tokens

    def unlink(self, chat_id: int) -> bool:
        """Forget the stored tokens. Returns False when nothing was linked."""
        tokens = self.storage.get_kakao_tokens(chat_id)
        if not tokens:
            return False
        self.storage.delete_kakao_tokens(chat_id)
        logger.info(f"Kakao account unlinked for chat_id={chat_id}")
        return True

    def _refresh(self, tokens: KakaoTokens) -> KakaoTokens:
        """Refresh the access token (and the refresh token when Kakao rotates it)."""
        if tokens.refresh_token_expired():
            raise KakaoAuthError("refresh token 만료")

        payload = self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
        })
        now = time.time()
        tokens.access_token = payload["access_token"]
        tokens.expires_at = now + float(payload.get("expires_in", 0))
        if payload.get("refresh_token"):
            tokens.refresh_token = payload["refresh_token"]
            tokens.refresh_expires_at = now + float(payload.get("refresh_token_expires_in", 0))
        self.storage.save_kakao_tokens(tokens)
        logger.info(f"Kakao access token refreshed for chat_id={tokens.chat_id}")
        return tokens

    def _valid_tokens(self, chat_id: int) -> KakaoTokens:
        tokens = self.storage.get_kakao_tokens(chat_id)
        if not tokens:
            raise KakaoAuthError("카카오톡이 연동되어 있지 않습니다")
        if tokens.access_token_expires_within(self.ACCESS_TOKEN_SAFETY_MARGIN):
            with self._refresh_lock:
                tokens = self.storage.get_kakao_tokens(chat_id) or tokens
                if tokens.access_token_expires_within(self.ACCESS_TOKEN_SAFETY_MARGIN):
                    tokens = self._refresh(tokens)
        return tokens

    # -------------------------------------------------------------------- send

    def _post_memo(self, access_token: str, text: str, link_url: Optional[str]) -> requests.Response:
        template = {"object_type": "text", "text": text}
        if link_url:
            template["link"] = {"web_url": link_url, "mobile_web_url": link_url}
            template["button_title"] = "코레일 결제/예약 확인"
        else:
            template["link"] = {}
        try:
            return self.session.post(
                f"{settings.KAKAO_API_BASE_URL}/v2/api/talk/memo/default/send",
                headers={"Authorization": f"Bearer {access_token}"},
                data={"template_object": json.dumps(template, ensure_ascii=False)},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise KakaoError(f"카카오 API 연결 실패: {e}") from e

    def send_memo(self, chat_id: int, text: str, link_url: Optional[str] = None) -> None:
        """Send ``text`` to the linked Kakao account's "나와의 채팅".

        Long texts are split into several memos (Kakao allows 200 characters each).

        Raises:
            KakaoAuthError: not linked, or tokens expired and could not be refreshed
            KakaoError: any other delivery failure
        """
        chunks = split_memo_text(text)
        if not chunks:
            return
        if len(chunks) > self.MAX_MEMOS_PER_MESSAGE:
            chunks = chunks[: self.MAX_MEMOS_PER_MESSAGE - 1] + [chunks[self.MAX_MEMOS_PER_MESSAGE - 1][:settings.KAKAO_MEMO_TEXT_LIMIT - 1] + "…"]

        tokens = self._valid_tokens(chat_id)
        total = len(chunks)
        for index, chunk in enumerate(chunks, 1):
            body = f"({index}/{total}) {chunk}" if total > 1 else chunk
            body = body[: settings.KAKAO_MEMO_TEXT_LIMIT]
            response = self._post_memo(tokens.access_token, body, link_url if index == total else None)

            if response.status_code == 401:
                # Token was revoked server-side; refresh once and retry this chunk.
                with self._refresh_lock:
                    tokens = self._refresh(tokens)
                response = self._post_memo(tokens.access_token, body, link_url if index == total else None)

            if response.status_code != 200:
                payload = self._json(response)
                code = payload.get("code")
                msg = payload.get("msg") or f"HTTP {response.status_code}"
                if code in (-401, -402):
                    raise KakaoAuthError(f"{msg} ({code})")
                raise KakaoError(f"{msg} ({code})" if code is not None else msg)

        logger.info(f"Kakao memo sent to chat_id={chat_id} ({total} part(s))")

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------ status

    def link_summary(self, chat_id: int) -> Optional[str]:
        """Human readable 'linked since' text, or None when not linked."""
        tokens = self.storage.get_kakao_tokens(chat_id)
        if not tokens:
            return None
        return datetime.fromtimestamp(tokens.linked_at).strftime("%Y-%m-%d %H:%M")

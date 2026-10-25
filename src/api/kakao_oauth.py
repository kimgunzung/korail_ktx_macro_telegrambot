"""Kakao OAuth redirect endpoint (completes /kakao linking)."""
from html import escape

from flask import request, make_response
from flask_restful import Resource

from services import KakaoService, KakaoError
from telegramBot.messages import Messages
from utils.logger import get_logger

logger = get_logger(__name__)


_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;background:#f5f5f5;margin:0;padding:48px 16px;color:#222}}
.card{{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
h1{{font-size:20px;margin:0 0 12px}}p{{line-height:1.6;margin:0 0 8px}}.ok{{color:#1a7f37}}.err{{color:#b42318}}</style></head>
<body><div class="card"><h1 class="{cls}">{title}</h1><p>{body}</p><p>이 창은 닫으셔도 됩니다.</p></div></body></html>"""


class KakaoOAuthCallback(Resource):
    """``GET /kakao/oauth?code=...&state=...`` - Kakao redirects here after login."""

    def __init__(self, storage, telegram_service, kakao_service: KakaoService = None, **kwargs):
        super().__init__(**kwargs)
        self.storage = storage
        self.telegram = telegram_service
        self.kakao = kakao_service or KakaoService(storage)

    @staticmethod
    def _page(ok: bool, title: str, body: str, status: int = 200):
        html = _PAGE.format(cls="ok" if ok else "err", title=escape(title), body=escape(body))
        response = make_response(html, status)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    def get(self):
        if not self.kakao.enabled:
            return self._page(False, "카카오톡 연동 미설정", "서버에 카카오 연동이 설정되어 있지 않습니다.", 404)

        chat_id = self.kakao.parse_state(request.args.get("state"))
        if chat_id is None:
            logger.warning("Kakao OAuth callback with invalid state")
            return self._page(False, "잘못된 요청", "연동 요청을 확인할 수 없습니다. 텔레그램에서 /kakao 를 다시 입력해주세요.", 400)

        error = request.args.get("error")
        if error:
            reason = request.args.get("error_description") or error
            logger.info(f"Kakao OAuth denied for chat_id={chat_id}: {reason}")
            self.telegram.send_message(chat_id, Messages.KAKAO_LINK_FAILED.format(reason=reason))
            return self._page(False, "카카오톡 연동 실패", f"사유: {reason}", 400)

        code = request.args.get("code", "")
        try:
            self.kakao.exchange_code(chat_id, code)
        except KakaoError as e:
            logger.warning(f"Kakao OAuth exchange failed for chat_id={chat_id}: {e}")
            self.telegram.send_message(chat_id, Messages.KAKAO_LINK_FAILED.format(reason=e))
            return self._page(False, "카카오톡 연동 실패", f"사유: {e}", 400)

        self.telegram.send_message(chat_id, Messages.KAKAO_LINKED)
        try:
            self.kakao.send_memo(chat_id, "🚄 코레일 예매 봇과 카카오톡 연동이 완료되었습니다. 예약 결과 알림이 이 채팅으로 전달됩니다.")
        except KakaoError as e:
            logger.warning(f"Kakao welcome memo failed for chat_id={chat_id}: {e}")
            self.telegram.send_message(chat_id, Messages.KAKAO_TEST_FAILED.format(reason=e))

        return self._page(True, "카카오톡 연동 완료", "텔레그램으로 돌아가 예약을 진행하세요. 예약 결과가 카카오톡으로도 전달됩니다.")

"""HTTP server for the KakaoTalk chatbot (Kakao i Open Builder skill) and the OAuth redirect.

Endpoints (run with ``korailbot serve``, put behind an HTTPS reverse proxy):

- ``POST <skill path>``  Open Builder skill request -> SkillResponse JSON (must answer < 5 s)
- ``GET  /kakao/oauth``  completes ``연동`` when KAKAO_REDIRECT_URI points at this server
- ``GET  /healthz``      liveness
"""
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from config.settings import settings
from kakao_skill.bot import KakaoBot
from kakao_skill.responses import text_response, user_key_of, utterance_of
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_BODY_BYTES = 64 * 1024


def build_app(storage, reservation_service=None, kakao=None) -> "SkillApp":
    from services import KakaoService, ReservationService
    reservation_service = reservation_service or ReservationService(storage, settings.LOCAL_USER_ID)
    kakao = kakao or KakaoService(storage)
    return SkillApp(storage, reservation_service, kakao)


class SkillApp:
    """Framework-free request dispatcher (testable without sockets)."""

    def __init__(self, storage, reservation_service, kakao):
        self.storage = storage
        self.reservation = reservation_service
        self.kakao = kakao
        self.bot = KakaoBot(storage, reservation_service, kakao)

    def skill(self, payload: dict) -> dict:
        key = user_key_of(payload)
        text = utterance_of(payload)
        logger.info(f"skill request from {(key or '?')[:6]}…: {text[:80]!r}")
        return self.bot.handle(key, text)

    def oauth(self, query: dict) -> tuple[int, str]:
        """Handle the OAuth redirect. Returns (status, html)."""
        from services.kakao_service import KakaoError
        if not self.kakao.enabled:
            return 404, _page(False, "카카오 알림 미설정", "서버에 KAKAO_REST_API_KEY 가 설정되어 있지 않습니다.")
        user_id = self.kakao.parse_state(query.get("state"))
        if user_id is None:
            return 400, _page(False, "잘못된 요청", "연동 요청을 확인할 수 없습니다. 카카오톡에서 '연동'을 다시 보내주세요.")
        if query.get("error"):
            reason = query.get("error_description") or query["error"]
            return 400, _page(False, "연동 실패", f"사유: {reason}")
        try:
            self.kakao.exchange_code(user_id, query.get("code", ""))
            self.kakao.send_memo(user_id, "🚄 코레일 예매 봇 알림 연동이 완료되었습니다. 예약 결과가 이 채팅으로 전달됩니다.")
        except KakaoError as e:
            return 400, _page(False, "연동 실패", f"사유: {e}")
        return 200, _page(True, "연동 완료", "예약 알림이 카카오톡 '나와의 채팅'으로 전달됩니다. 이 창은 닫아도 됩니다.")

    def dispatch(self, method: str, path: str, query: dict, body: Optional[bytes]) -> tuple[int, str, bytes]:
        """Returns (status, content_type, body)."""
        if method == "GET" and path == "/healthz":
            return 200, "application/json", b'{"ok":true}'
        if method == "GET" and path == "/kakao/oauth":
            status, html = self.oauth(query)
            return status, "text/html; charset=utf-8", html.encode("utf-8")
        if method == "POST" and path == settings.kakao_skill_path():
            try:
                payload = json.loads(body or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload is not an object")
            except ValueError as e:
                logger.warning(f"Bad skill payload: {e}")
                response = text_response("요청 형식이 올바르지 않습니다.")
            else:
                response = self.skill(payload)
            return 200, "application/json; charset=utf-8", json.dumps(response, ensure_ascii=False).encode("utf-8")
        return 404, "text/plain; charset=utf-8", b"not found"


def _page(ok: bool, title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title></head><body style='font-family:sans-serif;padding:40px'>"
        f"<h2 style='color:{'#1a7f37' if ok else '#b42318'}'>{'✅' if ok else '❌'} {escape(title)}</h2>"
        f"<p>{escape(body)}</p></body></html>"
    )


class _Handler(BaseHTTPRequestHandler):
    app: SkillApp = None  # type: ignore[assignment]
    server_version = "korailbot/1.0"

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send(413, "text/plain", b"payload too large")
                return
            body = self.rfile.read(length) if length else b""
        try:
            status, content_type, out = self.app.dispatch(method, parsed.path, query, body)
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Unhandled server error: {e}", exc_info=True)
            status, content_type, out = 500, "text/plain", b"internal error"
        self._send(status, content_type, out)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def log_message(self, format, *args):  # route access log through our logger
        logger.debug("%s - %s", self.address_string(), format % args)


def make_server(host: str, port: int, app: SkillApp) -> ThreadingHTTPServer:
    handler = type("SkillHandler", (_Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve_forever(host: str, port: int, storage) -> None:
    app = build_app(storage)
    server = make_server(host, port, app)
    logger.info(f"KakaoTalk skill server listening on http://{host}:{port}{settings.kakao_skill_path()}")
    try:
        server.serve_forever()
    finally:
        server.server_close()

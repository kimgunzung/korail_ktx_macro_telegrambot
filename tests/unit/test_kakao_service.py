"""Unit tests for the KakaoTalk service (no network, in-memory storage)."""
import json
import time
from unittest.mock import Mock, patch

import pytest
import requests

from models import KakaoTokens
from services.kakao_service import KakaoService, KakaoAuthError, KakaoError, split_memo_text


class MemoryStorage:
    """Minimal in-memory implementation of the Kakao token storage methods."""

    def __init__(self):
        self.tokens = {}

    def get_kakao_tokens(self, chat_id):
        return self.tokens.get(chat_id)

    def save_kakao_tokens(self, tokens):
        self.tokens[tokens.chat_id] = tokens

    def delete_kakao_tokens(self, chat_id):
        self.tokens.pop(chat_id, None)


def _response(status=200, payload=None):
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = payload if payload is not None else {}
    return response


@pytest.fixture
def kakao_env():
    with patch('config.settings.settings.KAKAO_REST_API_KEY', 'rest-key'), \
         patch('config.settings.settings.KAKAO_REDIRECT_URI', 'https://bot.example.com/kakao/oauth'), \
         patch('config.settings.settings.KAKAO_CLIENT_SECRET', None):
        yield


@pytest.fixture
def service(kakao_env):
    storage = MemoryStorage()
    session = Mock(spec=requests.Session)
    return KakaoService(storage, session=session), storage, session


class TestSplitMemoText:
    def test_short_text_is_one_chunk(self):
        assert split_memo_text("안녕하세요") == ["안녕하세요"]

    def test_blank_lines_are_collapsed(self):
        assert split_memo_text("a\n\n\nb\n") == ["a\nb"]

    def test_long_text_splits_on_line_boundaries_within_limit(self):
        lines = [f"줄 {i:02d} 열차 정보입니다" for i in range(40)]
        chunks = split_memo_text("\n".join(lines), limit=200)
        assert len(chunks) > 1
        assert all(len(c) <= 200 for c in chunks)
        assert "\n".join(chunks).replace("\n", "") == "".join(lines)

    def test_single_long_line_is_hard_cut(self):
        chunks = split_memo_text("가" * 450, limit=200)
        assert [len(c) for c in chunks] == [200, 200, 50]


class TestState:
    def test_state_round_trip(self, service):
        svc, _, _ = service
        assert svc.parse_state(svc.build_state(12345)) == 12345

    def test_tampered_state_is_rejected(self, service):
        svc, _, _ = service
        state = svc.build_state(12345)
        assert svc.parse_state(state.replace("12345", "99999")) is None
        assert svc.parse_state("garbage") is None
        assert svc.parse_state(None) is None

    def test_authorize_url_contains_scope_and_state(self, service):
        svc, _, _ = service
        url = svc.get_authorize_url(42)
        assert url.startswith("https://kauth.kakao.com/oauth/authorize?")
        assert "client_id=rest-key" in url
        assert "scope=talk_message" in url
        assert f"state={svc.build_state(42)}" in url
        assert "redirect_uri=https%3A%2F%2Fbot.example.com%2Fkakao%2Foauth" in url


class TestExchangeCode:
    def test_exchange_stores_tokens(self, service):
        svc, storage, session = service
        session.post.return_value = _response(200, {
            "access_token": "AT", "refresh_token": "RT", "expires_in": 21599,
            "refresh_token_expires_in": 5183999, "scope": "talk_message",
        })

        tokens = svc.exchange_code(7, "the-code")

        assert storage.get_kakao_tokens(7) is tokens
        assert tokens.access_token == "AT" and tokens.refresh_token == "RT"
        assert tokens.expires_at > time.time() + 21000
        data = session.post.call_args.kwargs["data"]
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "the-code"
        assert data["client_id"] == "rest-key"
        assert data["redirect_uri"] == "https://bot.example.com/kakao/oauth"

    def test_exchange_failure_raises_with_server_reason(self, service):
        svc, storage, session = service
        session.post.return_value = _response(400, {"error": "invalid_grant", "error_description": "authorization code not found"})

        with pytest.raises(KakaoAuthError, match="authorization code not found"):
            svc.exchange_code(7, "bad")
        assert storage.get_kakao_tokens(7) is None

    def test_exchange_without_talk_message_scope_is_rejected(self, service):
        svc, storage, session = service
        session.post.return_value = _response(200, {"access_token": "AT", "refresh_token": "RT", "scope": "profile"})

        with pytest.raises(KakaoAuthError, match="권한"):
            svc.exchange_code(7, "code")
        assert not svc.is_linked(7)

    def test_empty_code_rejected_without_request(self, service):
        svc, _, session = service
        with pytest.raises(KakaoAuthError):
            svc.exchange_code(7, "   ")
        session.post.assert_not_called()

    def test_network_error_is_kakao_error(self, service):
        svc, _, session = service
        session.post.side_effect = requests.ConnectionError("boom")
        with pytest.raises(KakaoError, match="연결 실패"):
            svc.exchange_code(7, "code")


def _linked(storage, chat_id=7, expires_in=3600, refresh_expires_in=10_000):
    now = time.time()
    tokens = KakaoTokens(
        chat_id=chat_id, access_token="AT", refresh_token="RT",
        expires_at=now + expires_in, refresh_expires_at=now + refresh_expires_in,
    )
    storage.save_kakao_tokens(tokens)
    return tokens


class TestSendMemo:
    def test_sends_text_template_with_bearer_token(self, service):
        svc, storage, session = service
        _linked(storage)
        session.post.return_value = _response(200, {"result_code": 0})

        svc.send_memo(7, "예약 성공!", link_url="https://www.letskorail.com/pay")

        kwargs = session.post.call_args.kwargs
        assert session.post.call_args.args[0].endswith("/v2/api/talk/memo/default/send")
        assert kwargs["headers"]["Authorization"] == "Bearer AT"
        template = json.loads(kwargs["data"]["template_object"])
        assert template["object_type"] == "text"
        assert template["text"] == "예약 성공!"
        assert template["link"]["web_url"] == "https://www.letskorail.com/pay"

    def test_long_message_is_sent_in_numbered_parts(self, service):
        svc, storage, session = service
        _linked(storage)
        session.post.return_value = _response(200, {"result_code": 0})

        svc.send_memo(7, "\n".join(f"좌석 {i} 정보 KTX 101 서울→부산 08:00 출발" for i in range(20)))

        texts = [json.loads(c.kwargs["data"]["template_object"])["text"] for c in session.post.call_args_list]
        assert len(texts) >= 2
        assert texts[0].startswith(f"(1/{len(texts)}) ")
        assert all(len(t) <= 200 for t in texts)

    def test_not_linked_raises_auth_error(self, service):
        svc, _, session = service
        with pytest.raises(KakaoAuthError):
            svc.send_memo(7, "hi")
        session.post.assert_not_called()

    def test_expired_access_token_is_refreshed_first(self, service):
        svc, storage, session = service
        _linked(storage, expires_in=10)  # inside the safety margin
        session.post.side_effect = [
            _response(200, {"access_token": "AT2", "expires_in": 21599}),  # refresh
            _response(200, {"result_code": 0}),                            # memo
        ]

        svc.send_memo(7, "hi")

        refresh_call, memo_call = session.post.call_args_list
        assert refresh_call.kwargs["data"]["grant_type"] == "refresh_token"
        assert refresh_call.kwargs["data"]["refresh_token"] == "RT"
        assert memo_call.kwargs["headers"]["Authorization"] == "Bearer AT2"
        assert storage.get_kakao_tokens(7).access_token == "AT2"

    def test_rotated_refresh_token_is_stored(self, service):
        svc, storage, session = service
        _linked(storage, expires_in=10)
        session.post.side_effect = [
            _response(200, {"access_token": "AT2", "expires_in": 21599,
                            "refresh_token": "RT2", "refresh_token_expires_in": 5183999}),
            _response(200, {"result_code": 0}),
        ]
        svc.send_memo(7, "hi")
        tokens = storage.get_kakao_tokens(7)
        assert tokens.refresh_token == "RT2"
        assert tokens.refresh_expires_at > time.time() + 5_000_000

    def test_401_triggers_one_refresh_and_retry(self, service):
        svc, storage, session = service
        _linked(storage)
        session.post.side_effect = [
            _response(401, {"msg": "this access token does not exist", "code": -401}),
            _response(200, {"access_token": "AT2", "expires_in": 21599}),
            _response(200, {"result_code": 0}),
        ]

        svc.send_memo(7, "hi")

        assert session.post.call_count == 3
        assert session.post.call_args_list[2].kwargs["headers"]["Authorization"] == "Bearer AT2"

    def test_expired_refresh_token_is_auth_error(self, service):
        svc, storage, session = service
        _linked(storage, expires_in=10, refresh_expires_in=-1)
        with pytest.raises(KakaoAuthError, match="만료"):
            svc.send_memo(7, "hi")
        session.post.assert_not_called()

    def test_insufficient_scope_is_auth_error(self, service):
        svc, storage, session = service
        _linked(storage)
        session.post.return_value = _response(403, {"msg": "insufficient scopes", "code": -402})
        with pytest.raises(KakaoAuthError, match="-402"):
            svc.send_memo(7, "hi")

    def test_other_api_error_is_kakao_error(self, service):
        svc, storage, session = service
        _linked(storage)
        session.post.return_value = _response(500, {"msg": "server error", "code": -1})
        with pytest.raises(KakaoError, match="server error"):
            svc.send_memo(7, "hi")


class TestUnlink:
    def test_unlink_removes_tokens(self, service):
        svc, storage, _ = service
        _linked(storage)
        assert svc.unlink(7) is True
        assert not svc.is_linked(7)
        assert svc.unlink(7) is False


class TestDisabled:
    def test_enabled_false_without_config(self):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', None):
            assert KakaoService(MemoryStorage()).enabled is False

    def test_enabled_true_with_config(self, kakao_env):
        assert KakaoService(MemoryStorage()).enabled is True


class TestLocalOAuthRedirect:
    def test_catches_single_redirect(self):
        import socket
        import threading
        import urllib.request
        from services.kakao_service import wait_for_oauth_redirect

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        result = {}

        def serve():
            result.update(wait_for_oauth_redirect(port, timeout=10))

        thread = threading.Thread(target=serve)
        thread.start()
        deadline = time.time() + 5
        body = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/kakao/oauth?code=abc123&state=1.deadbeef", timeout=1) as r:
                    body = r.read().decode("utf-8")
                    break
            except Exception:
                time.sleep(0.05)
        thread.join(timeout=5)
        assert result == {"code": "abc123", "state": "1.deadbeef"}
        assert body and "인증 코드를 받았습니다" in body

    def test_times_out_with_empty_result(self):
        import socket
        from services.kakao_service import wait_for_oauth_redirect

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert wait_for_oauth_redirect(port, timeout=0.2) == {}

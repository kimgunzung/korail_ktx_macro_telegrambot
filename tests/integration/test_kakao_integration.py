"""Integration tests for the KakaoTalk link commands and OAuth callback (Redis-backed)."""
import time
from unittest.mock import Mock, patch

import pytest
from flask import Flask
from flask_restful import Api

from api import TelegramWebhook, KakaoOAuthCallback
from handlers import CommandHandler
from models import KakaoTokens
from services import TelegramService, ReservationService, PaymentReminderService, KakaoService
from services.kakao_service import KakaoAuthError
from storage import RedisStorage


@pytest.fixture
def kakao_env():
    with patch('config.settings.settings.KAKAO_REST_API_KEY', 'rest-key'), \
         patch('config.settings.settings.KAKAO_REDIRECT_URI', 'https://bot.example.com/kakao/oauth'):
        yield


class TestKakaoCommands:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.kakao = KakaoService(self.storage)
        self.handler = CommandHandler(
            self.storage, self.telegram, Mock(spec=ReservationService),
            Mock(spec=PaymentReminderService), self.kakao
        )

    def teardown_method(self):
        self.storage.redis.flushdb()

    def _sent(self):
        return [c.args[1] for c in self.telegram.send_message.call_args_list]

    def test_kakao_not_configured(self):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', None):
            self.handler.route_command(1, "/kakao")
        assert "설정되어 있지 않습니다" in self._sent()[0]

    def test_kakao_sends_authorize_link(self, kakao_env):
        self.handler.route_command(1, "/kakao")
        text = self._sent()[0]
        assert "https://kauth.kakao.com/oauth/authorize?" in text
        assert self.kakao.build_state(1) in text
        assert "/kakao_code" in text

    def test_kakao_when_already_linked(self, kakao_env):
        self.storage.save_kakao_tokens(KakaoTokens(1, "AT", "RT", time.time() + 100, time.time() + 1000))
        self.handler.route_command(1, "/kakao")
        assert "이미" in self._sent()[0]

    def test_kakao_code_links_and_sends_test_memo(self, kakao_env):
        with patch.object(self.kakao, 'exchange_code') as exchange, patch.object(self.kakao, 'send_memo') as memo:
            self.handler.route_command(1, "/kakao_code abc123")
        exchange.assert_called_once_with(1, "abc123")
        memo.assert_called_once()
        assert "완료" in self._sent()[0]

    def test_kakao_code_usage_without_code(self, kakao_env):
        self.handler.route_command(1, "/kakao_code")
        assert "사용법" in self._sent()[0]

    def test_kakao_code_failure_reported(self, kakao_env):
        with patch.object(self.kakao, 'exchange_code', side_effect=KakaoAuthError("authorization code not found")):
            self.handler.route_command(1, "/kakao_code nope")
        assert "authorization code not found" in self._sent()[0]

    def test_kakao_off(self, kakao_env):
        self.storage.save_kakao_tokens(KakaoTokens(1, "AT", "RT", time.time() + 100, time.time() + 1000))
        self.handler.route_command(1, "/kakao_off")
        assert not self.kakao.is_linked(1)
        assert "해제" in self._sent()[0]

        self.handler.route_command(1, "/kakao_off")
        assert "연동되어 있지 않습니다" in self._sent()[1]

    def test_kakao_test_not_linked(self, kakao_env):
        self.handler.route_command(1, "/kakao_test")
        assert "연동되어 있지 않습니다" in self._sent()[0]

    def test_kakao_test_success(self, kakao_env):
        self.storage.save_kakao_tokens(KakaoTokens(1, "AT", "RT", time.time() + 100, time.time() + 1000))
        with patch.object(self.kakao, 'send_memo') as memo:
            self.handler.route_command(1, "/kakao_test")
        memo.assert_called_once()
        assert "테스트 메시지" in self._sent()[0]

    def test_kakao_test_expired_link_is_dropped(self, kakao_env):
        self.storage.save_kakao_tokens(KakaoTokens(1, "AT", "RT", time.time() + 100, time.time() + 1000))
        with patch.object(self.kakao, 'send_memo', side_effect=KakaoAuthError("refresh token 만료")):
            self.handler.route_command(1, "/kakao_test")
        assert not self.kakao.is_linked(1)
        assert "실패" in self._sent()[0]

    def test_help_mentions_kakao(self):
        self.handler.route_command(1, "/help")
        assert "/kakao" in self._sent()[0]

    def test_command_with_bot_suffix(self):
        self.handler.route_command(1, "/help@korail_bot")
        assert "/kakao" in self._sent()[0]


class TestKakaoOAuthCallback:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.kakao = KakaoService(self.storage, session=Mock())

        self.app = Flask(__name__)
        api = Api(self.app)
        api.add_resource(
            KakaoOAuthCallback, '/kakao/oauth',
            resource_class_kwargs={'storage': self.storage, 'telegram_service': self.telegram, 'kakao_service': self.kakao},
        )
        self.client = self.app.test_client()

    def teardown_method(self):
        self.storage.redis.flushdb()

    def test_disabled_returns_404(self):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', None):
            response = self.client.get('/kakao/oauth', query_string={'code': 'x', 'state': 'y'})
        assert response.status_code == 404

    def test_invalid_state_rejected(self, kakao_env):
        response = self.client.get('/kakao/oauth', query_string={'code': 'x', 'state': '1.deadbeef'})
        assert response.status_code == 400
        self.telegram.send_message.assert_not_called()

    def test_user_denied_consent(self, kakao_env):
        state = self.kakao.build_state(5)
        response = self.client.get('/kakao/oauth', query_string={
            'error': 'access_denied', 'error_description': 'User denied access', 'state': state})
        assert response.status_code == 400
        assert "User denied access" in self.telegram.send_message.call_args.args[1]

    def test_successful_link(self, kakao_env):
        state = self.kakao.build_state(5)
        self.kakao.session.post.side_effect = [
            Mock(status_code=200, json=Mock(return_value={
                "access_token": "AT", "refresh_token": "RT", "expires_in": 21599,
                "refresh_token_expires_in": 5183999, "scope": "talk_message"})),
            Mock(status_code=200, json=Mock(return_value={"result_code": 0})),
        ]

        response = self.client.get('/kakao/oauth', query_string={'code': 'the-code', 'state': state})

        assert response.status_code == 200
        assert "연동 완료" in response.get_data(as_text=True)
        tokens = self.storage.get_kakao_tokens(5)
        assert tokens is not None and tokens.access_token == "AT"
        assert "완료" in self.telegram.send_message.call_args_list[0].args[1]
        # welcome memo went out with the fresh access token
        memo_call = self.kakao.session.post.call_args_list[1]
        assert memo_call.kwargs["headers"]["Authorization"] == "Bearer AT"

    def test_exchange_failure(self, kakao_env):
        state = self.kakao.build_state(5)
        self.kakao.session.post.return_value = Mock(
            status_code=400, json=Mock(return_value={"error": "invalid_grant", "error_description": "code expired"}))

        response = self.client.get('/kakao/oauth', query_string={'code': 'old', 'state': state})

        assert response.status_code == 400
        assert self.storage.get_kakao_tokens(5) is None
        assert "code expired" in self.telegram.send_message.call_args.args[1]


class TestWebhookKakaoMirroring:
    """Reservation result callbacks go through send_notification (Telegram + Kakao)."""

    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock()
        self.telegram.send_message.return_value = True
        self.telegram.send_notification.return_value = True
        app = Flask(__name__)
        api = Api(app)
        api.add_resource(TelegramWebhook, '/telebot', resource_class_kwargs={
            'storage': self.storage,
            'telegram_service': self.telegram,
            'reservation_service': Mock(spec=ReservationService),
            'payment_reminder_service': Mock(spec=PaymentReminderService),
            'kakao_service': KakaoService(self.storage),
        })
        self.client = app.test_client()

    def teardown_method(self):
        self.storage.redis.flushdb()

    def test_callback_uses_send_notification(self):
        response = self.client.get('/telebot', query_string={'chatId': '5', 'msg': '🎉 예약 성공', 'status': '0'})
        assert response.status_code == 200
        self.telegram.send_notification.assert_called_once_with(5, '🎉 예약 성공')

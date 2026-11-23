"""Unit tests for the Telegram transport (API client, polling runner) and Telegram notifications."""
from unittest.mock import Mock, patch

import pytest
import requests

from chatbot.responses import Reply
from services.kakao_service import KakaoService
from services.notifier import Notifier
from telegram_bot.api import TelegramApi, TelegramApiError, split_message, _keyboard_rows
from telegram_bot.runner import TelegramRunner, translate


def _response(payload, status=200):
    r = Mock(spec=requests.Response)
    r.status_code = status
    r.json.return_value = payload
    return r


@pytest.fixture
def api():
    session = Mock(spec=requests.Session)
    return TelegramApi("123:abc", session=session), session


class TestSplit:
    def test_short(self):
        assert split_message("abc") == ["abc"]

    def test_long_prefers_newlines(self):
        text = "\n".join("x" * 100 for _ in range(60))
        chunks = split_message(text)
        assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)

    def test_keyboard_rows(self):
        rows = _keyboard_rows([("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")], per_row=3)
        assert rows == [[{"text": "1"}, {"text": "2"}, {"text": "3"}], [{"text": "4"}]]


class TestApi:
    def test_get_updates_passes_offset_and_timeout(self, api):
        client, session = api
        session.post.return_value = _response({"ok": True, "result": [{"update_id": 5}]})
        assert client.get_updates(offset=4, timeout=20) == [{"update_id": 5}]
        args, kwargs = session.post.call_args
        assert args[0].endswith("/bot123:abc/getUpdates")
        assert kwargs["json"] == {"timeout": 20, "allowed_updates": ["message"], "offset": 4}
        assert kwargs["timeout"] == 30

    def test_send_message_with_keyboard(self, api):
        client, session = api
        session.post.return_value = _response({"ok": True, "result": {}})
        assert client.send_message(7, "hi", quick_replies=[("예약", "예약"), ("상태", "상태")]) is True
        params = session.post.call_args.kwargs["json"]
        assert params["chat_id"] == 7 and params["text"] == "hi"
        assert params["reply_markup"]["keyboard"] == [[{"text": "예약"}, {"text": "상태"}]]
        assert params["reply_markup"]["one_time_keyboard"] is True

    def test_send_message_removes_keyboard(self, api):
        client, session = api
        session.post.return_value = _response({"ok": True, "result": {}})
        client.send_message(7, "hi", remove_keyboard=True)
        assert session.post.call_args.kwargs["json"]["reply_markup"] == {"remove_keyboard": True}

    def test_long_message_split_keyboard_on_last_chunk(self, api):
        client, session = api
        session.post.return_value = _response({"ok": True, "result": {}})
        client.send_message(7, "x" * 5000, quick_replies=[("a", "a")])
        calls = session.post.call_args_list
        assert len(calls) == 2
        assert "reply_markup" not in calls[0].kwargs["json"] and "reply_markup" in calls[1].kwargs["json"]

    def test_api_error_returns_false(self, api):
        client, session = api
        session.post.return_value = _response({"ok": False, "description": "chat not found"}, status=400)
        assert client.send_message(7, "hi") is False

    def test_network_error_raises_for_polling(self, api):
        client, session = api
        session.post.side_effect = requests.ConnectionError("down")
        with pytest.raises(TelegramApiError):
            client.get_updates()


class TestTranslate:
    def test_slash_commands(self):
        assert translate("/start") == "도움말"
        assert translate("/status@korail_bot") == "상태"
        assert translate("/reserve 20261225 서울 부산 0900") == "예약 20261225 서울 부산 0900"
        assert translate("/unknown") == "/unknown"
        assert translate("  상태 ") == "상태"


class TestRunner:
    def _runner(self):
        api = Mock(spec=TelegramApi)
        bot = Mock()
        bot.handle.return_value = Reply("안녕", [("예약", "예약")])
        return TelegramRunner(api, bot, poll_timeout=1), api, bot

    def test_handle_update_routes_text_and_renders_keyboard(self):
        runner, api, bot = self._runner()
        runner.handle_update({"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "/status"}})
        bot.handle.assert_called_once_with("42", "상태")
        api.send_message.assert_called_once_with(42, "안녕", quick_replies=[("예약", "예약")], remove_keyboard=False)

    def test_reply_without_buttons_removes_keyboard_and_uses_full_text(self):
        runner, api, bot = self._runner()
        bot.handle.return_value = Reply("출발일을 보내주세요", prefix_lines=["❌ 날짜 오류"])
        runner.handle_update({"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "x"}})
        api.send_message.assert_called_once_with(42, "❌ 날짜 오류\n출발일을 보내주세요", quick_replies=None, remove_keyboard=True)

    def test_ignores_non_text_and_group_chats(self):
        runner, api, bot = self._runner()
        runner.handle_update({"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "photo": []}})
        runner.handle_update({"update_id": 2, "message": {"chat": {"id": -9, "type": "group"}, "text": "상태"}})
        runner.handle_update({"update_id": 3, "edited_message": {"chat": {"id": 42}, "text": "상태"}})
        bot.handle.assert_not_called()

    def test_poll_once_advances_offset_and_survives_handler_errors(self):
        runner, api, bot = self._runner()
        api.get_updates.return_value = [
            {"update_id": 10, "message": {"chat": {"id": 1, "type": "private"}, "text": "a"}},
            {"update_id": 11, "message": {"chat": {"id": 1, "type": "private"}, "text": "b"}},
        ]
        bot.handle.side_effect = [RuntimeError("boom"), Reply("ok")]
        assert runner.poll_once() == 2
        assert runner.offset == 12
        api.get_updates.assert_called_once_with(offset=None, timeout=1)
        assert api.send_message.call_count == 1

    def test_run_forever_backs_off_on_api_errors(self):
        runner, api, bot = self._runner()
        api.get_updates.side_effect = [TelegramApiError("down"), KeyboardInterrupt()]
        with patch('telegram_bot.runner.time.sleep') as sleep:
            with pytest.raises(KeyboardInterrupt):
                runner.run_forever()
        sleep.assert_called_once_with(1)


class TestNotifierTelegram:
    def _notifier(self, paired, allowed=None):
        storage = Mock()
        storage.get_paired_users.return_value = paired
        kakao = Mock(spec=KakaoService)
        kakao.enabled = False
        api = Mock(spec=TelegramApi)
        api.send_message.return_value = True
        with patch('config.settings.settings.TELEGRAM_BOT_TOKEN', '123:abc'), \
             patch('config.settings.settings.TELEGRAM_ALLOWED_CHAT_IDS', allowed or []):
            notifier = Notifier(storage, kakao=kakao, telegram=api)
            return notifier, api

    def test_sends_to_each_paired_chat_once(self):
        with patch('config.settings.settings.TELEGRAM_BOT_TOKEN', '123:abc'), \
             patch('config.settings.settings.TELEGRAM_ALLOWED_CHAT_IDS', ["7"]):
            notifier, api = self._notifier(["7", "8"], allowed=["7"])
            assert notifier.notify("예약 성공") is True
        assert [c.args[0] for c in api.send_message.call_args_list] == ["7", "8"]

    def test_no_paired_chats(self):
        with patch('config.settings.settings.TELEGRAM_BOT_TOKEN', '123:abc'), \
             patch('config.settings.settings.TELEGRAM_ALLOWED_CHAT_IDS', []):
            notifier, api = self._notifier([])
            assert notifier.notify("x") is False
        api.send_message.assert_not_called()

    def test_without_token_no_telegram_client(self):
        storage = Mock()
        with patch('config.settings.settings.TELEGRAM_BOT_TOKEN', None):
            notifier = Notifier(storage, kakao=Mock(spec=KakaoService, enabled=False))
            assert notifier.telegram is None
            assert notifier.notify("x") is False

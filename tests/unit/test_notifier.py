"""Unit tests for the console + KakaoTalk notifier."""
from unittest.mock import Mock

from services.kakao_service import KakaoAuthError, KakaoError, KakaoService
from services.notifier import Notifier


def _notifier(linked=True, enabled=True):
    kakao = Mock(spec=KakaoService)
    kakao.enabled = enabled
    kakao.is_linked.return_value = linked
    storage = Mock()
    storage.get_paired_users.return_value = []
    return Notifier(storage=storage, kakao=kakao, user_id=1, telegram=None), kakao


class TestNotifier:
    def test_sends_memo_when_linked(self):
        notifier, kakao = _notifier()
        assert notifier.notify("예약 성공", link_url="https://x") is True
        kakao.send_memo.assert_called_once_with(1, "예약 성공", link_url="https://x")

    def test_not_linked(self):
        notifier, kakao = _notifier(linked=False)
        assert notifier.notify("x") is False
        kakao.send_memo.assert_not_called()

    def test_disabled(self):
        notifier, kakao = _notifier(enabled=False)
        assert notifier.notify("x") is False
        kakao.is_linked.assert_not_called()

    def test_delivery_error_is_swallowed(self):
        notifier, kakao = _notifier()
        kakao.send_memo.side_effect = KakaoError("down")
        assert notifier.notify("x") is False

    def test_expired_link_is_removed(self):
        notifier, kakao = _notifier()
        kakao.send_memo.side_effect = KakaoAuthError("expired")
        assert notifier.notify("x") is False
        kakao.unlink.assert_called_once_with(1)

    def test_logs_without_kakao_service(self, caplog):
        notifier = Notifier(storage=Mock(), kakao=Mock(spec=KakaoService, enabled=False), user_id=1)
        with caplog.at_level("INFO"):
            notifier.notify("hello\nworld")
        assert "hello | world" in caplog.text

"""Unit tests for the Telegram + KakaoTalk composite notifier."""
from unittest.mock import Mock

from services.kakao_service import KakaoAuthError, KakaoError, KakaoService
from services.notification_service import NotificationService
from services.telegram_service import TelegramService, split_message


def _notifier(linked=True, enabled=True):
    telegram = Mock(spec=TelegramService)
    telegram.send_message.return_value = True
    kakao = Mock(spec=KakaoService)
    kakao.enabled = enabled
    kakao.is_linked.return_value = linked
    return NotificationService(telegram, kakao, storage=Mock()), telegram, kakao


class TestNotificationService:
    def test_send_message_is_telegram_only(self):
        notifier, telegram, kakao = _notifier()
        notifier.send_message(1, "prompt")
        telegram.send_message.assert_called_once_with(1, "prompt")
        kakao.send_memo.assert_not_called()

    def test_notification_is_mirrored_when_linked(self):
        notifier, telegram, kakao = _notifier()
        assert notifier.send_notification(1, "예약 성공") is True
        telegram.send_message.assert_called_once_with(1, "예약 성공")
        kakao.send_memo.assert_called_once()
        assert kakao.send_memo.call_args.args[:2] == (1, "예약 성공")

    def test_notification_not_mirrored_when_not_linked(self):
        notifier, telegram, kakao = _notifier(linked=False)
        notifier.send_notification(1, "예약 성공")
        telegram.send_message.assert_called_once()
        kakao.send_memo.assert_not_called()

    def test_notification_not_mirrored_when_disabled(self):
        notifier, telegram, kakao = _notifier(enabled=False)
        notifier.send_notification(1, "예약 성공")
        kakao.is_linked.assert_not_called()
        kakao.send_memo.assert_not_called()

    def test_kakao_failure_never_breaks_telegram_delivery(self):
        notifier, telegram, kakao = _notifier()
        kakao.send_memo.side_effect = KakaoError("api down")
        assert notifier.send_notification(1, "msg") is True
        telegram.send_message.assert_called_once_with(1, "msg")

    def test_expired_link_is_dropped_and_user_told(self):
        notifier, telegram, kakao = _notifier()
        kakao.send_memo.side_effect = KakaoAuthError("refresh token 만료")
        notifier.send_notification(1, "msg")
        kakao.unlink.assert_called_once_with(1)
        texts = [c.args[1] for c in telegram.send_message.call_args_list]
        assert texts[0] == "msg"
        assert any("/kakao" in t for t in texts[1:])

    def test_send_to_multiple_delegates(self):
        notifier, telegram, _ = _notifier()
        telegram.send_to_multiple.return_value = 2
        assert notifier.send_to_multiple([1, 2], "x") == 2

    def test_without_kakao_service(self):
        telegram = Mock(spec=TelegramService)
        telegram.send_message.return_value = True
        notifier = NotificationService(telegram, None, storage=Mock())
        assert notifier.send_notification(1, "x") is True


class TestTelegramSplit:
    def test_short_message_untouched(self):
        assert split_message("abc") == ["abc"]

    def test_long_message_split_under_limit(self):
        text = "\n".join("x" * 100 for _ in range(60))
        chunks = split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)
        assert "".join(chunks).replace("\n", "") == text.replace("\n", "")

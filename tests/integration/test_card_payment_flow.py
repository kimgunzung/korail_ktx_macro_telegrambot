"""Integration tests for the card payment conversation step and its hand-off (Redis-backed)."""
from unittest.mock import Mock, patch

import pytest
from flask import Flask
from flask_restful import Api

from api import TelegramWebhook
from handlers import ConversationHandler
from models import CardInfo, TrainSearchParams, UserCredentials, UserProgress, UserSession
from services import PaymentReminderService, ReservationService, TelegramService
from storage import RedisStorage


FUTURE_EXPIRE = "3512"


def _session(storage, chat_id=1, state=UserProgress.CARD_CHOICE_PENDING, **extra):
    session = UserSession(chat_id=chat_id, in_progress=True, last_action=state,
                          credentials=UserCredentials(korail_id="010-1234-5678", korail_pw="pw"))
    session.train_info = {
        'depDate': '20991231', 'srcLocate': '서울', 'dstLocate': '부산', 'depTime': '090000',
        'maxDepTime': '1800', 'trainType': 'TrainType.KTX', 'trainTypeShow': 'KTX',
        'specialInfo': 'ReserveOption.GENERAL_FIRST', 'specialInfoShow': 'GENERAL_FIRST',
        'passengerCount': 1, 'seatStrategy': 'consecutive', **extra,
    }
    storage.save_user_session(session)
    return session


class TestCardConversation:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.telegram.delete_message.return_value = True
        self.reservation = Mock(spec=ReservationService)
        self.reservation.start_reservation_process.return_value = True
        self.handler = ConversationHandler(self.storage, self.telegram, self.reservation)

    def teardown_method(self):
        self.storage.redis.flushdb()

    def _sent(self):
        return [c.args[1] for c in self.telegram.send_message.call_args_list]

    def _state(self, chat_id=1):
        return self.storage.get_user_session(chat_id).last_action

    def test_decline_goes_to_final_confirmation(self):
        _session(self.storage)
        self.handler.handle_message(1, "아니오")
        assert self._state() == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert "직접 결제 (예약 후 안내)" in self._sent()[-1]

    def test_invalid_choice_repeats_question(self):
        _session(self.storage)
        self.handler.handle_message(1, "maybe")
        assert self._state() == UserProgress.CARD_CHOICE_PENDING

    def test_full_card_flow_scrubs_messages_and_starts_with_card(self):
        _session(self.storage)
        self.handler.handle_message(1, "Y")
        assert self._state() == UserProgress.CARD_NUMBER_PENDING

        self.handler.handle_message(1, "4111-1111-1111-1111", message_id=101)
        assert self._state() == UserProgress.CARD_EXPIRE_PENDING
        assert "****1111" in self._sent()[-1]
        self.telegram.delete_message.assert_called_with(1, 101)

        self.handler.handle_message(1, f"12/{FUTURE_EXPIRE[:2]}", message_id=102)
        assert self._state() == UserProgress.CARD_PASSWORD_PENDING

        self.handler.handle_message(1, "12", message_id=103)
        assert self._state() == UserProgress.CARD_VERIFY_PENDING

        self.handler.handle_message(1, "900101", message_id=104)
        assert self._state() == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert [c.args[1] for c in self.telegram.delete_message.call_args_list] == [101, 102, 103, 104]

        # Card stored (short TTL) and the summary shows only the mask
        card = self.storage.get_card_info(1)
        assert card == CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number="900101")
        assert 0 < self.storage.redis.ttl("card_info:1") <= 3600
        summary = self._sent()[-1]
        assert "카드 자동 결제 (****1111)" in summary and "4111111111111111" not in summary

        # Final confirmation hands the card to the background process and forgets it
        self.handler.handle_message(1, "Y")
        assert self._state() == UserProgress.FINDING_TICKET
        kwargs = self.reservation.start_reservation_process.call_args.kwargs
        assert kwargs['card'] == card
        assert self.storage.get_card_info(1) is None

    def test_corporate_card(self):
        _session(self.storage, state=UserProgress.CARD_VERIFY_PENDING, cardMask="****1111")
        self.storage.save_card_info(1, CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number=""))
        self.handler.handle_message(1, "123-45-67890", message_id=5)
        assert self.storage.get_card_info(1).is_corporate is True
        assert "법인카드" in self._sent()[-2]
        assert "법인" in self._sent()[-1]

    def test_invalid_card_number_is_rejected_and_message_still_deleted(self):
        _session(self.storage, state=UserProgress.CARD_NUMBER_PENDING)
        self.handler.handle_message(1, "4111111111111112", message_id=7)
        assert self._state() == UserProgress.CARD_NUMBER_PENDING
        self.telegram.delete_message.assert_called_once_with(1, 7)
        assert self.storage.get_card_info(1) is None

    def test_delete_failure_warns_user(self):
        self.telegram.delete_message.return_value = False
        _session(self.storage, state=UserProgress.CARD_NUMBER_PENDING)
        self.handler.handle_message(1, "4111111111111111", message_id=7)
        assert any("직접 삭제" in t for t in self._sent())

    def test_expired_partial_card_restarts_card_step(self):
        _session(self.storage, state=UserProgress.CARD_EXPIRE_PENDING, cardMask="****1111")
        # no card_info in Redis (TTL expired)
        self.handler.handle_message(1, FUTURE_EXPIRE, message_id=8)
        assert self._state() == UserProgress.CARD_NUMBER_PENDING
        assert "다시 입력" in self._sent()[-1]

    def test_final_no_forgets_card(self):
        _session(self.storage, state=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, cardMask="****1111")
        self.storage.save_card_info(1, CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number="900101"))
        self.handler.handle_message(1, "N")
        assert self.storage.get_card_info(1) is None
        assert self.storage.get_user_session(1).in_progress is False

    def test_incomplete_card_at_start_falls_back_to_manual(self):
        _session(self.storage, state=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS, cardMask="****1111")
        # TTL expired: nothing in Redis
        self.handler.handle_message(1, "Y")
        kwargs = self.reservation.start_reservation_process.call_args.kwargs
        assert kwargs['card'] is None
        assert any("자동 결제 없이" in t for t in self._sent())

    def test_feature_disabled_skips_question(self):
        session = _session(self.storage, state=UserProgress.SPECIAL_INPUT_SUCCESS)
        with patch('config.settings.settings.AUTO_PAYMENT_ENABLED', False):
            self.handler.handle_message(1, "1")
        assert self._state() == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert "직접 결제" in self._sent()[-1]
        assert not any("자동 결제할까요" in t for t in self._sent())

    def test_handler_works_without_message_id(self):
        _session(self.storage, state=UserProgress.CARD_NUMBER_PENDING)
        self.handler.handle_message(1, "4111111111111111")
        self.telegram.delete_message.assert_not_called()
        assert self._state() == UserProgress.CARD_EXPIRE_PENDING
        assert any("직접 삭제" in t for t in self._sent())


class TestReservationServiceCardHandoff:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.service = ReservationService(self.storage, self.telegram)

    def teardown_method(self):
        self.storage.redis.flushdb()

    @patch('subprocess.Popen')
    def test_card_goes_through_env_only(self, mock_popen):
        mock_popen.return_value = Mock(pid=321)
        card = CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number="900101")
        params = TrainSearchParams(dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000")

        assert self.service.start_reservation_process(1, "010-1234-5678", "pw", params, card=card)

        args, kwargs = mock_popen.call_args
        assert "4111111111111111" not in " ".join(args[0])
        assert kwargs['env']['KORAIL_BOT_CARD']
        assert CardInfo.from_dict(__import__('json').loads(kwargs['env']['KORAIL_BOT_CARD'])) == card

    @patch('subprocess.Popen')
    def test_no_card_leaves_env_clean(self, mock_popen):
        mock_popen.return_value = Mock(pid=321)
        params = TrainSearchParams(dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000")
        with patch.dict('os.environ', {"KORAIL_BOT_CARD": "stale"}):
            self.service.start_reservation_process(1, "010-1234-5678", "pw", params)
        assert "KORAIL_BOT_CARD" not in mock_popen.call_args.kwargs['env']

    def test_cancel_forgets_card(self):
        self.storage.save_card_info(1, CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number="900101"))
        from models import RunningReservation
        self.storage.save_running_reservation(RunningReservation(
            chat_id=1, process_id=999999999, korail_id="x",
            search_params=TrainSearchParams(dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000")))
        self.service.cancel_reservation(1)
        assert self.storage.get_card_info(1) is None


class TestWebhookPaidCallbacks:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)
        self.multi_reminder = Mock()
        app = Flask(__name__)
        api = Api(app)
        api.add_resource(TelegramWebhook, '/telebot', resource_class_kwargs={
            'storage': self.storage,
            'telegram_service': self.telegram,
            'reservation_service': self.reservation,
            'payment_reminder_service': self.payment_reminder,
            'multi_reminder_service': self.multi_reminder,
        })
        self.client = app.test_client()

    def teardown_method(self):
        self.storage.redis.flushdb()

    def test_paid_success_skips_reminders(self):
        response = self.client.get('/telebot', query_string={
            'chatId': '1', 'msg': '결제 완료', 'status': '0', 'paid': '1'})
        assert response.status_code == 200
        self.payment_reminder.start_reminders.assert_not_called()
        self.reservation.finish_reservation.assert_called_once_with(1, success=True)

    def test_unpaid_success_starts_reminders(self):
        self.client.get('/telebot', query_string={'chatId': '1', 'msg': '예약 성공', 'status': '0', 'paid': '0'})
        self.payment_reminder.start_reminders.assert_called_once_with(1)

    def test_paid_partial_skips_multi_reminders(self):
        from datetime import datetime, timedelta
        from models import MultiReservationStatus, ReservationPaymentStatus, SingleReservationInfo
        now = datetime.now()
        self.storage.save_multi_reservation_status(MultiReservationStatus(
            chat_id=1, total_seats=2, seat_strategy="random", created_at=now,
            reservations=[SingleReservationInfo("r1", None, now, now + timedelta(minutes=10),
                                                ReservationPaymentStatus.PAID, 1, "KTX")]))
        self.client.get('/telebot', query_string={
            'chatId': '1', 'msg': '1번 좌석 결제 완료', 'status': '2', 'seatStrategy': 'random', 'paid': '1'})
        self.multi_reminder.start_reminders.assert_not_called()

    def test_secrets_are_not_logged(self, caplog):
        import logging
        self.storage.save_user_session(UserSession(chat_id=1, in_progress=True, last_action=UserProgress.CARD_NUMBER_PENDING))
        self.storage.save_user_session(UserSession(chat_id=2, in_progress=True, last_action=UserProgress.ID_INPUT_SUCCESS))
        with caplog.at_level(logging.DEBUG):
            self.client.post('/telebot', json={"message": {"message_id": 1, "chat": {"id": 1}, "text": "4111-1111-1111-1111"}})
            self.client.post('/telebot', json={"message": {"message_id": 2, "chat": {"id": 2}, "text": "my-korail-pw"}})
            self.client.post('/telebot', json={"message": {"message_id": 3, "chat": {"id": 3}, "text": "/status"}})
        assert "4111" not in caplog.text
        assert "my-korail-pw" not in caplog.text
        assert "<redacted>" in caplog.text
        assert "/status" in caplog.text

    def test_message_id_is_forwarded_to_conversation(self):
        session = UserSession(chat_id=1, in_progress=True, last_action=UserProgress.CARD_NUMBER_PENDING)
        self.storage.save_user_session(session)
        self.telegram.delete_message.return_value = True
        self.client.post('/telebot', json={"message": {"message_id": 55, "chat": {"id": 1}, "text": "4111111111111111"}})
        self.telegram.delete_message.assert_called_once_with(1, 55)

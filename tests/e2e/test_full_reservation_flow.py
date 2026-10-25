"""
End-to-end tests for complete reservation flows.

Tests the entire user journey from start to reservation completion.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from storage import RedisStorage
from services import TelegramService, ReservationService, PaymentReminderService
from handlers import CommandHandler, ConversationHandler
from models import UserProgress


class TestFullReservationFlow:
    """Test complete reservation flow end-to-end."""

    def setup_method(self):
        """Set up test fixtures."""
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)

        self.command_handler = CommandHandler(
            self.storage,
            self.telegram,
            self.reservation,
            self.payment_reminder
        )

        self.conversation_handler = ConversationHandler(
            self.storage,
            self.telegram,
            self.reservation
        )

    def teardown_method(self):
        """Clean up after each test."""
        self.storage.redis.flushdb()

    @patch('services.korail_service.KorailService.login')
    def test_complete_single_reservation_happy_path(self, mock_login):
        """Test complete single passenger reservation flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Step 1: /start command
        self.command_handler.route_command(chat_id, "/start")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.STARTED

        # Step 2: Confirm start (Y)
        self.conversation_handler.handle_message(chat_id, "Y")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.START_ACCEPTED

        # Step 3: Enter phone number
        with patch('config.settings.settings.is_user_allowed', return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.ID_INPUT_SUCCESS
        assert session.credentials.korail_id == "010-1234-5678"

        # Step 4: Enter password
        self.conversation_handler.handle_message(chat_id, "password123")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS
        assert session.credentials.korail_pw == "password123"

        # Step 5: Enter date
        self.conversation_handler.handle_message(chat_id, future_date)
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DATE_INPUT_SUCCESS
        assert session.train_info['depDate'] == future_date

        # Step 6: Enter source station
        self.conversation_handler.handle_message(chat_id, "서울")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        assert session.train_info['srcLocate'] == "서울"

        # Step 7: Enter destination station
        self.conversation_handler.handle_message(chat_id, "부산")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DST_LOCATE_INPUT_SUCCESS
        assert session.train_info['dstLocate'] == "부산"

        # Step 8: Enter departure time
        self.conversation_handler.handle_message(chat_id, "0900")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS
        assert session.train_info['depTime'] == "090000"

        # Step 9: Enter max departure time
        self.conversation_handler.handle_message(chat_id, "1800")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        assert session.train_info['maxDepTime'] == "1800"

        # Step 10: Select train type (KTX)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        assert session.train_info['trainType'] == "TrainType.KTX"

        # Step 11: Select seat option (GENERAL_FIRST)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SPECIAL_INPUT_SUCCESS

        # Step 12: Enter passenger count (1)
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info['passengerCount'] == 1
        assert session.last_action == UserProgress.CARD_CHOICE_PENDING
        # Single passenger auto-sets consecutive
        assert session.train_info['seatStrategy'] == 'consecutive'

        # Step 12-1: Decline automatic card payment
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

        # Step 13: Final confirmation (Y)
        self.conversation_handler.handle_message(chat_id, "Y")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.FINDING_TICKET

        # Verify reservation started
        self.reservation.start_reservation_process.assert_called_once()
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]['chat_id'] == chat_id
        assert call_args[1]['username'] == "010-1234-5678"
        assert call_args[1]['password'] == "password123"
        assert call_args[1]['search_params'].src_locate == "서울"
        assert call_args[1]['search_params'].dst_locate == "부산"

    @patch('services.korail_service.KorailService.login')
    def test_complete_multi_passenger_consecutive_flow(self, mock_login):
        """Test complete multi-passenger consecutive seating flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Go through flow quickly
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")

        with patch('config.settings.settings.is_user_allowed', return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)
        self.conversation_handler.handle_message(chat_id, "서울")
        self.conversation_handler.handle_message(chat_id, "부산")
        self.conversation_handler.handle_message(chat_id, "0900")
        self.conversation_handler.handle_message(chat_id, "1800")
        self.conversation_handler.handle_message(chat_id, "1")  # KTX
        self.conversation_handler.handle_message(chat_id, "1")  # GENERAL_FIRST

        # Multiple passengers
        self.conversation_handler.handle_message(chat_id, "3")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info['passengerCount'] == 3
        assert session.last_action == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS

        # Select consecutive strategy
        self.conversation_handler.handle_message(chat_id, "1")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info['seatStrategy'] == 'consecutive'
        assert session.last_action == UserProgress.CARD_CHOICE_PENDING

        # Decline automatic payment
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS

        # Final confirmation
        self.conversation_handler.handle_message(chat_id, "Y")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.FINDING_TICKET

        # Verify reservation parameters
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]['search_params'].passenger_count == 3
        assert call_args[1]['search_params'].seat_strategy == 'consecutive'

    @patch('services.korail_service.KorailService.login')
    def test_complete_multi_passenger_random_flow(self, mock_login):
        """Test complete multi-passenger random seating flow."""
        mock_login.return_value = True
        self.reservation.start_reservation_process.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Quick flow setup
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")

        with patch('config.settings.settings.is_user_allowed', return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)
        self.conversation_handler.handle_message(chat_id, "서울")
        self.conversation_handler.handle_message(chat_id, "부산")
        self.conversation_handler.handle_message(chat_id, "0900")
        self.conversation_handler.handle_message(chat_id, "1800")
        self.conversation_handler.handle_message(chat_id, "1")
        self.conversation_handler.handle_message(chat_id, "1")

        # Multiple passengers
        self.conversation_handler.handle_message(chat_id, "5")

        # Select random strategy
        self.conversation_handler.handle_message(chat_id, "2")
        session = self.storage.get_user_session(chat_id)
        assert session.train_info['seatStrategy'] == 'random'
        assert session.last_action == UserProgress.CARD_CHOICE_PENDING

        # Opt in to automatic card payment and enter the card
        self.telegram.delete_message.return_value = True
        self.conversation_handler.handle_message(chat_id, "Y")
        self.conversation_handler.handle_message(chat_id, "4111-1111-1111-1111", message_id=1)
        self.conversation_handler.handle_message(chat_id, "12/35", message_id=2)
        self.conversation_handler.handle_message(chat_id, "12", message_id=3)
        self.conversation_handler.handle_message(chat_id, "900101", message_id=4)
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert session.train_info['cardMask'] == "****1111"
        assert self.telegram.delete_message.call_count == 4

        # Final confirmation
        self.conversation_handler.handle_message(chat_id, "Y")

        # Verify reservation parameters
        call_args = self.reservation.start_reservation_process.call_args
        assert call_args[1]['search_params'].passenger_count == 5
        assert call_args[1]['search_params'].seat_strategy == 'random'
        card = call_args[1]['card']
        assert card.number == "4111111111111111" and card.expire == "3512"
        # the entry buffer is gone; the card stays saved (encrypted) for the next reservation
        assert self.storage.get_card_info(chat_id) is None
        assert self.storage.get_saved_card(chat_id).number == "4111111111111111"

    @patch('services.korail_service.KorailService.login')
    def test_flow_with_cancellation_mid_way(self, mock_login):
        """Test user cancels in the middle of flow."""
        mock_login.return_value = True

        chat_id = 12345
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")

        # Start flow
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")

        with patch('config.settings.settings.is_user_allowed', return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        self.conversation_handler.handle_message(chat_id, "password123")
        self.conversation_handler.handle_message(chat_id, future_date)

        # User decides to cancel
        self.command_handler.route_command(chat_id, "/cancel")

        # Session should be reset
        session = self.storage.get_user_session(chat_id)
        assert session.in_progress is False
        assert session.last_action == 0

    @patch('services.korail_service.KorailService.login')
    def test_flow_with_login_retry(self, mock_login):
        """Test flow with failed login and retry."""
        # First attempt fails, second succeeds
        mock_login.side_effect = [False, True]

        chat_id = 12345

        # Start flow
        self.command_handler.route_command(chat_id, "/start")
        self.conversation_handler.handle_message(chat_id, "Y")

        with patch('config.settings.settings.is_user_allowed', return_value=True):
            self.conversation_handler.handle_message(chat_id, "010-1234-5678")

        # First password attempt - fails
        self.conversation_handler.handle_message(chat_id, "wrong_password")
        session = self.storage.get_user_session(chat_id)
        # Should stay in same state for retry
        assert session.last_action == UserProgress.ID_INPUT_SUCCESS

        # Retry with correct password
        self.conversation_handler.handle_message(chat_id, "correct_password")
        session = self.storage.get_user_session(chat_id)
        # Should progress
        assert session.last_action == UserProgress.PW_INPUT_SUCCESS

    def test_flow_rejection_at_start(self):
        """Test user rejects at start confirmation."""
        chat_id = 12345

        # Start
        self.command_handler.route_command(chat_id, "/start")
        session = self.storage.get_user_session(chat_id)
        assert session.last_action == UserProgress.STARTED

        # Reject
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        # Should be reset
        assert session.in_progress is False
        assert session.last_action == 0

    def test_flow_rejection_at_final_confirmation(self):
        """Test user rejects at final confirmation."""
        chat_id = 12345

        # Create session at final confirmation stage
        from models import UserSession, UserCredentials
        session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        )
        session.credentials = UserCredentials(
            korail_id="010-1234-5678",
            korail_pw="password123"
        )
        session.train_info = {
            'depDate': '20991231',
            'srcLocate': '서울',
            'dstLocate': '부산',
            'depTime': '090000',
            'maxDepTime': '1800',
            'trainType': 'TrainType.KTX',
            'trainTypeShow': 'KTX',
            'specialInfo': 'ReserveOption.GENERAL_FIRST',
            'specialInfoShow': 'GENERAL_FIRST',
            'passengerCount': 1,
            'seatStrategy': 'consecutive'
        }
        self.storage.save_user_session(session)

        # Reject
        self.conversation_handler.handle_message(chat_id, "N")
        session = self.storage.get_user_session(chat_id)
        # Should be reset
        assert session.in_progress is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestCommandGuards:
    """/start and /cancel behaviour around a running background reservation."""

    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.reservation = Mock(spec=ReservationService)
        self.payment_reminder = Mock(spec=PaymentReminderService)
        self.command_handler = CommandHandler(
            self.storage, self.telegram, self.reservation, self.payment_reminder
        )

    def teardown_method(self):
        self.storage.redis.flushdb()

    def _running(self, chat_id=12345):
        from models import RunningReservation, TrainSearchParams
        self.storage.save_running_reservation(RunningReservation(
            chat_id=chat_id, process_id=4321, korail_id="010-1234-5678",
            search_params=TrainSearchParams(dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000")
        ))

    def test_start_refused_while_reservation_running(self):
        self._running()
        self.command_handler.route_command(12345, "/start")
        assert self.storage.get_user_session(12345) is None
        text = self.telegram.send_message.call_args.args[1]
        assert "이미 예약이 진행 중" in text and "서울" in text

    def test_cancel_sends_single_message_when_nothing_running(self):
        self.reservation.cancel_reservation.return_value = False
        self.command_handler.route_command(12345, "/cancel")
        assert self.telegram.send_message.call_count == 1
        assert "취소" in self.telegram.send_message.call_args.args[1]

    def test_cancel_relies_on_service_message_when_running(self):
        self.reservation.cancel_reservation.return_value = True
        self.command_handler.route_command(12345, "/cancel")
        self.telegram.send_message.assert_not_called()

"""Conversation flow handler for reservation process."""
from typing import Optional

from config.settings import settings
from models import UserSession, UserProgress, UserCredentials, TrainSearchParams, CardInfo
from storage.base import StorageInterface
from services import TelegramService, KorailService, ReservationService, MessageTemplates
from utils.validators import InputValidator
from utils.logger import get_logger

logger = get_logger(__name__)


class ConversationHandler:
    """Handles multi-step conversation flow for train reservation."""

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService
    ):
        """
        Initialize conversation handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service

    def handle_message(self, chat_id: int, text: str, message_id: Optional[int] = None) -> None:
        """
        Handle user message based on current conversation state.

        Args:
            chat_id: Telegram chat ID
            text: User's message text
            message_id: Telegram message id (used to scrub card details from the chat)
        """
        # Get user session
        session = self.storage.get_user_session(chat_id)
        if not session:
            logger.warning(f"No session found for chat_id={chat_id}")
            self.telegram.send_message(
                chat_id,
                "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요."
            )
            return

        # Check if already finding ticket
        if session.last_action == UserProgress.FINDING_TICKET:
            self._handle_already_processing(chat_id, session)
            return

        # Route to appropriate handler based on progress
        progress = session.last_action

        if progress == UserProgress.STARTED:
            self._handle_start_confirmation(chat_id, text, session)
        elif progress == UserProgress.START_ACCEPTED:
            self._handle_phone_input(chat_id, text, session)
        elif progress == UserProgress.ID_INPUT_SUCCESS:
            self._handle_password_input(chat_id, text, session)
        elif progress == UserProgress.PW_INPUT_SUCCESS:
            self._handle_date_input(chat_id, text, session)
        elif progress == UserProgress.DATE_INPUT_SUCCESS:
            self._handle_src_station_input(chat_id, text, session)
        elif progress == UserProgress.SRC_LOCATE_INPUT_SUCCESS:
            self._handle_dst_station_input(chat_id, text, session)
        elif progress == UserProgress.DST_LOCATE_INPUT_SUCCESS:
            self._handle_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.DEP_TIME_INPUT_SUCCESS:
            self._handle_max_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS:
            self._handle_train_type_input(chat_id, text, session)
        elif progress == UserProgress.TRAIN_TYPE_INPUT_SUCCESS:
            self._handle_special_option_input(chat_id, text, session)
        elif progress == UserProgress.SPECIAL_INPUT_SUCCESS:
            self._handle_passenger_count_input(chat_id, text, session)
        elif progress == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS:
            self._handle_seat_strategy_input(chat_id, text, session)
        elif progress == UserProgress.CARD_CHOICE_PENDING:
            self._handle_auto_payment_choice(chat_id, text, session)
        elif progress == UserProgress.CARD_NUMBER_PENDING:
            self._handle_card_number_input(chat_id, text, session, message_id)
        elif progress == UserProgress.CARD_EXPIRE_PENDING:
            self._handle_card_expire_input(chat_id, text, session, message_id)
        elif progress == UserProgress.CARD_PASSWORD_PENDING:
            self._handle_card_password_input(chat_id, text, session, message_id)
        elif progress == UserProgress.CARD_VERIFY_PENDING:
            self._handle_card_verify_input(chat_id, text, session, message_id)
        elif progress == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS:
            self._handle_final_confirmation(chat_id, text, session)
        else:
            logger.error(f"Unknown progress state: {progress}")
            self.telegram.send_message(
                chat_id,
                "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요."
            )

    def _handle_start_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle initial start confirmation (Y/N)."""
        # Check for magic admin login
        if text == settings.ADMIN_MAGIC_STRING:
            self._handle_admin_login(chat_id, session)
            return

        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.request_phone_number())
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.CANCEL_START_CONFIRMATION)
        else:
            self.telegram.send_message(chat_id, error)

    def _handle_admin_login(self, chat_id: int, session: UserSession) -> None:
        """Handle magic admin login."""
        username = settings.KORAIL_ADMIN_USER_ID
        password = settings.KORAIL_ADMIN_PASSWORD

        if not username or not password:
            session.reset()
            self.storage.save_user_session(session)
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_ENV)
            return

        # Try login
        korail = KorailService()
        if korail.login(username, password):
            session.credentials = UserCredentials(korail_id=username, korail_pw=password)
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.login_success())
        else:
            session.reset()
            self.storage.save_user_session(session)
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_LOGIN)

    def _handle_phone_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle phone number input."""
        is_valid, error = InputValidator.validate_phone_number(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error + " 다시 입력 바랍니다.")
            return

        # Check allow list
        if not settings.is_user_allowed(text):
            # Notify subscribers
            subscribers = self.storage.get_all_subscribers()
            self.telegram.send_to_multiple(
                subscribers,
                f"{text}가 구독자 목록에 없어서 실행에 실패했음."
            )

            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.not_in_allow_list())
            return

        # Save phone number
        if not session.credentials:
            session.credentials = UserCredentials(korail_id=text, korail_pw="")
        else:
            session.credentials.korail_id = text

        session.last_action = UserProgress.ID_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_password())

    def _handle_password_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle password input and login."""
        # After a failed login the user may answer Y (re-enter account) or N (cancel),
        # as offered by LOGIN_FAILED_RETRY. Real passwords are at least 4 characters,
        # so Y/N/예/아니오 can never be mistaken for one.
        if session.train_info.get('loginFailed'):
            is_yes, _ = InputValidator.validate_yes_no(text)
            if is_yes is True:
                session.train_info.pop('loginFailed', None)
                session.last_action = UserProgress.START_ACCEPTED
                self.storage.save_user_session(session)
                self.telegram.send_message(chat_id, MessageTemplates.request_phone_number())
                return
            if is_yes is False:
                session.reset()
                self.storage.save_user_session(session)
                from telegramBot.messages import Messages
                self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER)
                return

        # Validate password
        is_valid, error = InputValidator.validate_password(text)
        if not is_valid:
            self.telegram.send_message(chat_id, error + " 다시 입력 바랍니다.")
            return

        username = session.credentials.korail_id
        password = text

        # Update credentials
        session.credentials.korail_pw = password
        self.storage.save_user_session(session)

        # Try login
        korail = KorailService()
        if korail.login(username, password):
            session.train_info.pop('loginFailed', None)
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.login_success())
        else:
            # Login failed - ask for retry, keep state so the next input is tried again
            session.train_info['loginFailed'] = True
            self.storage.save_user_session(session)
            failure = MessageTemplates.login_failure(username)
            if korail.last_login_error:
                failure += f"\n서버 응답: {korail.last_login_error}"
            self.telegram.send_message(chat_id, failure)

    def _handle_date_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure date input."""
        is_valid, error = InputValidator.validate_date(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id,
                f"{error}\n예매 희망일 8자를 입력해주십시오.\n(ex_ 20210124) <- 2021년 1월 24일"
            )
            return

        session.train_info['depDate'] = text
        session.last_action = UserProgress.DATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_departure_station())

    def _handle_src_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle source station input."""
        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        session.train_info['srcLocate'] = text
        session.last_action = UserProgress.SRC_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_arrival_station())

    def _handle_dst_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle destination station input."""
        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        session.train_info['dstLocate'] = text
        session.last_action = UserProgress.DST_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from telegramBot.messages import Messages
        self.telegram.send_message(chat_id, Messages.REQUEST_DST_STATION)

    def _handle_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure time input."""
        is_valid, error = InputValidator.validate_time(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        session.train_info['depTime'] = text + "00"  # Add seconds
        session.last_action = UserProgress.DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from telegramBot.messages import Messages
        self.telegram.send_message(chat_id, Messages.REQUEST_DEP_TIME)

    def _handle_max_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle max departure time input."""
        # Allow 2400 as special value
        if text == "2400":
            is_valid = True
        else:
            is_valid, error = InputValidator.validate_time(text)
            if not is_valid:
                self.telegram.send_message(chat_id, error)
                return

        dep_time = session.train_info.get('depTime', '000000')[:4]
        if text != "2400" and text < dep_time:
            self.telegram.send_message(
                chat_id,
                f"❌ 검색 종료 시각({text})이 검색 시작 시각({dep_time})보다 빠릅니다.\n"
                f"시작 시각 이후의 시각을 입력하거나 2400을 입력해주세요."
            )
            return

        session.train_info['maxDepTime'] = text
        session.last_action = UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from telegramBot.messages import Messages
        self.telegram.send_message(chat_id, Messages.REQUEST_TRAIN_TYPE)

    def _handle_train_type_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle train type selection."""
        is_valid, error = InputValidator.validate_train_type_choice(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        if text == "1":
            session.train_info['trainType'] = "TrainType.KTX"
            session.train_info['trainTypeShow'] = "KTX"
        else:
            session.train_info['trainType'] = "TrainType.ALL"
            session.train_info['trainTypeShow'] = "ALL"

        session.last_action = UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        from telegramBot.messages import Messages
        self.telegram.send_message(chat_id, Messages.REQUEST_SEAT_TYPE)

    def _handle_special_option_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle special seat option selection."""
        is_valid, error = InputValidator.validate_special_option_choice(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        option_map = {
            "1": "GENERAL_FIRST",
            "2": "GENERAL_ONLY",
            "3": "SPECIAL_FIRST",
            "4": "SPECIAL_ONLY",
        }

        option_display = option_map[text]
        # Stored as "ReserveOption.<NAME>"; the background process parses this string.
        session.train_info['specialInfo'] = f"ReserveOption.{option_display}"
        session.train_info['specialInfoShow'] = option_display

        session.last_action = UserProgress.SPECIAL_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for passenger count
        from telegramBot.messages import Messages
        self.telegram.send_message(chat_id, Messages.REQUEST_PASSENGER_COUNT)

    def _handle_passenger_count_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle passenger count input."""
        # Validate input with enhanced validator
        is_valid, error = InputValidator.validate_passenger_count(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        count = int(text)

        # Save passenger count
        session.train_info['passengerCount'] = count
        session.last_action = UserProgress.PASSENGER_COUNT_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for seat strategy if more than 1 passenger
        if count > 1:
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.REQUEST_SEAT_STRATEGY.format(count=count))
        else:
            # Single passenger, skip seat strategy
            session.train_info['seatStrategy'] = 'consecutive'
            self._ask_auto_payment(chat_id, session)

    def _handle_seat_strategy_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle seat strategy selection."""
        # Validate with enhanced validator
        is_valid, error = InputValidator.validate_seat_strategy_choice(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        strategy = "consecutive" if text == "1" else "random"
        strategy_display = "연속 좌석" if text == "1" else "랜덤 배치"

        session.train_info['seatStrategy'] = strategy
        session.train_info['seatStrategyShow'] = strategy_display
        self._ask_auto_payment(chat_id, session)

    # ==================== Automatic card payment ====================

    def _ask_auto_payment(self, chat_id: int, session: UserSession) -> None:
        """Offer automatic card payment, or go straight to the final confirmation."""
        from telegramBot.messages import Messages
        self.storage.delete_card_info(chat_id)
        session.train_info.pop('cardMask', None)
        session.train_info.pop('cardCorporate', None)

        if not settings.AUTO_PAYMENT_ENABLED:
            session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self._show_final_confirmation(chat_id, session)
            return

        session.last_action = UserProgress.CARD_CHOICE_PENDING
        self.storage.save_user_session(session)
        self.telegram.send_message(
            chat_id, Messages.REQUEST_AUTO_PAYMENT.format(minutes=settings.PAYMENT_TIMEOUT_MINUTES)
        )

    def _handle_auto_payment_choice(self, chat_id: int, text: str, session: UserSession) -> None:
        """Y -> collect card details, N -> manual payment."""
        from telegramBot.messages import Messages
        is_yes, error = InputValidator.validate_yes_no(text)
        if is_yes is True:
            session.last_action = UserProgress.CARD_NUMBER_PENDING
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.REQUEST_CARD_NUMBER)
        elif is_yes is False:
            session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id, Messages.AUTO_PAYMENT_SKIPPED.format(minutes=settings.PAYMENT_TIMEOUT_MINUTES)
            )
            self._show_final_confirmation(chat_id, session)
        else:
            self.telegram.send_message(chat_id, error)

    def _scrub_message(self, chat_id: int, message_id: Optional[int]) -> None:
        """Delete the user's message holding card details; warn if that is not possible."""
        from telegramBot.messages import Messages
        delete = getattr(self.telegram, "delete_message", None)
        deleted = False
        if message_id is not None and callable(delete):
            try:
                deleted = bool(delete(chat_id, message_id))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"delete_message failed for chat_id={chat_id}: {e}")
        if not deleted:
            self.telegram.send_message(chat_id, Messages.CARD_MESSAGE_DELETE_FAILED)

    def _handle_card_number_input(self, chat_id: int, text: str, session: UserSession,
                                  message_id: Optional[int]) -> None:
        from telegramBot.messages import Messages
        is_valid, error = InputValidator.validate_card_number(text)
        self._scrub_message(chat_id, message_id)
        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        number = InputValidator.normalize_card_number(text)
        self.storage.save_card_info(chat_id, CardInfo(number=number, expire="", password="", verify_number=""))
        session.train_info['cardMask'] = f"****{number[-4:]}"
        session.last_action = UserProgress.CARD_EXPIRE_PENDING
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, Messages.REQUEST_CARD_EXPIRE.format(masked=session.train_info['cardMask']))

    def _pending_card(self, chat_id: int, session: UserSession) -> Optional[CardInfo]:
        """Card being collected; None (and restart of the card step) when the TTL expired."""
        from telegramBot.messages import Messages
        card = self.storage.get_card_info(chat_id)
        if card is None:
            session.last_action = UserProgress.CARD_NUMBER_PENDING
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, "⚠️ 카드 정보 입력 시간이 지났습니다. 처음부터 다시 입력해주세요.\n\n" + Messages.REQUEST_CARD_NUMBER)
        return card

    def _handle_card_expire_input(self, chat_id: int, text: str, session: UserSession,
                                  message_id: Optional[int]) -> None:
        from telegramBot.messages import Messages
        is_valid, error = InputValidator.validate_card_expire(text)
        self._scrub_message(chat_id, message_id)
        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return
        card = self._pending_card(chat_id, session)
        if card is None:
            return
        card.expire = InputValidator.normalize_card_expire(text)
        self.storage.save_card_info(chat_id, card)
        session.last_action = UserProgress.CARD_PASSWORD_PENDING
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, Messages.REQUEST_CARD_PASSWORD)

    def _handle_card_password_input(self, chat_id: int, text: str, session: UserSession,
                                    message_id: Optional[int]) -> None:
        from telegramBot.messages import Messages
        is_valid, error = InputValidator.validate_card_password(text)
        self._scrub_message(chat_id, message_id)
        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return
        card = self._pending_card(chat_id, session)
        if card is None:
            return
        card.password = text.strip()
        self.storage.save_card_info(chat_id, card)
        session.last_action = UserProgress.CARD_VERIFY_PENDING
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, Messages.REQUEST_CARD_VERIFY)

    def _handle_card_verify_input(self, chat_id: int, text: str, session: UserSession,
                                  message_id: Optional[int]) -> None:
        from telegramBot.messages import Messages
        is_valid, error = InputValidator.validate_card_verify_number(text)
        self._scrub_message(chat_id, message_id)
        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return
        card = self._pending_card(chat_id, session)
        if card is None:
            return
        card.verify_number = text.strip().replace("-", "")
        self.storage.save_card_info(chat_id, card)
        session.train_info['cardCorporate'] = card.is_corporate
        session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, Messages.CARD_REGISTERED.format(
            masked=card.masked, corporate=", 법인카드" if card.is_corporate else ""
        ))
        self._show_final_confirmation(chat_id, session)

    def _show_final_confirmation(self, chat_id: int, session: UserSession) -> None:
        """Show final confirmation summary."""
        passenger_count = session.train_info.get('passengerCount', 1)
        seat_strategy_display = session.train_info.get('seatStrategyShow', '1명')

        from telegramBot.messages import Messages
        info = session.train_info
        if info.get('cardMask'):
            payment = f"카드 자동 결제 ({info['cardMask']}{', 법인' if info.get('cardCorporate') else ''})"
        else:
            payment = "직접 결제 (예약 후 안내)"
        summary = Messages.CONFIRM_RESERVATION.format(
            payment=payment,
            depDate=info.get('depDate', 'N/A'),
            srcLocate=info.get('srcLocate', 'N/A'),
            dstLocate=info.get('dstLocate', 'N/A'),
            depTime=info.get('depTime', 'N/A')[:4],
            maxDepTime=info.get('maxDepTime', 'N/A'),
            trainTypeShow=info.get('trainTypeShow', 'N/A'),
            specialInfoShow=info.get('specialInfoShow', 'N/A'),
            passengerCount=passenger_count,
            seatStrategy=seat_strategy_display
        )
        self.telegram.send_message(chat_id, summary)

    def _handle_final_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle final confirmation before starting reservation."""
        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            # Start reservation process
            self._start_reservation(chat_id, session)
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            self.storage.delete_card_info(chat_id)
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER)
        else:
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.ERROR_CONFIRM_INVALID)

    def _start_reservation(self, chat_id: int, session: UserSession) -> None:
        """Start the reservation background process."""
        # Create search params
        search_params = TrainSearchParams(
            dep_date=session.train_info['depDate'],
            src_locate=session.train_info['srcLocate'],
            dst_locate=session.train_info['dstLocate'],
            dep_time=session.train_info['depTime'],
            max_dep_time=session.train_info['maxDepTime'],
            train_type=session.train_info['trainType'],
            train_type_display=session.train_info['trainTypeShow'],
            special_option=session.train_info['specialInfo'],
            special_option_display=session.train_info['specialInfoShow'],
            passenger_count=session.train_info.get('passengerCount', 1),
            seat_strategy=session.train_info.get('seatStrategy', 'consecutive')
        )

        # Card for automatic payment (if registered). Complete cards only; a half-entered
        # card (TTL expiry mid-conversation) is dropped and the user pays manually.
        card = self.storage.get_card_info(chat_id) if session.train_info.get('cardMask') else None
        if card and not (card.expire and card.password and card.verify_number):
            card = None
        if session.train_info.get('cardMask') and card is None:
            self.telegram.send_message(
                chat_id, "⚠️ 카드 정보가 만료되어 자동 결제 없이 진행합니다. 예약 후 직접 결제해주세요."
            )
            session.train_info.pop('cardMask', None)

        # Update session
        session.last_action = UserProgress.FINDING_TICKET
        self.storage.save_user_session(session)

        # Start reservation
        success = self.reservation.start_reservation_process(
            chat_id=chat_id,
            username=session.credentials.korail_id,
            password=session.credentials.korail_pw,
            search_params=search_params,
            card=card
        )

        # The background process now holds the card in memory; nothing else needs it.
        self.storage.delete_card_info(chat_id)

        if not success:
            logger.error(f"Failed to start reservation for chat_id={chat_id}")
            session.reset()
            self.storage.save_user_session(session)
            from telegramBot.messages import Messages
            self.telegram.send_message(chat_id, Messages.ERROR_RESERVATION_START_FAILED)

    def _handle_already_processing(self, chat_id: int, session: UserSession) -> None:
        """Handle message when reservation is already in progress."""
        info = session.train_info
        from telegramBot.messages import Messages
        message = Messages.ALREADY_RUNNING.format(
            depDate=info.get('depDate', 'N/A'),
            srcLocate=info.get('srcLocate', 'N/A'),
            dstLocate=info.get('dstLocate', 'N/A'),
            depTime=info.get('depTime', 'N/A')[:4] if info.get('depTime') else 'N/A',
            trainTypeShow=info.get('trainTypeShow', 'N/A'),
            specialInfoShow=info.get('specialInfoShow', 'N/A')
        )
        self.telegram.send_message(chat_id, message)

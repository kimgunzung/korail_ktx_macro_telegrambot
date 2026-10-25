"""
Background process for train reservation.

This module is executed as a subprocess to continuously search for
and attempt to reserve trains. It has been refactored to use the new
service architecture while maintaining backward compatibility.
"""
import json
import sys
import time
import requests
from datetime import datetime, timedelta
from typing import Optional, Tuple

# Add src to path
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
sys.path.insert(0, src_dir)

from config.settings import settings
from storage.redis import RedisStorage
from services import (
    KorailService, TelegramService, PaymentReminderService, MultiReservationReminderService,
    KakaoService, NotificationService,
)
from services.korail_service import (
    DuplicateReservationError, KorailAccessRestrictedError, PaymentError, TrainType, ReserveOption
)
from models import MultiReservationStatus, SingleReservationInfo, ReservationPaymentStatus, CardInfo
from utils.logger import get_logger, LoggerFactory

logger = get_logger(__name__)

# Set recursion limit
sys.setrecursionlimit(settings.RECURSION_LIMIT)


class BackgroundReservationProcess:
    """Background process for train reservation."""

    def __init__(self):
        """Initialize from command line arguments."""
        if len(sys.argv) < 11:
            logger.error("Insufficient arguments")
            sys.exit(1)

        self.username = sys.argv[1]
        # The web app hands the password over through the environment so it does not
        # appear in `ps`; the argv slot ("-") is kept for backward compatibility.
        self.password = os.environ.get("KORAIL_BOT_PASSWORD") or sys.argv[2]
        if self.password == "-":
            logger.error("No Korail password provided (KORAIL_BOT_PASSWORD not set)")
            sys.exit(1)
        self.dep_date = sys.argv[3]
        self.src_locate = sys.argv[4]
        self.dst_locate = sys.argv[5]
        self.dep_time = sys.argv[6]
        self.train_type_str = sys.argv[7]
        self.special_info_str = sys.argv[8]
        self.chat_id = sys.argv[9]
        self.max_dep_time = sys.argv[10]

        # New parameters with defaults for backward compatibility
        self.passenger_count = int(sys.argv[11]) if len(sys.argv) > 11 else 1
        self.seat_strategy = sys.argv[12] if len(sys.argv) > 12 else "consecutive"

        # Card for automatic payment (JSON via environment; never logged)
        self.card: Optional[CardInfo] = self._load_card_from_env()

        # Parse train type
        self.train_type = self._parse_train_type(self.train_type_str)
        self.reserve_option = self._parse_reserve_option(self.special_info_str)

        # Initialize services
        self.storage = RedisStorage()
        self.telegram = NotificationService(
            TelegramService(settings.TELEGRAM_BOT_TOKEN),
            KakaoService(self.storage),
            self.storage,
        )
        self.payment_reminder = PaymentReminderService(self.storage, self.telegram)
        self.multi_reminder = MultiReservationReminderService(self.storage, self.telegram)
        self.korail = KorailService()

        logger.info(f"Redis storage connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")

        # Restore debug mode from Redis
        if self.storage.is_debug_mode():
            LoggerFactory.set_log_level("DEBUG")
            logger.info("Debug mode restored from Redis - log level set to DEBUG")

        logger.info(f"========================================")
        logger.info(f"Background Process Initialized")
        logger.info(f"========================================")
        logger.info(f"  chat_id: {self.chat_id}")
        logger.info(f"  username: {self.username}")
        logger.info(f"  dep_date: '{self.dep_date}'")
        logger.info(f"  src_locate: '{self.src_locate}'")
        logger.info(f"  dst_locate: '{self.dst_locate}'")
        logger.info(f"  dep_time: '{self.dep_time}'")
        logger.info(f"  max_dep_time: '{self.max_dep_time}'")
        logger.info(f"  train_type_str: '{self.train_type_str}' -> {self.train_type}")
        logger.info(f"  special_info_str: '{self.special_info_str}' -> {self.reserve_option}")
        logger.info(f"  passenger_count: {self.passenger_count}")
        logger.info(f"  seat_strategy: '{self.seat_strategy}'")
        logger.info(f"  auto_payment: {self.card.masked if self.card else 'off'}")
        logger.info(f"========================================")

    @staticmethod
    def _load_card_from_env() -> Optional[CardInfo]:
        raw = os.environ.pop("KORAIL_BOT_CARD", None)
        if not raw:
            return None
        try:
            return CardInfo.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Ignoring malformed KORAIL_BOT_CARD: {type(e).__name__}")
            return None

    def _parse_train_type(self, train_type_str: str) -> TrainType:
        """Parse train type from string."""
        # Check for exact string representation of enum
        if "TrainType.KTX" in train_type_str:
            return TrainType.KTX
        elif "TrainType.ALL" in train_type_str:
            return TrainType.ALL
        # Check for numeric values (backward compatibility)
        elif train_type_str == "100":  # KTX value
            return TrainType.KTX
        elif train_type_str == "0":  # ALL value
            return TrainType.ALL
        # Fallback to checking for keywords
        elif "KTX" in train_type_str.upper() and "ALL" not in train_type_str.upper():
            return TrainType.KTX
        else:
            return TrainType.ALL

    def _parse_reserve_option(self, option_str: str) -> ReserveOption:
        """Parse reserve option from string."""
        # Check for exact string representation of enum
        if "ReserveOption.GENERAL_FIRST" in option_str:
            return ReserveOption.GENERAL_FIRST
        elif "ReserveOption.GENERAL_ONLY" in option_str:
            return ReserveOption.GENERAL_ONLY
        elif "ReserveOption.SPECIAL_FIRST" in option_str:
            return ReserveOption.SPECIAL_FIRST
        elif "ReserveOption.SPECIAL_ONLY" in option_str:
            return ReserveOption.SPECIAL_ONLY

        # Fallback to checking for keywords
        option_str_upper = option_str.upper()
        if "GENERAL_FIRST" in option_str_upper:
            return ReserveOption.GENERAL_FIRST
        elif "GENERAL_ONLY" in option_str_upper:
            return ReserveOption.GENERAL_ONLY
        elif "SPECIAL_FIRST" in option_str_upper:
            return ReserveOption.SPECIAL_FIRST
        elif "SPECIAL_ONLY" in option_str_upper:
            return ReserveOption.SPECIAL_ONLY
        else:
            return ReserveOption.GENERAL_FIRST

    def run(self):
        """Run the reservation process."""
        try:
            logger.info(f"Logging in as {self.username}...")

            # Login
            if not self.korail.login(self.username, self.password):
                logger.error("Login failed")
                message = f"""
❌ 코레일 로그인 실패

사유: {self.korail.last_login_error or '알 수 없음'}

아이디/비밀번호가 올바르지 않거나 코레일 서버에 문제가 있습니다.

💡 조치 방법:
1. 코레일 회원번호를 확인하세요
2. 비밀번호가 올바른지 확인하세요
3. 코레일 사이트에서 직접 로그인을 시도해보세요
4. 계정이 잠기지 않았는지 확인하세요

🔗 코레일 로그인: {settings.KORAIL_PAYMENT_URL}

정보 수정이 필요하면 /cancel 후 다시 시작하세요.
"""
                self._send_callback(message, status=1)
                return

            logger.info("Login successful, starting reservation loop...")

            # Check seat strategy
            if self.seat_strategy == "random":
                # Random seating: reserve one seat at a time with payment confirmation
                self._run_random_reservation()
                return

            # Consecutive seating: original logic
            # Search and reserve
            reservation = None
            try:
                reservation = self.korail.search_and_reserve_loop(
                    dep_date=self.dep_date,
                    src_locate=self.src_locate,
                    dst_locate=self.dst_locate,
                    dep_time=self.dep_time,
                    max_dep_time=self.max_dep_time,
                    train_type=self.train_type,
                    reserve_option=self.reserve_option,
                    passenger_count=self.passenger_count,
                    seat_strategy=self.seat_strategy
                )
            except DuplicateReservationError as e:
                # First duplicate detection - notify user but continue searching
                logger.warning(f"Duplicate reservation detected (first time): {e}")
                message = f"""
⚠️ 기존 예약 감지

이미 동일한 열차에 대한 예약이 존재합니다.

🔄 기존 예약이 취소될 때까지 대기하면서 계속 검색합니다...

🔗 기존 예약 확인: {settings.KORAIL_PAYMENT_URL}

💡 검색을 중단하려면 /cancel 명령어를 사용하세요.
💡 기존 예약을 취소하면 자동으로 새 예약을 시도합니다.
"""
                # Send notification but DON'T stop the process
                self._send_callback(message, status=2)  # status=2 for warning/info

                # Continue the reservation loop (retry)
                logger.info("Continuing search after duplicate detection...")
                try:
                    reservation = self.korail.search_and_reserve_loop(
                        dep_date=self.dep_date,
                        src_locate=self.src_locate,
                        dst_locate=self.dst_locate,
                        dep_time=self.dep_time,
                        max_dep_time=self.max_dep_time,
                        train_type=self.train_type,
                        reserve_option=self.reserve_option,
                        passenger_count=self.passenger_count,
                        seat_strategy=self.seat_strategy
                    )
                except DuplicateReservationError:
                    # Should not happen as we already notified, but handle gracefully
                    logger.error("Duplicate error raised again - this shouldn't happen")
                    pass
            except KorailAccessRestrictedError as e:
                logger.error(f"Korail access restricted, stopping search: {e}")
                message = f"""
🚫 코레일 이용 제한

코레일이 현재 네트워크 환경에서 오는 요청을 거부했습니다.

서버 메시지: {str(e)}

💡 조치 방법:
1. VPN을 끄고 가정용 회선에서 실행하세요
2. 같은 IP에서 반복 시도하면 차단이 길어집니다
3. 공유기 재접속 등으로 공인 IP가 바뀐 뒤 /cancel 후 다시 시작하세요
"""
                self._send_callback(message, status=1)
                return
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error during reservation: {e}")
                message = f"""
🌐 네트워크 오류

코레일 서버와 통신 중 오류가 발생했습니다.

오류 내용: {str(e)}

💡 조치 방법:
1. 인터넷 연결을 확인하세요
2. 잠시 후 다시 시도하세요 (/cancel 후 /start)
3. 코레일 서버가 점검 중일 수 있습니다

🔗 코레일 사이트 상태 확인: {settings.KORAIL_PAYMENT_URL}
"""
                self._send_callback(message, status=1)
                return
            except ValueError as e:
                logger.error(f"Invalid data during reservation: {e}")
                message = f"""
⚠️ 입력 데이터 오류

입력하신 정보에 문제가 있습니다.

오류 내용: {str(e)}

💡 조치 방법:
1. 역 이름을 확인하세요 (예: 서울, 부산)
2. 날짜 형식을 확인하세요 (YYYYMMDD)
3. 시간 형식을 확인하세요 (HHMMSS)
4. /cancel 후 정확한 정보로 다시 시도하세요
"""
                self._send_callback(message, status=1)
                return
            except Exception as e:
                # Catch any other unexpected errors from the loop
                logger.error(f"Unexpected error in reservation loop: {e}", exc_info=True)
                message = f"""
❌ 예약 검색 중 예상치 못한 오류

오류 유형: {type(e).__name__}
오류 내용: {str(e)}

💡 조치 방법:
1. /cancel 후 다시 시도하세요
2. 문제가 계속되면 관리자에게 문의하세요

로그에 자세한 정보가 기록되었습니다.
"""
                self._send_callback(message, status=1)
                return

            if reservation:
                logger.info(f"Reservation successful: {reservation}")

                # Check if this is a random allocation with multiple reservations
                is_random = hasattr(reservation, '_is_random_allocation') and reservation._is_random_allocation
                total_seats = getattr(reservation, '_total_seats', self.passenger_count)

                # Automatic card payment (consecutive seating = one reservation)
                paid, payment_text = False, ""
                if self.card and not is_random:
                    paid, payment_text = self._try_auto_payment(reservation)

                # Build success message
                if is_random and total_seats > 1:
                    all_reservations = getattr(reservation, '_all_reservations', [reservation])
                    reservation_details = "\n".join([f"좌석 {i+1}: {res}" for i, res in enumerate(all_reservations)])
                    message = f"""
🎉 열차 예약에 성공했습니다!!

총 {total_seats}명의 좌석이 개별적으로 예약되었습니다.
(랜덤 배치 옵션: 좌석이 떨어져 있을 수 있습니다)

예약에 성공한 열차 정보는 다음과 같습니다.
===================
{reservation_details}
===================

⚠️ 중요: {settings.PAYMENT_TIMEOUT_MINUTES}분내에 사이트에서 결제를 완료하지 않으면 예약이 취소됩니다!

💡 결제 완료 후 아무 메시지나 입력하시면 리마인더 알림이 중단됩니다.
🔗 결제 링크: {settings.KORAIL_PAYMENT_URL}
"""

                    # Create MultiReservationStatus for smart reminders
                    try:
                        self._create_multi_reservation_status(all_reservations, total_seats)
                    except Exception as e:
                        logger.error(f"Failed to create multi-reservation status: {e}", exc_info=True)
                        # Non-critical error - reservation succeeded, just reminder setup failed
                        # Continue with callback

                else:
                    seats_text = f"{self.passenger_count}명" if self.passenger_count > 1 else ""
                    consecutive_text = " (연속된 좌석)" if self.passenger_count > 1 else ""
                    message = f"""
🎉 열차 예약에 성공했습니다!!

{seats_text}{consecutive_text}

예약에 성공한 열차 정보는 다음과 같습니다.
===================
{reservation}
===================

{payment_text or self._manual_payment_text()}
"""

                # Send callback with reservation metadata
                self._send_callback(
                    message,
                    status=0,
                    is_multi=is_random and total_seats > 1,
                    total_seats=total_seats,
                    seat_strategy=self.seat_strategy,
                    paid=paid
                )

                # Note: Payment reminders will be started by main app after receiving callback
                # (subprocess and main app don't share memory, so reminders must start in main app)

            else:
                logger.warning("Reservation failed - no result")
                message = """
알수 없는 오류로 예매에 실패했습니다. 처음부터 다시 시도해주세요.

[문제가 없는데 계속 반복되는 경우, 이미 해당 열차가 예매가 되었을 수 있습니다. 사이트를 확인해주세요.]
"""
                self._send_callback(message, status=1)

        except Exception as e:
            logger.error(f"Error in reservation process: {e}", exc_info=True)

            # Build detailed error message
            error_type = type(e).__name__
            error_msg = str(e)

            message = f"""
❌ 예약 프로세스 오류 발생

오류 유형: {error_type}
오류 내용: {error_msg}

📋 상황:
- 출발일: {self.dep_date}
- 출발역: {self.src_locate}
- 도착역: {self.dst_locate}
- 출발시각: {self.dep_time}

💡 조치 방법:
1. 인터넷 연결 상태를 확인하세요
2. 코레일 계정 정보가 올바른지 확인하세요
3. 코레일 사이트가 정상 작동하는지 확인하세요
4. /cancel 후 다시 시도하세요

🔗 코레일 사이트 확인: {settings.KORAIL_PAYMENT_URL}
"""
            self._send_callback(message, status=1)

        logger.info(f"Reservation process ended for {self.username}")

    # ------------------------------------------------------------ auto payment

    def _manual_payment_text(self) -> str:
        return (
            f"⚠️ 중요: {settings.PAYMENT_TIMEOUT_MINUTES}분내에 사이트에서 결제를 완료하지 않으면 예약이 취소됩니다!\n\n"
            f"💡 결제 완료 후 아무 메시지나 입력하시면 리마인더 알림이 중단됩니다.\n"
            f"🔗 결제 링크: {settings.KORAIL_PAYMENT_URL}"
        )

    def _try_auto_payment(self, reservation) -> Tuple[bool, str]:
        """
        Pay ``reservation`` with the registered card.

        Returns:
            (paid, text) where ``text`` is the payment section of the user message:
            a receipt on success, or the failure reason plus manual payment instructions.
        """
        if not self.card:
            return False, ""
        try:
            price = self.korail.pay_reservation(reservation, self.card)
        except KorailAccessRestrictedError as e:
            logger.error(f"Payment blocked by Korail access restriction: {e}")
            reason = f"코레일 이용 제한: {e}"
        except PaymentError as e:
            logger.warning(f"Automatic payment failed: {e}")
            reason = str(e)
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Unexpected auto payment error: {e}", exc_info=True)
            reason = f"{type(e).__name__}: {e}"
        else:
            amount = f"{price:,}원" if price else "금액 미확인"
            return True, (
                f"💳 자동 결제 완료! (카드 {self.card.masked}, {amount})\n\n"
                f"승차권이 발권되었습니다. 코레일 앱/홈페이지의 '승차권 확인'에서 확인하세요.\n"
                f"🔗 {settings.KORAIL_PAYMENT_URL}"
            )

        return False, (
            f"❌ 자동 결제 실패 (카드 {self.card.masked})\n"
            f"사유: {reason}\n\n"
            f"예약은 유지되어 있으니 직접 결제해주세요.\n\n"
            f"{self._manual_payment_text()}"
        )

    def _update_multi_reservation_status(self, seat_index: int, reservation, total_seats: int,
                                         paid: bool = False) -> None:
        """
        Create or update MultiReservationStatus for tracking individual seat payment.

        Called after each seat is reserved in random allocation mode.

        Args:
            seat_index: Index of the seat just reserved (0-based)
            reservation: Reservation object from pykorail
            total_seats: Total number of seats being reserved
        """
        try:
            now = datetime.now()
            expires_at = now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)

            # Get existing status or create new
            multi_status = self.storage.get_multi_reservation_status(self.chat_id)

            if not multi_status or seat_index == 0:
                # First seat - delete any old status and create fresh one
                if seat_index == 0 and multi_status:
                    logger.info(f"Deleting old MultiReservationStatus for chat_id={self.chat_id}")
                    self.storage.delete_multi_reservation_status(self.chat_id)

                logger.info(f"Creating new MultiReservationStatus for chat_id={self.chat_id}")
                multi_status = MultiReservationStatus(
                    chat_id=int(self.chat_id),
                    reservations=[],
                    total_seats=total_seats,
                    seat_strategy=self.seat_strategy,
                    created_at=now,
                    manually_stopped=False
                )

            # Add this reservation
            rsv_id = getattr(reservation, 'rsv_id', f"seat_{seat_index + 1}")
            info = SingleReservationInfo(
                reservation_id=rsv_id,
                reservation_obj=reservation,
                reserved_at=now,
                expires_at=expires_at,
                status=ReservationPaymentStatus.PAID if paid else ReservationPaymentStatus.PENDING,
                seat_number=seat_index + 1,
                train_info=str(reservation)
            )
            multi_status.reservations.append(info)

            # Save to storage
            self.storage.save_multi_reservation_status(multi_status)
            logger.info(
                f"Updated MultiReservationStatus: {len(multi_status.reservations)}/{total_seats} seats"
            )

        except Exception as e:
            logger.error(f"Failed to update MultiReservationStatus: {e}", exc_info=True)

    def _create_multi_reservation_status(self, all_reservations: list, total_seats: int) -> None:
        """
        Create MultiReservationStatus for tracking individual seat payment.
        (Legacy method - kept for compatibility)

        Args:
            all_reservations: List of reservation objects from pykorail
            total_seats: Total number of seats reserved
        """
        try:
            now = datetime.now()
            expires_at = now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)

            # Create SingleReservationInfo for each reservation
            reservation_infos = []
            for i, res in enumerate(all_reservations):
                # Extract reservation ID (try to get from korail2 object)
                rsv_id = getattr(res, 'rsv_id', f"unknown_{i+1}")

                info = SingleReservationInfo(
                    reservation_id=rsv_id,
                    reservation_obj=res,
                    reserved_at=now,
                    expires_at=expires_at,
                    status=ReservationPaymentStatus.PENDING,
                    seat_number=i + 1,
                    train_info=str(res)
                )
                reservation_infos.append(info)

            # Create MultiReservationStatus
            multi_status = MultiReservationStatus(
                chat_id=int(self.chat_id),
                reservations=reservation_infos,
                total_seats=total_seats,
                seat_strategy=self.seat_strategy,
                created_at=now,
                manually_stopped=False
            )

            # Save to storage
            self.storage.save_multi_reservation_status(multi_status)
            logger.info(
                f"Created MultiReservationStatus for chat_id={self.chat_id} "
                f"with {len(reservation_infos)} reservations"
            )

        except Exception as e:
            logger.error(f"Failed to create MultiReservationStatus: {e}", exc_info=True)

    def _send_callback(self, message: str, status: int = 0, is_multi: bool = False,
                       total_seats: int = 1, seat_strategy: str = "consecutive",
                       paid: bool = False):
        """
        Send callback to main app.

        Args:
            message: Message to send to user
            status: 0 for success/completion, 1 for error
            is_multi: True if multi-reservation (random allocation with multiple seats)
            total_seats: Total number of seats reserved
            seat_strategy: Seat allocation strategy used
            paid: True when the reservation(s) were already paid by card (no reminders)
        """
        try:
            callback_url = f"{settings.CALLBACK_BASE_URL}/telebot"
            params = {
                "chatId": self.chat_id,
                "msg": message,
                "status": status,
                "isMulti": "1" if is_multi else "0",
                "totalSeats": str(total_seats),
                "seatStrategy": seat_strategy,
                "paid": "1" if paid else "0"
            }

            session = requests.session()
            response = session.get(callback_url, params=params, verify=False, timeout=10)

            if response.status_code == 200:
                logger.debug(f"Callback sent successfully: status={status}, is_multi={is_multi}")
            else:
                logger.warning(
                    f"Callback returned non-200 status: {response.status_code}, "
                    f"response={response.text[:200]}"
                )

        except requests.exceptions.Timeout:
            logger.error(f"Callback timeout - main app may be down or slow")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Failed to connect to main app for callback: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending callback: {e}", exc_info=True)

    def _run_random_reservation(self):
        """
        Run random seating reservation: one seat at a time with payment confirmation.

        Flow for each seat:
        1. Search and reserve one seat
        2. Send notification to user
        3. Wait for payment confirmation (up to 10 minutes)
        4. Proceed to next seat
        """
        total_seats = self.passenger_count
        logger.info(f"=== RANDOM SEATING MODE: {total_seats} seats ===")

        # Seats of a previous run must not leak into this run's final summary
        self.storage.delete_partial_reservations(self.chat_id)
        self.storage.set_current_seat_index(self.chat_id, None)

        payment_timeout_seconds = settings.PAYMENT_TIMEOUT_MINUTES * 60
        all_paid = self.card is not None  # becomes False as soon as one payment fails

        for seat_index in range(total_seats):
            logger.info(f"━━━ Seat {seat_index + 1}/{total_seats} ━━━")

            # Reserve one seat (don't set current_seat_index yet!)
            try:
                reservation = self._reserve_single_seat_random(seat_index)
            except Exception as e:
                logger.error(f"Failed to reserve seat {seat_index + 1}: {e}", exc_info=True)
                error_msg = f"""
❌ {seat_index + 1}번째 좌석 예약 실패

오류: {str(e)}

💡 /cancel 후 다시 시도하세요.
"""
                self._send_callback(error_msg, status=1)
                return

            if not reservation:
                logger.error(f"No reservation returned for seat {seat_index + 1}")
                error_msg = f"❌ {seat_index + 1}번째 좌석 예약 실패 (결과 없음)"
                self._send_callback(error_msg, status=1)
                return

            # Save partial reservation
            reservation_data = {
                "seat_index": seat_index,
                "train_info": str(reservation),
                "reserved_at": datetime.now().isoformat()
            }
            self.storage.save_partial_reservation(self.chat_id, seat_index, reservation_data)
            logger.info(f"✅ Seat {seat_index + 1} reserved and saved to Redis")

            # Automatic card payment for this seat
            paid, payment_text = (False, "")
            if self.card:
                paid, payment_text = self._try_auto_payment(reservation)
                if not paid:
                    all_paid = False
                reservation_data["paid"] = paid
                self.storage.save_partial_reservation(self.chat_id, seat_index, reservation_data)

            # Create or update MultiReservationStatus for reminder service
            self._update_multi_reservation_status(seat_index, reservation, total_seats, paid=paid)

            # NOTE: Don't start reminder service here (subprocess doesn't share memory with main app)
            # Reminder will be started by main app when it receives the callback (status=2)

            if not paid:
                # NOW set current seat index for payment waiting
                # This prevents "결제 대기중" message before reservation succeeds
                self.storage.set_current_seat_index(self.chat_id, seat_index)

            # Send notification to user
            message = self._build_partial_reservation_message(
                seat_index,
                total_seats,
                reservation,
                payment_text
            )
            self._send_callback(message, status=2, seat_strategy=self.seat_strategy, paid=paid)  # status=2: partial success

            if paid and seat_index < total_seats - 1:
                logger.info("Seat paid automatically, moving on to the next seat...")
                time.sleep(3)
                continue

            # Wait for payment confirmation (or timeout)
            if seat_index < total_seats - 1:  # Not the last seat
                logger.info(f"⏳ Waiting for payment confirmation for seat {seat_index + 1}...")
                payment_confirmed = self.storage.wait_for_payment(
                    self.chat_id,
                    seat_index,
                    timeout=payment_timeout_seconds
                )

                if payment_confirmed:
                    logger.info(f"✅ Payment confirmed for seat {seat_index + 1}")
                else:
                    logger.warning(f"⏱ Payment timeout for seat {seat_index + 1}")
                    timeout_msg = f"""
⏱ {seat_index + 1}번째 좌석 결제 시간 초과

{settings.PAYMENT_TIMEOUT_MINUTES}분이 지났습니다. 다음 좌석 예약을 진행합니다.

⚠️ 미결제 좌석은 자동 취소될 수 있으니 빠르게 결제해주세요!
"""
                    self._send_callback(timeout_msg, status=2, seat_strategy=self.seat_strategy)

                # Seat index must not linger: otherwise every user message during the next
                # search is misread as a payment confirmation for this seat.
                self.storage.set_current_seat_index(self.chat_id, None)

                # Brief pause before next reservation
                logger.info("Waiting 3 seconds before next reservation...")
                time.sleep(3)

        # All seats reserved!
        self.storage.set_current_seat_index(self.chat_id, None)  # Clear index
        all_reservations = self.storage.get_partial_reservations(self.chat_id)

        final_message = self._build_final_random_message(all_reservations, total_seats, all_paid)
        self._send_callback(final_message, status=0, seat_strategy=self.seat_strategy, paid=all_paid)  # status=0: complete success

        logger.info(f"🎉 All {total_seats} seats reserved successfully!")

    def _reserve_single_seat_random(self, seat_index: int):
        """
        Reserve a single seat for random allocation.

        Args:
            seat_index: Index of the seat being reserved (0-based)

        Returns:
            Reservation object if successful

        Raises:
            Exception: If reservation fails with non-duplicate error
        """
        logger.info(f"🔍 Starting search for seat {seat_index + 1}...")

        attempts = 0
        max_attempts = None  # Infinite
        duplicate_notified = False  # Track if we already notified about duplicate

        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.error(f"Max attempts reached for seat {seat_index + 1}")
                return None

            is_summary = (attempts % 60 == 0)

            if is_summary:
                logger.debug(f"🔄 Search attempt #{attempts} for seat {seat_index + 1}")

            # Search for trains (single passenger)
            try:
                trains = self.korail.search_trains(
                    dep_date=self.dep_date,
                    src_locate=self.src_locate,
                    dst_locate=self.dst_locate,
                    dep_time=self.dep_time,
                    max_dep_time=self.max_dep_time,
                    train_type=self.train_type,
                    passenger_count=1,  # Single seat
                    verbose=is_summary
                )
                if trains:
                    logger.debug(f"✅ Search completed: found {len(trains)} trains")
                elif is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
            except (KorailAccessRestrictedError, ValueError):
                raise  # fatal: blocked client or invalid search (e.g. departure already passed)
            except Exception as e:
                logger.error(f"❌ Search failed (attempt #{attempts}): {e}", exc_info=True)
                time.sleep(self.korail._search_interval)
                continue

            if not trains:
                time.sleep(self.korail._search_interval)
                continue

            # Try to reserve (trains found = rare, always log)
            duplicate_found = False
            for idx, train in enumerate(trains, 1):
                logger.info(f"🚂 Trying train {idx}/{len(trains)}: {train}")

                reservation = self.korail.reserve_train(
                    train,
                    option=self.reserve_option,
                    passenger_count=1
                )

                if reservation == "DUPLICATE":
                    # Duplicate reservation exists - notify user once and keep retrying
                    logger.warning(f"⚠️ Duplicate reservation detected for seat {seat_index + 1}")
                    duplicate_found = True

                    if not duplicate_notified:
                        # Send notification only once
                        self.telegram.send_notification(
                            self.chat_id,
                            f"⚠️ {seat_index + 1}번째 좌석 예약 시도 중 기존 예약 감지\n\n"
                            f"이미 해당 시간에 예약된 좌석이 있습니다.\n"
                            f"기존 예약이 취소될 때까지 10초마다 재시도합니다.\n\n"
                            f"🔗 기존 예약 확인: {settings.KORAIL_PAYMENT_URL}\n\n"
                            f"💡 검색을 중단하려면 /cancel 명령어를 사용하세요.\n"
                            f"💡 기존 예약을 취소하면 자동으로 새 예약을 시도합니다."
                        )
                        duplicate_notified = True
                        logger.info(f"📢 Duplicate notification sent for seat {seat_index + 1}")

                    # Continue to next train
                    continue

                elif reservation:
                    logger.info(f"✅ Seat {seat_index + 1} reserved after {attempts} search attempts!")
                    logger.info(f"🎉 Successfully reserved: {reservation}")
                    return reservation
                else:
                    logger.info(f"  ❌ Train {idx} failed (sold out or unavailable)")

            # All trains in this search failed
            if duplicate_found:
                logger.info(f"⚠️ Duplicate reservation detected, waiting 10s before retry...")
                time.sleep(10)  # Wait 10 seconds when duplicate found
            else:
                logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")
                time.sleep(self.korail._search_interval)

    def _build_partial_reservation_message(self, seat_index: int, total_seats: int, reservation,
                                           payment_text: str = "") -> str:
        """Build message for partial reservation success."""
        if payment_text and payment_text.startswith("💳"):
            next_text = "다음 좌석 예약을 바로 시작합니다." if seat_index < total_seats - 1 else ""
            return f"""
🎉 {seat_index + 1}/{total_seats}번째 좌석 예약 성공!

━━━━━━━━━━━━━━━━━━━━
{reservation}
━━━━━━━━━━━━━━━━━━━━

{payment_text}

{next_text}
"""
        failure_text = f"{payment_text}\n\n" if payment_text else ""
        return f"""
🎉 {seat_index + 1}/{total_seats}번째 좌석 예약 성공!

━━━━━━━━━━━━━━━━━━━━
{reservation}
━━━━━━━━━━━━━━━━━━━━

{failure_text}⏰ 예약 후 {settings.PAYMENT_TIMEOUT_MINUTES}분 이내 결제하세요!
🔗 결제: {settings.KORAIL_PAYMENT_URL}

💡 결제 후 아무 메시지나 보내면 다음 좌석 예약이 시작됩니다.

⚠️ {settings.PAYMENT_TIMEOUT_MINUTES}분 동안 메시지가 없으면 자동으로 다음 좌석 예약을 진행합니다.
"""

    def _build_final_random_message(self, all_reservations: list, total_seats: int,
                                    all_paid: bool = False) -> str:
        """Build final message for all random reservations complete."""
        reservation_details = "\n".join([
            f"좌석 {i+1}: {r.get('train_info', 'N/A')}" + (" 💳 결제완료" if r.get('paid') else "")
            for i, r in enumerate(all_reservations)
        ])

        if all_paid:
            payment_notice = (
                f"💳 모든 좌석이 카드({self.card.masked})로 자동 결제되었습니다.\n"
                f"코레일 앱/홈페이지의 '승차권 확인'에서 확인하세요."
            )
        else:
            payment_notice = (
                f"⚠️ 중요 안내:\n"
                f"• 결제되지 않은 좌석은 {settings.PAYMENT_TIMEOUT_MINUTES}분 내 결제해야 합니다!\n"
                f"• 미결제 시 자동 취소됩니다!\n\n"
                f"🔗 결제 링크: {settings.KORAIL_PAYMENT_URL}"
            )

        return f"""
🎉🎉 모든 좌석 예약 완료! 🎉🎉

총 {total_seats}명의 좌석이 개별적으로 예약되었습니다.
(랜덤 배치: 좌석이 떨어져 있을 수 있습니다)

━━━━━━━━━━━━━━━━━━━━
{reservation_details}
━━━━━━━━━━━━━━━━━━━━

{payment_notice}

✅ 축하합니다! 🎊
"""


if __name__ == "__main__":
    process = BackgroundReservationProcess()
    process.run()

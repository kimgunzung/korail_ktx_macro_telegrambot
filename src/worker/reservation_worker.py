"""
Background worker for train reservation.

Started by ``korailbot start`` as a detached process. It logs in to Korail, searches
until a seat is available, reserves it, optionally pays with the saved card, and
reports through the ``Notifier`` (console log + KakaoTalk) and Redis
(``last_result``), which ``korailbot status`` reads.

Arguments (argv): korail_id dep_date src dst dep_time train_type special_info
user_id max_dep_time passenger_count seat_strategy
Environment: KORAIL_BOT_PASSWORD (required), KORAIL_BOT_CARD (optional JSON CardInfo)
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests

# Add src to path when run as a script
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from config.settings import settings  # noqa: E402
from storage.redis import RedisStorage  # noqa: E402
from services.korail_service import (  # noqa: E402
    KorailService, DuplicateReservationError, KorailAccessRestrictedError, PaymentError,
    TrainType, ReserveOption,
)
from services.notifier import Notifier  # noqa: E402
from models import (  # noqa: E402
    MultiReservationStatus, SingleReservationInfo, ReservationPaymentStatus, CardInfo,
    PaymentStatus, ReservationResult,
)
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

sys.setrecursionlimit(settings.RECURSION_LIMIT)


class ReservationWorker:
    """Searches, reserves (and pays) according to the CLI arguments."""

    def __init__(self, argv: Optional[list] = None, storage=None, notifier: Optional[Notifier] = None,
                 korail: Optional[KorailService] = None):
        argv = sys.argv if argv is None else argv
        if len(argv) < 12:
            logger.error("Insufficient arguments")
            sys.exit(1)

        self.username = argv[1]
        self.password = os.environ.pop("KORAIL_BOT_PASSWORD", "")
        if not self.password:
            logger.error("No Korail password provided (KORAIL_BOT_PASSWORD not set)")
            sys.exit(1)
        self.dep_date = argv[2]
        self.src_locate = argv[3]
        self.dst_locate = argv[4]
        self.dep_time = argv[5]
        self.train_type_str = argv[6]
        self.special_info_str = argv[7]
        self.user_id = int(argv[8])
        self.max_dep_time = argv[9]
        self.passenger_count = int(argv[10])
        self.seat_strategy = argv[11]

        self.train_type = self._parse_train_type(self.train_type_str)
        self.reserve_option = self._parse_reserve_option(self.special_info_str)
        self.card: Optional[CardInfo] = self._load_card_from_env()

        self.storage = storage or RedisStorage()
        self.notifier = notifier or Notifier(self.storage, user_id=self.user_id)
        self.korail = korail or KorailService()

        logger.info("========================================")
        logger.info("Reservation worker started")
        logger.info(f"  user: {self.username}")
        logger.info(f"  route: {self.src_locate} -> {self.dst_locate} on {self.dep_date} "
                    f"{self.dep_time[:4]}~{self.max_dep_time}")
        logger.info(f"  train: {self.train_type} seat: {self.reserve_option} "
                    f"passengers: {self.passenger_count} ({self.seat_strategy})")
        logger.info(f"  auto_payment: {self.card.masked if self.card else 'off'}")
        logger.info("========================================")

    # ------------------------------------------------------------ parsing

    @staticmethod
    def _parse_train_type(train_type_str: str) -> str:
        upper = train_type_str.upper()
        if "KTX" in upper and "ALL" not in upper:
            return TrainType.KTX
        if train_type_str == "100":
            return TrainType.KTX
        return TrainType.ALL

    @staticmethod
    def _parse_reserve_option(option_str: str) -> str:
        upper = option_str.upper()
        for name in ("GENERAL_FIRST", "GENERAL_ONLY", "SPECIAL_FIRST", "SPECIAL_ONLY"):
            if name in upper:
                return getattr(ReserveOption, name)
        return ReserveOption.GENERAL_FIRST

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

    # ---------------------------------------------------------- reporting

    def _report(self, status: str, message: str, paid: bool = False, final: bool = True) -> None:
        """Notify the user and record the outcome for ``korailbot status``."""
        self.notifier.notify(message, link_url=settings.KORAIL_PAYMENT_URL)
        self.storage.save_last_result(self.user_id, ReservationResult(status=status, message=message, paid=paid))
        if final:
            self.storage.delete_running_reservation(self.user_id)

    def _manual_payment_text(self) -> str:
        return (
            f"⚠️ {settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제하지 않으면 예약이 취소됩니다.\n"
            f"결제 후 터미널에서 `korailbot paid` 를 실행하면 알림이 중단됩니다.\n"
            f"🔗 결제: {settings.KORAIL_PAYMENT_URL}"
        )

    # ------------------------------------------------------------- payment

    def _try_auto_payment(self, reservation) -> Tuple[bool, str]:
        """Pay ``reservation`` with the saved card. Returns (paid, text for the message)."""
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
                f"💳 자동 결제 완료 (카드 {self.card.masked}, {amount})\n"
                f"승차권이 발권되었습니다. 코레일 앱/홈페이지 '승차권 확인'에서 확인하세요."
            )
        return False, (
            f"❌ 자동 결제 실패 (카드 {self.card.masked})\n사유: {reason}\n"
            f"예약은 유지되어 있으니 직접 결제해주세요. (`korailbot card set` 으로 카드 교체 가능)\n\n"
            f"{self._manual_payment_text()}"
        )

    def _run_payment_reminders(self, reserved_at: datetime, context: str = "") -> bool:
        """
        Send KakaoTalk reminders until the user runs ``korailbot paid`` or the deadline passes.

        Returns True when the payment was confirmed.
        """
        existing = self.storage.get_payment_status(self.user_id)
        if existing and existing.completed and existing.reservation_time and existing.reservation_time >= reserved_at:
            logger.info("Payment already confirmed before reminders started")
            return True
        self.storage.save_payment_status(PaymentStatus(
            chat_id=self.user_id, completed=False, reservation_time=reserved_at, reminder_active=True
        ))

        deadline = reserved_at + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)
        interval = max(10, settings.PAYMENT_REMINDER_INTERVAL_SECONDS)
        prefix = f"{context} " if context else ""
        while True:
            remaining = (deadline - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
            status = self.storage.get_payment_status(self.user_id)
            if status is None or status.completed:
                self.notifier.notify(f"✅ {prefix}결제 확인. 결제 알림을 종료합니다. 즐거운 여행 되세요! 🚄")
                return True
            remaining = int((deadline - datetime.now()).total_seconds())
            if remaining > 0:
                self.notifier.notify(
                    f"⏰ {prefix}결제 리마인더: 예약 취소까지 {remaining // 60}분 {remaining % 60}초 남았습니다.\n"
                    f"결제 후 `korailbot paid` 를 실행하세요.",
                    link_url=settings.KORAIL_PAYMENT_URL,
                )

        status = self.storage.get_payment_status(self.user_id)
        if status and status.completed:
            return True
        self.notifier.notify(
            f"⏰ {prefix}결제 리마인더 종료: {settings.PAYMENT_TIMEOUT_MINUTES}분이 지났습니다. "
            f"결제하지 않았다면 예약이 취소되었을 수 있으니 코레일에서 확인해주세요."
        )
        if status:
            status.reminder_active = False
            self.storage.save_payment_status(status)
        return False

    # ----------------------------------------------------------------- run

    def run(self) -> None:
        """Run the reservation process to completion."""
        try:
            logger.info(f"Logging in as {self.username}...")
            if not self.korail.login(self.username, self.password):
                self._report("failed", (
                    f"❌ 코레일 로그인 실패\n사유: {self.korail.last_login_error or '알 수 없음'}\n"
                    f"`korailbot login` 으로 계정 정보를 다시 저장한 뒤 시도하세요."
                ))
                return

            logger.info("Login successful, starting reservation loop...")
            if self.seat_strategy == "random":
                self._run_random_reservation()
            else:
                self._run_consecutive_reservation()

        except Exception as e:
            logger.error(f"Error in reservation process: {e}", exc_info=True)
            self._report("failed", (
                f"❌ 예약 프로세스 오류: {type(e).__name__}: {e}\n"
                f"{self.src_locate}→{self.dst_locate} {self.dep_date} {self.dep_time[:4]}\n"
                f"`korailbot logs` 로 자세한 로그를 확인하고 다시 시도하세요."
            ))
        finally:
            self.storage.delete_running_reservation(self.user_id)
            logger.info(f"Reservation worker ended for {self.username}")

    def _search_loop(self):
        """Run the consecutive search loop, translating errors into user reports.

        Returns the reservation, or None when it failed (``self._aborted`` tells whether
        the failure was already reported).
        """
        self._aborted = False
        try:
            try:
                return self.korail.search_and_reserve_loop(
                    dep_date=self.dep_date, src_locate=self.src_locate, dst_locate=self.dst_locate,
                    dep_time=self.dep_time, max_dep_time=self.max_dep_time, train_type=self.train_type,
                    reserve_option=self.reserve_option, passenger_count=self.passenger_count,
                    seat_strategy=self.seat_strategy,
                )
            except DuplicateReservationError:
                logger.warning("Duplicate reservation detected (first time), continuing search")
                self._report("partial", (
                    "⚠️ 동일한 열차에 대한 기존 예약이 있습니다. 기존 예약이 취소될 때까지 계속 검색합니다.\n"
                    "중단하려면 `korailbot cancel`, 기존 예약을 취소하면 자동으로 새 예약을 시도합니다."
                ), final=False)
                try:
                    return self.korail.search_and_reserve_loop(
                        dep_date=self.dep_date, src_locate=self.src_locate, dst_locate=self.dst_locate,
                        dep_time=self.dep_time, max_dep_time=self.max_dep_time, train_type=self.train_type,
                        reserve_option=self.reserve_option, passenger_count=self.passenger_count,
                        seat_strategy=self.seat_strategy,
                    )
                except DuplicateReservationError:
                    logger.error("Duplicate error raised again")
                    return None
        except KorailAccessRestrictedError as e:
            self._report("failed", (
                f"🚫 코레일 이용 제한: {e}\n"
                "VPN을 끄고 가정용 회선에서 실행하세요. 같은 IP에서 반복하면 차단이 길어집니다."
            ))
        except requests.exceptions.RequestException as e:
            self._report("failed", f"🌐 네트워크 오류: {e}\n인터넷 연결을 확인하고 다시 시도하세요.")
        except ValueError as e:
            self._report("failed", f"⚠️ 입력 데이터 오류: {e}\n역 이름/날짜/시각을 확인하고 다시 시도하세요.")
        self._aborted = True
        return None

    def _run_consecutive_reservation(self) -> None:
        reservation = self._search_loop()
        if not reservation:
            if not self._aborted:
                self._report("failed", "❌ 알 수 없는 오류로 예매에 실패했습니다. 코레일에서 기존 예약 여부를 확인하고 다시 시도하세요.")
            return

        logger.info(f"Reservation successful: {reservation}")
        reserved_at = datetime.now()
        paid, payment_text = self._try_auto_payment(reservation) if self.card else (False, "")
        seats_text = f" ({self.passenger_count}명, 연속 좌석)" if self.passenger_count > 1 else ""
        message = (
            f"🎉 열차 예약 성공!{seats_text}\n"
            f"{reservation}\n\n"
            f"{payment_text or self._manual_payment_text()}"
        )
        self._report("success", message, paid=paid, final=False)
        if not paid:
            self._run_payment_reminders(reserved_at)
        self.storage.delete_running_reservation(self.user_id)

    # --------------------------------------------------------------- random

    def _run_random_reservation(self) -> None:
        """Random seating: one seat at a time, paying (or waiting for payment) per seat."""
        total_seats = self.passenger_count
        logger.info(f"=== RANDOM SEATING MODE: {total_seats} seats ===")
        self.storage.delete_partial_reservations(self.user_id)
        self.storage.set_current_seat_index(self.user_id, None)
        payment_timeout_seconds = settings.PAYMENT_TIMEOUT_MINUTES * 60
        all_paid = self.card is not None
        last_reserved_at = datetime.now()

        for seat_index in range(total_seats):
            logger.info(f"━━━ Seat {seat_index + 1}/{total_seats} ━━━")
            try:
                reservation = self._reserve_single_seat_random(seat_index)
            except KorailAccessRestrictedError as e:
                self._report("failed", f"🚫 코레일 이용 제한: {e}\nVPN을 끄고 가정용 회선에서 다시 시도하세요.")
                return
            except Exception as e:
                logger.error(f"Failed to reserve seat {seat_index + 1}: {e}", exc_info=True)
                self._report("failed", f"❌ {seat_index + 1}번째 좌석 예약 실패: {e}\n다시 시도하세요.")
                return
            if not reservation:
                self._report("failed", f"❌ {seat_index + 1}번째 좌석 예약 실패 (결과 없음)")
                return

            last_reserved_at = datetime.now()
            reservation_data = {
                "seat_index": seat_index, "train_info": str(reservation),
                "reserved_at": last_reserved_at.isoformat(),
            }
            paid, payment_text = (False, "")
            if self.card:
                paid, payment_text = self._try_auto_payment(reservation)
                if not paid:
                    all_paid = False
            reservation_data["paid"] = paid
            self.storage.save_partial_reservation(self.user_id, seat_index, reservation_data)
            self._update_multi_reservation_status(seat_index, reservation, total_seats, paid=paid)

            if not paid:
                self.storage.set_current_seat_index(self.user_id, seat_index)

            next_text = ""
            if seat_index < total_seats - 1:
                next_text = ("\n다음 좌석 예약을 바로 시작합니다." if paid else
                             f"\n결제 후 `korailbot paid` 를 실행하면 다음 좌석 예약을 시작합니다. "
                             f"({settings.PAYMENT_TIMEOUT_MINUTES}분 뒤에는 자동으로 진행)")
            self._report("partial", (
                f"🎉 {seat_index + 1}/{total_seats}번째 좌석 예약 성공!\n{reservation}\n\n"
                f"{payment_text or self._manual_payment_text()}{next_text}"
            ), paid=paid, final=False)

            if seat_index < total_seats - 1:
                if not paid:
                    confirmed = self.storage.wait_for_payment(self.user_id, seat_index, timeout=payment_timeout_seconds)
                    if not confirmed:
                        self.notifier.notify(
                            f"⏱ {seat_index + 1}번째 좌석 결제 확인이 없어 다음 좌석 예약을 진행합니다. "
                            f"미결제 좌석은 자동 취소될 수 있으니 빠르게 결제해주세요!"
                        )
                    self.storage.set_current_seat_index(self.user_id, None)
                time.sleep(3)

        self.storage.set_current_seat_index(self.user_id, None)
        all_reservations = self.storage.get_partial_reservations(self.user_id)
        details = "\n".join(
            f"좌석 {i + 1}: {r.get('train_info', 'N/A')}" + (" 💳 결제완료" if r.get('paid') else "")
            for i, r in enumerate(all_reservations)
        )
        if all_paid:
            notice = f"💳 모든 좌석이 카드({self.card.masked})로 자동 결제되었습니다."
        else:
            notice = f"⚠️ 결제되지 않은 좌석은 {settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제해야 합니다!\n{self._manual_payment_text()}"
        self._report("success", (
            f"🎉 모든 좌석 예약 완료! ({total_seats}명, 랜덤 배치)\n{details}\n\n{notice}"
        ), paid=all_paid, final=False)

        multi = self.storage.get_multi_reservation_status(self.user_id)
        if not all_paid and multi and multi.get_pending_count() > 0:
            self._run_payment_reminders(last_reserved_at, context="랜덤 배치")
        self.storage.delete_running_reservation(self.user_id)
        logger.info(f"🎉 All {total_seats} seats reserved")

    def _update_multi_reservation_status(self, seat_index: int, reservation, total_seats: int,
                                         paid: bool = False) -> None:
        try:
            now = datetime.now()
            multi_status = self.storage.get_multi_reservation_status(self.user_id)
            if not multi_status or seat_index == 0:
                if seat_index == 0 and multi_status:
                    self.storage.delete_multi_reservation_status(self.user_id)
                multi_status = MultiReservationStatus(
                    chat_id=self.user_id, reservations=[], total_seats=total_seats,
                    seat_strategy=self.seat_strategy, created_at=now, manually_stopped=False,
                )
            multi_status.reservations.append(SingleReservationInfo(
                reservation_id=getattr(reservation, 'rsv_id', f"seat_{seat_index + 1}"),
                reservation_obj=reservation,
                reserved_at=now,
                expires_at=now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES),
                status=ReservationPaymentStatus.PAID if paid else ReservationPaymentStatus.PENDING,
                seat_number=seat_index + 1,
                train_info=str(reservation),
            ))
            self.storage.save_multi_reservation_status(multi_status)
        except Exception as e:
            logger.error(f"Failed to update MultiReservationStatus: {e}", exc_info=True)

    def _reserve_single_seat_random(self, seat_index: int):
        """Search until one seat is reserved for this index."""
        logger.info(f"🔍 Starting search for seat {seat_index + 1}...")
        attempts = 0
        duplicate_notified = False

        while True:
            attempts += 1
            is_summary = (attempts % 60 == 0)
            if is_summary:
                logger.debug(f"🔄 Search attempt #{attempts} for seat {seat_index + 1}")

            try:
                trains = self.korail.search_trains(
                    dep_date=self.dep_date, src_locate=self.src_locate, dst_locate=self.dst_locate,
                    dep_time=self.dep_time, max_dep_time=self.max_dep_time, train_type=self.train_type,
                    passenger_count=1, verbose=is_summary,
                )
            except (KorailAccessRestrictedError, ValueError):
                raise
            except Exception as e:
                logger.error(f"❌ Search failed (attempt #{attempts}): {e}", exc_info=True)
                time.sleep(self.korail._search_interval)
                continue

            if not trains:
                time.sleep(self.korail._search_interval)
                continue

            duplicate_found = False
            for idx, train in enumerate(trains, 1):
                logger.info(f"🚂 Trying train {idx}/{len(trains)}: {train}")
                reservation = self.korail.reserve_train(train, option=self.reserve_option, passenger_count=1)
                if reservation == "DUPLICATE":
                    duplicate_found = True
                    if not duplicate_notified:
                        duplicate_notified = True
                        self.notifier.notify(
                            f"⚠️ {seat_index + 1}번째 좌석 예약 중 기존 예약 감지. 기존 예약이 취소될 때까지 재시도합니다. "
                            f"중단하려면 `korailbot cancel`."
                        )
                    continue
                if reservation:
                    logger.info(f"✅ Seat {seat_index + 1} reserved after {attempts} attempts: {reservation}")
                    return reservation
                logger.info(f"  ❌ Train {idx} failed (sold out or unavailable)")

            time.sleep(10 if duplicate_found else self.korail._search_interval)


if __name__ == "__main__":
    ReservationWorker().run()

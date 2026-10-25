"""Reservation orchestration service."""
import os
import signal
import subprocess
import sys
from typing import Optional

from config.settings import settings
from models import TrainSearchParams, RunningReservation, UserSession
from storage.base import StorageInterface
from services.korail_service import KorailService
from services.telegram_service import TelegramService, MessageTemplates
from utils.logger import get_logger

logger = get_logger(__name__)

# Directory that holds the ``telegramBot``/``services``/... packages. The background
# process is started with ``-m``, so it must be importable from the child's cwd.
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Environment variable used to hand the Korail password to the background process so it
# never shows up in ``ps`` output as a command-line argument.
PASSWORD_ENV_VAR = "KORAIL_BOT_PASSWORD"


class ReservationService:
    """
    Service for managing train reservations.

    Orchestrates the reservation process including:
    - Starting background reservation processes
    - Managing running reservations
    - Cancelling reservations
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService
    ):
        """
        Initialize reservation service.

        Args:
            storage: Storage interface for state management
            telegram_service: Telegram service for notifications
        """
        self.storage = storage
        self.telegram = telegram_service

    @staticmethod
    def _process_alive(pid: int) -> bool:
        """True when ``pid`` refers to a live process we may signal."""
        if not pid or pid == 9999999:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def start_reservation_process(
        self,
        chat_id: int,
        username: str,
        password: str,
        search_params: TrainSearchParams
    ) -> bool:
        """
        Start a background reservation process.

        Args:
            chat_id: Telegram chat ID
            username: Korail username
            password: Korail password
            search_params: Train search parameters

        Returns:
            True if process started successfully
        """
        try:
            existing = self.storage.get_running_reservation(chat_id)
            if existing:
                if self._process_alive(existing.process_id):
                    logger.warning(
                        f"Reservation already running for chat_id={chat_id} (pid={existing.process_id})"
                    )
                    self.telegram.send_message(
                        chat_id,
                        "⚠️ 이미 진행 중인 예약이 있습니다.\n"
                        "새로 시작하려면 /cancel 로 먼저 취소해주세요."
                    )
                    return False
                # Stale record from a process that already exited (e.g. crash) - clean it up.
                logger.info(f"Removing stale running reservation for chat_id={chat_id}")
                self.storage.delete_running_reservation(chat_id)

            # Prepare subprocess arguments (password goes through the environment, see
            # PASSWORD_ENV_VAR; the argv slot is kept for backward compatibility).
            arguments = [
                username,
                "-",
                search_params.dep_date,
                search_params.src_locate,
                search_params.dst_locate,
                search_params.dep_time,
                search_params.train_type,
                search_params.special_option,
                str(chat_id),
                search_params.max_dep_time,
                str(search_params.passenger_count),
                search_params.seat_strategy
            ]

            env = os.environ.copy()
            env[PASSWORD_ENV_VAR] = password
            env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

            # Start background process with the same interpreter that runs the web app
            cmd = [sys.executable, '-m', 'telegramBot.telebotBackProcess'] + arguments
            proc = subprocess.Popen(cmd, cwd=SRC_DIR, env=env)

            logger.info(
                f"Started reservation process for chat_id={chat_id}, pid={proc.pid}"
            )

            # Save running reservation
            reservation = RunningReservation(
                chat_id=chat_id,
                process_id=proc.pid,
                korail_id=username,
                search_params=search_params
            )
            self.storage.save_running_reservation(reservation)

            # Update user session
            session = self.storage.get_user_session(chat_id)
            if session:
                session.process_id = proc.pid
                self.storage.save_user_session(session)

            # Notify subscribers
            self._notify_subscribers_start(username, search_params)

            # Send confirmation to user
            self.telegram.send_message(chat_id, MessageTemplates.reservation_started())

            return True

        except Exception as e:
            logger.error(f"Failed to start reservation process: {e}", exc_info=True)
            return False

    def cancel_reservation(self, chat_id: int) -> bool:
        """
        Cancel a running reservation.

        Args:
            chat_id: Telegram chat ID

        Returns:
            True if a running reservation was cancelled, False if none was running
        """
        try:
            # Get running reservation
            reservation = self.storage.get_running_reservation(chat_id)
            if not reservation:
                logger.info(f"No running reservation found for chat_id={chat_id}")
                return False

            # Kill process
            try:
                if reservation.process_id != 9999999:
                    os.kill(reservation.process_id, signal.SIGTERM)
                    logger.info(f"Killed process {reservation.process_id}")
            except ProcessLookupError:
                logger.warning(f"Process {reservation.process_id} not found")

            # Clean up storage
            self.storage.delete_running_reservation(chat_id)
            self._clear_partial_state(chat_id)

            # Reset user session
            session = self.storage.get_user_session(chat_id)
            if session:
                session.reset()
                self.storage.save_user_session(session)

            # Notify
            self._notify_subscribers_end(reservation.korail_id)
            self.telegram.send_message(chat_id, MessageTemplates.reservation_cancelled())

            return True

        except Exception as e:
            logger.error(f"Error cancelling reservation: {e}")
            return False

    def finish_reservation(self, chat_id: int, success: bool) -> None:
        """
        Clean up after the background process reported its final result.

        Args:
            chat_id: Telegram chat ID
            success: Whether the reservation succeeded
        """
        reservation = self.storage.get_running_reservation(chat_id)
        self.storage.delete_running_reservation(chat_id)
        if not success:
            self._clear_partial_state(chat_id)

        session = self.storage.get_user_session(chat_id)
        if session:
            session.reset()
            self.storage.save_user_session(session)

        korail_id = reservation.korail_id if reservation else (
            session.credentials.korail_id if session and session.credentials else None
        )
        if korail_id:
            self._notify_subscribers_end(korail_id)

    def _clear_partial_state(self, chat_id: int) -> None:
        """Drop per-seat state of the random seating flow."""
        if hasattr(self.storage, "delete_partial_reservations"):
            self.storage.delete_partial_reservations(chat_id)
        if hasattr(self.storage, "set_current_seat_index"):
            self.storage.set_current_seat_index(chat_id, None)

    def cancel_all_reservations(self, admin_chat_id: int) -> int:
        """
        Cancel all running reservations (admin function).

        Args:
            admin_chat_id: Admin's chat ID for notification

        Returns:
            Number of reservations cancelled
        """
        reservations = self.storage.get_all_running_reservations()
        count = 0

        for reservation in reservations:
            try:
                # Kill process
                if reservation.process_id != 9999999:
                    try:
                        os.kill(reservation.process_id, signal.SIGTERM)
                        logger.info(f"Killed process {reservation.process_id}")
                    except ProcessLookupError:
                        logger.warning(f"Process {reservation.process_id} not found")

                # Notify user
                self.telegram.send_message(
                    reservation.chat_id,
                    "관리자에 의해 실행중이던 예약이 강제 종료되었습니다. 문의는 관리자에게 연락하세요."
                )

                # Reset session
                session = self.storage.get_user_session(reservation.chat_id)
                if session:
                    session.reset()
                    self.storage.save_user_session(session)

                # Clean up
                self.storage.delete_running_reservation(reservation.chat_id)
                self._clear_partial_state(reservation.chat_id)
                count += 1

            except Exception as e:
                logger.error(f"Error cancelling reservation {reservation.chat_id}: {e}")

        # Notify admin
        korail_ids = [r.korail_id for r in reservations]
        self.telegram.send_message(
            admin_chat_id,
            f"총 {count}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {korail_ids}"
        )

        return count

    def get_status(self, chat_id: int) -> str:
        """
        Get status of running reservations.

        Args:
            chat_id: Chat ID requesting status

        Returns:
            Status message
        """
        reservations = self.storage.get_all_running_reservations()
        count = len(reservations)
        korail_ids = [r.korail_id for r in reservations]

        if count == 0:
            return "ℹ️ 실행 중인 예약이 없습니다.\n/start 로 새 예약을 시작할 수 있습니다."

        lines = [f"총 {count}개의 예약이 실행중입니다. 이용중인 사용자 : {korail_ids}"]

        mine = next((r for r in reservations if r.chat_id == chat_id), None)
        if mine:
            p = mine.search_params
            lines.append("")
            lines.append("📋 내 예약 검색 정보")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"📅 출발일: {p.dep_date}")
            lines.append(f"🚉 구간: {p.src_locate} → {p.dst_locate}")
            lines.append(f"🕐 검색 시각: {p.dep_time[:4]} ~ {p.max_dep_time}")
            lines.append(f"👥 인원: {p.passenger_count}명 ({p.seat_strategy})")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 취소하려면 /cancel 을 입력하세요.")

        return "\n".join(lines)

    def _notify_subscribers_start(self, username: str, params: TrainSearchParams) -> None:
        """Notify subscribers about reservation start."""
        subscribers = self.storage.get_all_subscribers()
        message = (
            f"{username}의 {params.src_locate}에서 {params.dst_locate}로 "
            f"{params.dep_date}에 출발하는 열차 예약이 시작되었습니다."
        )
        self.telegram.send_to_multiple(subscribers, message)

    def _notify_subscribers_end(self, username: str) -> None:
        """Notify subscribers about reservation end."""
        subscribers = self.storage.get_all_subscribers()
        message = f"{username}의 예약이 종료되었습니다."
        self.telegram.send_to_multiple(subscribers, message)

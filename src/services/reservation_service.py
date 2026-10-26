"""Reservation orchestration: start/cancel/status of the background worker."""
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from typing import Optional

from config.settings import settings
from models import TrainSearchParams, RunningReservation, CardInfo, KorailCredentials, PaymentStatus
from storage.base import StorageInterface
from utils.logger import get_logger

logger = get_logger(__name__)

# Directory that holds the ``worker``/``services``/... packages. The worker is started
# with ``-m``, so it must be importable from the child's cwd.
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Secrets are handed to the worker through its environment, never argv (visible in ``ps``).
PASSWORD_ENV_VAR = "KORAIL_BOT_PASSWORD"
CARD_ENV_VAR = "KORAIL_BOT_CARD"
LOG_FILE_ENV_VAR = "KORAIL_BOT_WORKER_LOG"


class ReservationService:
    """Manages the detached worker process that searches and reserves."""

    def __init__(self, storage: StorageInterface, user_id: int = settings.LOCAL_USER_ID):
        self.storage = storage
        self.user_id = user_id

    # ------------------------------------------------------------- liveness

    @staticmethod
    def _process_alive(pid: int) -> bool:
        """True when ``pid`` refers to a live process we may signal."""
        if not pid or pid == 9999999:
            return False
        if os.name == "nt":
            return ReservationService._windows_process_alive(pid)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    @staticmethod
    def _windows_process_alive(pid: int) -> bool:
        """Windows: ``os.kill(pid, 0)`` would *terminate* the process, so query it instead."""
        import ctypes
        import ctypes.wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    def running(self) -> Optional[RunningReservation]:
        """The running reservation, after dropping a record whose process has exited."""
        reservation = self.storage.get_running_reservation(self.user_id)
        if reservation and not self._process_alive(reservation.process_id):
            logger.info(f"Removing stale running reservation (pid={reservation.process_id})")
            self.storage.delete_running_reservation(self.user_id)
            return None
        return reservation

    # ---------------------------------------------------------------- start

    @staticmethod
    def worker_log_path(started_at: Optional[datetime] = None) -> str:
        stamp = (started_at or datetime.now()).strftime("%Y%m%d-%H%M%S")
        return os.path.join(settings.LOG_DIR, f"worker-{stamp}.log")

    def start(
        self,
        credentials: KorailCredentials,
        search_params: TrainSearchParams,
        card: Optional[CardInfo] = None,
    ) -> RunningReservation:
        """
        Start the detached worker.

        Raises:
            RuntimeError: a reservation is already running
            OSError: the process could not be started
        """
        if self.running():
            raise RuntimeError("이미 진행 중인 예약이 있습니다. 먼저 `cancel` 하세요.")

        # Fresh state for this run
        self.storage.delete_multi_reservation_status(self.user_id)
        self.storage.delete_partial_reservations(self.user_id)
        self.storage.delete_payment_status(self.user_id)

        started_at = datetime.now()
        os.makedirs(settings.LOG_DIR, exist_ok=True)
        log_path = self.worker_log_path(started_at)

        arguments = [
            credentials.korail_id,
            search_params.dep_date,
            search_params.src_locate,
            search_params.dst_locate,
            search_params.dep_time,
            search_params.train_type,
            search_params.special_option,
            str(self.user_id),
            search_params.max_dep_time,
            str(search_params.passenger_count),
            search_params.seat_strategy,
        ]

        env = os.environ.copy()
        env[PASSWORD_ENV_VAR] = credentials.korail_pw
        env[LOG_FILE_ENV_VAR] = log_path
        env.pop(CARD_ENV_VAR, None)
        if card is not None:
            env[CARD_ENV_VAR] = json.dumps(card.to_dict())
        env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.setdefault("PYTHONUNBUFFERED", "1")

        cmd = [sys.executable, "-m", "worker.reservation_worker"] + arguments
        popen_kwargs = dict(cwd=SRC_DIR, env=env, stdin=subprocess.DEVNULL, close_fds=True)
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            popen_kwargs["start_new_session"] = True

        with open(log_path, "a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, **popen_kwargs)

        reservation = RunningReservation(
            chat_id=self.user_id,
            process_id=proc.pid,
            korail_id=credentials.korail_id,
            search_params=search_params,
            started_at=started_at,
        )
        self.storage.save_running_reservation(reservation)
        logger.info(
            f"Started worker pid={proc.pid}, log={log_path}, "
            f"auto_payment={'on (' + card.masked + ')' if card else 'off'}"
        )
        return reservation

    # --------------------------------------------------------------- cancel

    def cancel(self) -> Optional[RunningReservation]:
        """Stop the worker. Returns the cancelled reservation, or None when nothing ran."""
        reservation = self.storage.get_running_reservation(self.user_id)
        if not reservation:
            return None
        if self._process_alive(reservation.process_id):
            try:
                os.kill(reservation.process_id, signal.SIGTERM)
                logger.info(f"Terminated worker pid={reservation.process_id}")
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.warning(f"Could not terminate pid={reservation.process_id}: {e}")
        self.storage.delete_running_reservation(self.user_id)
        self.storage.delete_multi_reservation_status(self.user_id)
        self.storage.delete_partial_reservations(self.user_id)
        self.storage.delete_payment_status(self.user_id)
        return reservation

    # ----------------------------------------------------------------- paid

    def confirm_payment(self) -> str:
        """
        ``korailbot paid``: tell the worker the user paid manually.

        Returns a short description of what was acknowledged.
        """
        acknowledged = []
        seat = self.storage.get_current_seat_index(self.user_id)
        if seat is not None:
            self.storage.mark_payment_ready(self.user_id, seat)
            self.storage.set_current_seat_index(self.user_id, None)
            acknowledged.append(f"{seat + 1}번째 좌석 결제 확인 → 다음 좌석 검색을 계속합니다")

        multi = self.storage.get_multi_reservation_status(self.user_id)
        if multi:
            multi.manually_stopped = True
            from models import ReservationPaymentStatus
            for r in multi.reservations:
                if r.status == ReservationPaymentStatus.PENDING:
                    r.status = ReservationPaymentStatus.PAID
            self.storage.save_multi_reservation_status(multi)
            acknowledged.append("미결제 좌석을 모두 결제 완료로 표시했습니다")

        status = self.storage.get_payment_status(self.user_id)
        if status and not status.completed:
            status.completed = True
            status.reminder_active = False
            self.storage.save_payment_status(status)
            acknowledged.append("결제 리마인더를 중단했습니다")
        elif status is None and seat is None and multi is None:
            # Nothing is waiting, but record the intent so a reminder that starts a moment
            # later sees it (e.g. the worker is between reservation and reminder loop).
            self.storage.save_payment_status(PaymentStatus(chat_id=self.user_id, completed=True, reminder_active=False))
            acknowledged.append("대기 중인 결제가 없습니다 (확인만 기록)")
        return "\n".join(acknowledged)

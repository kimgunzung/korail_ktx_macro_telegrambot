"""Integration tests for ReservationService (worker process management, Redis-backed)."""
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from config.settings import settings
from models import (
    CardInfo, KorailCredentials, MultiReservationStatus, PaymentStatus, ReservationPaymentStatus,
    RunningReservation, SingleReservationInfo, TrainSearchParams,
)
from services.reservation_service import ReservationService, CARD_ENV_VAR, PASSWORD_ENV_VAR
from storage import RedisStorage

USER = settings.LOCAL_USER_ID


def _params(**overrides) -> TrainSearchParams:
    base = dict(dep_date="20991231", src_locate="서울", dst_locate="부산", dep_time="090000",
                max_dep_time="1800", train_type="TrainType.KTX", train_type_display="KTX",
                special_option="ReserveOption.GENERAL_FIRST", special_option_display="GENERAL_FIRST",
                passenger_count=1, seat_strategy="consecutive")
    base.update(overrides)
    return TrainSearchParams(**base)


def _creds() -> KorailCredentials:
    return KorailCredentials(korail_id="010-1234-5678", korail_pw="s3cret")


class TestStart:
    def setup_method(self):
        self.storage = RedisStorage()
        self.service = ReservationService(self.storage, USER)

    def teardown_method(self):
        self.storage.redis.flushdb()

    @patch('subprocess.Popen')
    def test_start_spawns_detached_worker_and_records_it(self, mock_popen, tmp_path):
        mock_popen.return_value = Mock(pid=12345)
        with patch('config.settings.settings.LOG_DIR', str(tmp_path / "logs")):
            running = self.service.start(_creds(), _params())

        assert running.process_id == 12345
        stored = self.storage.get_running_reservation(USER)
        assert stored is not None and stored.process_id == 12345
        assert stored.search_params.src_locate == "서울"
        assert stored.korail_id == "010-1234-5678"

        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd[1:3] == ['-m', 'worker.reservation_worker']
        assert "s3cret" not in " ".join(cmd)                   # password never on argv
        assert kwargs['env'][PASSWORD_ENV_VAR] == "s3cret"
        assert CARD_ENV_VAR not in kwargs['env']
        assert kwargs['cwd'].endswith('src')
        assert kwargs.get('start_new_session') is True or 'creationflags' in kwargs
        assert (tmp_path / "logs").is_dir()                    # worker log directory created

    @patch('subprocess.Popen')
    def test_start_passes_card_via_env(self, mock_popen, tmp_path):
        mock_popen.return_value = Mock(pid=1)
        card = CardInfo(number="4111111111111111", expire="3512", password="12", verify_number="900101")
        with patch('config.settings.settings.LOG_DIR', str(tmp_path)):
            self.service.start(_creds(), _params(), card=card)
        env = mock_popen.call_args.kwargs['env']
        assert CardInfo.from_dict(json.loads(env[CARD_ENV_VAR])) == card
        assert "4111111111111111" not in " ".join(mock_popen.call_args.args[0])

    @patch('subprocess.Popen')
    def test_start_refused_while_running(self, mock_popen, tmp_path):
        mock_popen.return_value = Mock(pid=4242)
        with patch('config.settings.settings.LOG_DIR', str(tmp_path)), \
             patch.object(ReservationService, '_process_alive', return_value=True):
            self.service.start(_creds(), _params())
            with pytest.raises(RuntimeError, match="이미 진행 중"):
                self.service.start(_creds(), _params())
        assert mock_popen.call_count == 1

    @patch('subprocess.Popen')
    def test_stale_record_is_replaced(self, mock_popen, tmp_path):
        mock_popen.return_value = Mock(pid=4242)
        self.storage.save_running_reservation(RunningReservation(
            chat_id=USER, process_id=999999999, korail_id="x", search_params=_params()))
        with patch('config.settings.settings.LOG_DIR', str(tmp_path)):
            running = self.service.start(_creds(), _params())
        assert running.process_id == 4242

    @patch('subprocess.Popen')
    def test_start_clears_previous_run_state(self, mock_popen, tmp_path):
        mock_popen.return_value = Mock(pid=4242)
        self.storage.save_partial_reservation(USER, 0, {"train_info": "old"})
        self.storage.save_payment_status(PaymentStatus(chat_id=USER, completed=True))
        with patch('config.settings.settings.LOG_DIR', str(tmp_path)):
            self.service.start(_creds(), _params())
        assert self.storage.get_partial_reservations(USER) == []
        assert self.storage.get_payment_status(USER) is None


class TestCancelAndStatus:
    def setup_method(self):
        self.storage = RedisStorage()
        self.service = ReservationService(self.storage, USER)

    def teardown_method(self):
        self.storage.redis.flushdb()

    def _running(self, pid=4321):
        self.storage.save_running_reservation(RunningReservation(
            chat_id=USER, process_id=pid, korail_id="010-1234-5678", search_params=_params()))

    def test_cancel_terminates_and_cleans_up(self):
        self._running()
        self.storage.save_partial_reservation(USER, 0, {"train_info": "x"})
        with patch.object(ReservationService, '_process_alive', return_value=True), patch('os.kill') as kill:
            cancelled = self.service.cancel()
        kill.assert_called_once()
        assert kill.call_args.args[0] == 4321
        assert cancelled is not None and cancelled.process_id == 4321
        assert self.storage.get_running_reservation(USER) is None
        assert self.storage.get_partial_reservations(USER) == []

    def test_cancel_without_running_returns_none(self):
        assert self.service.cancel() is None

    def test_cancel_dead_process_still_cleans(self):
        self._running(pid=999999999)
        with patch.object(ReservationService, '_process_alive', return_value=False), patch('os.kill') as kill:
            assert self.service.cancel() is not None
        kill.assert_not_called()
        assert self.storage.get_running_reservation(USER) is None

    def test_running_drops_dead_record(self):
        self._running(pid=999999999)
        assert self.service.running() is None
        assert self.storage.get_running_reservation(USER) is None

    def test_running_keeps_live_record(self):
        self._running()
        with patch.object(ReservationService, '_process_alive', return_value=True):
            assert self.service.running().process_id == 4321


class TestConfirmPayment:
    def setup_method(self):
        self.storage = RedisStorage()
        self.service = ReservationService(self.storage, USER)

    def teardown_method(self):
        self.storage.redis.flushdb()

    def test_marks_payment_status_completed(self):
        self.storage.save_payment_status(PaymentStatus(chat_id=USER, completed=False, reminder_active=True))
        text = self.service.confirm_payment()
        status = self.storage.get_payment_status(USER)
        assert status.completed is True and status.reminder_active is False
        assert "리마인더" in text

    def test_raises_seat_flag_in_random_mode(self):
        self.storage.set_current_seat_index(USER, 1)
        text = self.service.confirm_payment()
        assert self.storage.is_payment_ready(USER, 1)
        assert self.storage.get_current_seat_index(USER) is None
        assert "2번째 좌석" in text

    def test_marks_pending_seats_paid(self):
        now = datetime.now()
        self.storage.save_multi_reservation_status(MultiReservationStatus(
            chat_id=USER, total_seats=2, seat_strategy="random", created_at=now, reservations=[
                SingleReservationInfo("r1", None, now, now + timedelta(minutes=10), ReservationPaymentStatus.PENDING, 1, "a"),
                SingleReservationInfo("r2", None, now, now + timedelta(minutes=10), ReservationPaymentStatus.PAID, 2, "b"),
            ]))
        self.service.confirm_payment()
        multi = self.storage.get_multi_reservation_status(USER)
        assert all(r.status == ReservationPaymentStatus.PAID for r in multi.reservations)
        assert multi.manually_stopped is True

    def test_nothing_waiting_records_intent(self):
        text = self.service.confirm_payment()
        assert "대기 중인 결제가 없습니다" in text
        assert self.storage.get_payment_status(USER).completed is True


class TestProcessAlive:
    """Liveness check must never signal the process (Windows os.kill(pid, 0) terminates it)."""

    def test_posix_uses_signal_zero(self):
        with patch('os.name', 'posix'), patch('os.kill') as kill:
            assert ReservationService._process_alive(4242) is True
            kill.assert_called_once_with(4242, 0)

    def test_posix_dead_process(self):
        with patch('os.name', 'posix'), patch('os.kill', side_effect=ProcessLookupError):
            assert ReservationService._process_alive(4242) is False

    def test_windows_never_calls_os_kill(self):
        with patch('os.name', 'nt'), patch('os.kill') as kill, \
             patch.object(ReservationService, '_windows_process_alive', return_value=True) as win:
            assert ReservationService._process_alive(4242) is True
            kill.assert_not_called()
            win.assert_called_once_with(4242)

    def test_placeholder_pid_is_not_alive(self):
        with patch('os.kill') as kill:
            assert ReservationService._process_alive(9999999) is False
            assert ReservationService._process_alive(0) is False
            kill.assert_not_called()

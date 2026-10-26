"""Unit tests for the reservation worker (fake storage/notifier/Korail, no network, no sleep)."""
import os
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from models import CardInfo, PaymentStatus, ReservationPaymentStatus
from services.korail_service import DuplicateReservationError, KorailAccessRestrictedError
from worker.reservation_worker import ReservationWorker


class FakeStorage:
    """In-memory stand-in for RedisStorage covering what the worker touches."""

    def __init__(self):
        self.running = {1: object()}
        self.last_result = None
        self.payment_status = None
        self.multi = None
        self.partials = {}
        self.seat_index = None
        self.ready = set()

    def delete_running_reservation(self, user_id):
        self.running.pop(user_id, None)

    def save_last_result(self, user_id, result):
        self.last_result = result

    def get_payment_status(self, user_id):
        return self.payment_status

    def save_payment_status(self, status):
        self.payment_status = status

    def get_multi_reservation_status(self, user_id):
        return self.multi

    def save_multi_reservation_status(self, status):
        self.multi = status

    def delete_multi_reservation_status(self, user_id):
        self.multi = None

    def save_partial_reservation(self, user_id, seat_index, data):
        self.partials[seat_index] = data

    def get_partial_reservations(self, user_id):
        return [self.partials[k] for k in sorted(self.partials)]

    def delete_partial_reservations(self, user_id):
        self.partials = {}

    def set_current_seat_index(self, user_id, index):
        self.seat_index = index

    def get_current_seat_index(self, user_id):
        return self.seat_index

    def wait_for_payment(self, user_id, seat_index, timeout=600):
        return seat_index in self.ready


ARGV = ["worker", "010-1234-5678", "20991231", "서울", "부산", "090000", "TrainType.KTX",
        "ReserveOption.GENERAL_FIRST", "1", "1800", "1", "consecutive"]


def _reservation(text="KTX 101 서울→부산 09:00", price=59800):
    r = MagicMock()
    r.rsv_id = "R1"
    r.wct_no = "01"
    r.price = price
    r.is_waiting = False
    r.__str__ = lambda self: text
    return r


@pytest.fixture
def make_worker():
    def _make(argv=None, card=None, env=None):
        environment = {"KORAIL_BOT_PASSWORD": "pw"}
        if card:
            import json
            environment["KORAIL_BOT_CARD"] = json.dumps(card.to_dict())
        environment.update(env or {})
        with patch.dict(os.environ, environment):
            storage = FakeStorage()
            notifier = Mock()
            korail = Mock()
            korail._search_interval = 0
            korail.login.return_value = True
            worker = ReservationWorker(argv or ARGV, storage=storage, notifier=notifier, korail=korail)
        return worker
    return _make


class TestInit:
    def test_parses_arguments_and_env(self, make_worker):
        card = CardInfo(number="4111111111111111", expire="3512", password="12", verify_number="900101")
        worker = make_worker(card=card)
        assert worker.username == "010-1234-5678" and worker.password == "pw"
        assert worker.user_id == 1 and worker.passenger_count == 1
        assert worker.card == card
        assert "KORAIL_BOT_PASSWORD" not in os.environ and "KORAIL_BOT_CARD" not in os.environ

    def test_missing_password_exits(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KORAIL_BOT_PASSWORD", None)
            with pytest.raises(SystemExit):
                ReservationWorker(ARGV, storage=FakeStorage(), notifier=Mock(), korail=Mock())

    def test_parsers(self):
        from services.korail_service import TrainType, ReserveOption
        assert ReservationWorker._parse_train_type("TrainType.KTX") == TrainType.KTX
        assert ReservationWorker._parse_train_type("TrainType.ALL") == TrainType.ALL
        assert ReservationWorker._parse_reserve_option("ReserveOption.SPECIAL_ONLY") == ReserveOption.SPECIAL_ONLY
        assert ReservationWorker._parse_reserve_option("junk") == ReserveOption.GENERAL_FIRST


class TestConsecutiveRun:
    def test_login_failure_is_reported(self, make_worker):
        worker = make_worker()
        worker.korail.login.return_value = False
        worker.korail.last_login_error = "비밀번호 오류"
        worker.run()
        assert worker.storage.last_result.status == "failed"
        assert "비밀번호 오류" in worker.storage.last_result.message
        assert 1 not in worker.storage.running
        worker.notifier.notify.assert_called_once()

    def test_success_with_auto_payment(self, make_worker):
        card = CardInfo(number="4111111111111111", expire="3512", password="12", verify_number="900101")
        worker = make_worker(card=card)
        worker.korail.search_and_reserve_loop.return_value = _reservation()
        worker.korail.pay_reservation.return_value = 59800
        worker.run()
        result = worker.storage.last_result
        assert result.status == "success" and result.paid is True
        assert "자동 결제 완료" in result.message and "59,800원" in result.message
        assert worker.storage.payment_status is None       # no reminders when paid
        assert 1 not in worker.storage.running

    def test_success_without_card_runs_reminders_until_paid(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.return_value = _reservation()

        def paid_after_first_sleep(_seconds):
            worker.storage.payment_status.completed = True

        with patch('worker.reservation_worker.time.sleep', side_effect=paid_after_first_sleep) as sleep:
            worker.run()
        assert worker.storage.last_result.status == "success" and worker.storage.last_result.paid is False
        assert "korailbot paid" in worker.storage.last_result.message
        texts = [c.args[0] for c in worker.notifier.notify.call_args_list]
        assert any("결제 확인" in t for t in texts)
        assert sleep.call_count == 1

    def test_reminders_time_out(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.return_value = _reservation()
        clock = {"now": datetime(2026, 1, 1, 12, 0, 0)}

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        def advance(seconds):
            clock["now"] = clock["now"] + timedelta(seconds=seconds)

        with patch('worker.reservation_worker.datetime', FakeDateTime), \
             patch('worker.reservation_worker.time.sleep', side_effect=advance), \
             patch('config.settings.settings.PAYMENT_TIMEOUT_MINUTES', 1), \
             patch('config.settings.settings.PAYMENT_REMINDER_INTERVAL_SECONDS', 20):
            worker.run()
        texts = [c.args[0] for c in worker.notifier.notify.call_args_list]
        assert sum("결제 리마인더:" in t for t in texts) == 2       # at 40s and 20s remaining
        assert any("리마인더 종료" in t for t in texts)
        assert worker.storage.payment_status.reminder_active is False

    def test_payment_confirmed_before_reminders_start(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.return_value = _reservation()
        # `korailbot paid` ran a moment before the worker created its status (same run)
        worker.storage.payment_status = PaymentStatus(chat_id=1, completed=True, reservation_time=datetime.now() + timedelta(seconds=5))
        with patch('worker.reservation_worker.time.sleep') as sleep:
            worker.run()
        sleep.assert_not_called()

    def test_access_restriction_stops_and_reports(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.side_effect = KorailAccessRestrictedError("VPN (-2000)")
        worker.run()
        assert worker.storage.last_result.status == "failed"
        assert "이용 제한" in worker.storage.last_result.message

    def test_duplicate_then_success(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.side_effect = [DuplicateReservationError("dup"), _reservation()]
        worker.korail.pay_reservation.return_value = 0
        with patch('worker.reservation_worker.time.sleep', side_effect=lambda s: setattr(worker.storage.payment_status, 'completed', True)):
            worker.run()
        texts = [c.args[0] for c in worker.notifier.notify.call_args_list]
        assert any("기존 예약" in t for t in texts)
        assert worker.storage.last_result.status == "success"

    def test_no_result_is_failure(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.return_value = None
        worker.run()
        assert worker.storage.last_result.status == "failed"

    def test_unexpected_error_is_reported(self, make_worker):
        worker = make_worker()
        worker.korail.search_and_reserve_loop.side_effect = RuntimeError("boom")
        worker.run()
        assert worker.storage.last_result.status == "failed" and "boom" in worker.storage.last_result.message
        assert 1 not in worker.storage.running


class TestRandomRun:
    def _random_worker(self, make_worker, card=None, seats=2):
        argv = ARGV[:]
        argv[10], argv[11] = str(seats), "random"
        worker = make_worker(argv=argv, card=card)
        worker.korail.search_trains.return_value = [Mock()]
        worker.korail.reserve_train.side_effect = [_reservation(f"KTX {i}") for i in range(seats)]
        return worker

    def test_all_seats_paid_automatically(self, make_worker):
        card = CardInfo(number="4111111111111111", expire="3512", password="12", verify_number="900101")
        worker = self._random_worker(make_worker, card=card)
        worker.korail.pay_reservation.return_value = 10000
        with patch('worker.reservation_worker.time.sleep'):
            worker.run()
        result = worker.storage.last_result
        assert result.status == "success" and result.paid is True
        assert "모든 좌석이 카드" in result.message and result.message.count("💳 결제완료") == 2
        assert worker.storage.seat_index is None
        assert all(r.status == ReservationPaymentStatus.PAID for r in worker.storage.multi.reservations)

    def test_manual_payment_waits_per_seat_then_reminds(self, make_worker):
        worker = self._random_worker(make_worker)
        worker.storage.ready = {0}  # user runs `korailbot paid` for seat 1

        def finish(_):
            if worker.storage.payment_status:
                worker.storage.payment_status.completed = True

        with patch('worker.reservation_worker.time.sleep', side_effect=finish):
            worker.run()
        result = worker.storage.last_result
        assert result.status == "success" and result.paid is False
        texts = [c.args[0] for c in worker.notifier.notify.call_args_list]
        assert any("1/2번째 좌석 예약 성공" in t for t in texts)
        assert any("2/2번째 좌석 예약 성공" in t for t in texts)
        assert any("랜덤 배치 결제 확인" in t for t in texts)

    def test_seat_failure_is_reported(self, make_worker):
        worker = self._random_worker(make_worker)
        worker.korail.search_trains.side_effect = ValueError("출발 시각이 이미 지났습니다")
        worker.run()
        assert worker.storage.last_result.status == "failed"
        assert "지났습니다" in worker.storage.last_result.message

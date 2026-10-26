"""Unit tests for automatic card payment: validators, Korail pay wrapper, background helper."""
import json
import os
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from pykorail.exceptions import AccessRestrictedError, HttpStatusError, KorailError, NeedToLoginError

from models import CardInfo
from services.korail_service import KorailAccessRestrictedError, KorailService, PaymentError
from utils.validators import InputValidator


# ------------------------------------------------------------------ validators

class TestCardValidators:
    @pytest.mark.parametrize("number", ["4111111111111111", "4111-1111-1111-1111", "4111 1111 1111 1111", "5500000000000004"])
    def test_valid_numbers(self, number):
        assert InputValidator.validate_card_number(number) == (True, None)

    @pytest.mark.parametrize("number,fragment", [
        ("", "입력"), ("abcd", "숫자"), ("1234", "자릿수"), ("4111111111111112", "올바르지"),
    ])
    def test_invalid_numbers(self, number, fragment):
        ok, error = InputValidator.validate_card_number(number)
        assert ok is False and fragment in error

    def test_normalize_number(self):
        assert InputValidator.normalize_card_number("4111-1111 1111-1111") == "4111111111111111"

    def test_expire_yymm_and_mmyy(self):
        future = datetime.today().year + 2
        yy = str(future)[-2:]
        assert InputValidator.validate_card_expire(f"{yy}12") == (True, None)
        assert InputValidator.validate_card_expire(f"12/{yy}") == (True, None)
        assert InputValidator.normalize_card_expire(f"12/{yy}") == f"{yy}12"
        assert InputValidator.normalize_card_expire(f"12-20{yy}") == f"{yy}12"

    def test_expire_rejects_bad_month_and_past(self):
        ok, error = InputValidator.validate_card_expire("2813")
        assert ok is False and "월" in error
        ok, error = InputValidator.validate_card_expire("2001")
        assert ok is False and "만료" in error
        ok, error = InputValidator.validate_card_expire("12/2")
        assert ok is False

    def test_password(self):
        assert InputValidator.validate_card_password("12") == (True, None)
        assert InputValidator.validate_card_password("1")[0] is False
        assert InputValidator.validate_card_password("1234")[0] is False
        assert InputValidator.validate_card_password("ab")[0] is False

    def test_verify_number(self):
        assert InputValidator.validate_card_verify_number("900101") == (True, None)
        assert InputValidator.validate_card_verify_number("123-45-67890") == (True, None)
        assert InputValidator.validate_card_verify_number("991340")[0] is False  # month 13
        assert InputValidator.validate_card_verify_number("12345")[0] is False


class TestCardInfo:
    def test_masked_and_corporate(self):
        personal = CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="900101")
        assert personal.masked == "****1111"
        assert personal.is_corporate is False
        corporate = CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="1234567890")
        assert corporate.is_corporate is True

    def test_repr_never_shows_full_number(self):
        card = CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="900101")
        text = repr(card)
        assert "4111111111111111" not in text and "900101" not in text and "****1111" in text

    def test_round_trip(self):
        card = CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="900101", installment=3)
        assert CardInfo.from_dict(json.loads(json.dumps(card.to_dict()))) == card


# ---------------------------------------------------------- korail pay wrapper

def _card():
    return CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="900101")


def _reservation(wct_no="01", price=59800, waiting=False):
    reservation = MagicMock()
    reservation.rsv_id = "12345678"
    reservation.wct_no = wct_no
    reservation.price = price
    reservation.is_waiting = waiting
    return reservation


@pytest.fixture
def service():
    svc = KorailService()
    fake = MagicMock()
    svc._korail_instance = fake
    svc._logged_in = True
    svc._username, svc._password = "010-0000-0000", "pw"
    return svc, fake


class TestPayReservation:
    def test_pays_with_pykorail_card(self, service):
        svc, fake = service
        reservation = _reservation()

        assert svc.pay_reservation(reservation, _card()) == 59800

        fake.reservations.pay.assert_called_once()
        passed_reservation, passed_card = fake.reservations.pay.call_args.args
        assert passed_reservation is reservation
        assert passed_card.number == "4111111111111111"
        assert passed_card.expire == "2812"
        assert passed_card.password == "12"
        assert passed_card.verify_number == "900101"
        assert passed_card.installment == 0
        assert passed_card.is_corporate is False

    def test_corporate_card_flag(self, service):
        svc, fake = service
        card = CardInfo(number="4111111111111111", expire="2812", password="12", verify_number="1234567890")
        svc.pay_reservation(_reservation(), card)
        assert fake.reservations.pay.call_args.args[1].is_corporate is True

    def test_missing_wct_no_is_looked_up(self, service):
        svc, fake = service
        complete = _reservation(wct_no="07", price=1000)
        fake.reservations.find.return_value = complete

        assert svc.pay_reservation(_reservation(wct_no=None), _card()) == 1000

        fake.reservations.find.assert_called_once_with("12345678")
        assert fake.reservations.pay.call_args.args[0] is complete

    def test_missing_reservation_after_lookup(self, service):
        svc, fake = service
        fake.reservations.find.return_value = None
        with pytest.raises(PaymentError, match="찾을 수 없습니다"):
            svc.pay_reservation(_reservation(wct_no=None), _card())

    def test_waiting_list_is_not_payable(self, service):
        svc, fake = service
        with pytest.raises(PaymentError, match="예약대기"):
            svc.pay_reservation(_reservation(waiting=True), _card())
        fake.reservations.pay.assert_not_called()

    def test_korail_refusal_is_payment_error_with_code(self, service):
        svc, fake = service
        fake.reservations.pay.side_effect = KorailError("카드 한도 초과", "WRP000123")
        with pytest.raises(PaymentError, match="한도 초과"):
            svc.pay_reservation(_reservation(), _card())

    def test_session_expiry_relogins_and_retries_once(self, service):
        svc, fake = service
        fake.reservations.pay.side_effect = [NeedToLoginError(code="P058"), None]

        assert svc.pay_reservation(_reservation(), _card()) == 59800

        fake.login.assert_called_once_with("010-0000-0000", "pw")
        assert fake.reservations.pay.call_count == 2

    def test_persistent_session_expiry_fails(self, service):
        svc, fake = service
        fake.reservations.pay.side_effect = NeedToLoginError(code="P058")
        with pytest.raises(PaymentError, match="세션 만료"):
            svc.pay_reservation(_reservation(), _card())

    def test_access_restriction_propagates(self, service):
        svc, fake = service
        fake.reservations.pay.side_effect = AccessRestrictedError(403, "VPN", "-2000")
        with pytest.raises(KorailAccessRestrictedError):
            svc.pay_reservation(_reservation(), _card())

    def test_http_error_is_payment_error(self, service):
        svc, fake = service
        fake.reservations.pay.side_effect = HttpStatusError(500, "boom")
        with pytest.raises(PaymentError, match="서버 오류"):
            svc.pay_reservation(_reservation(), _card())

    def test_invalid_card_detected_before_request(self, service):
        svc, fake = service
        bad = CardInfo(number="4111111111111111", expire="", password="12", verify_number="900101")
        with pytest.raises(PaymentError, match="카드 정보"):
            svc.pay_reservation(_reservation(), bad)
        fake.reservations.pay.assert_not_called()


# ----------------------------------------------------------------- worker bits

class TestWorkerAutoPayment:
    def _worker(self, card=None):
        """Build a ReservationWorker without running __init__."""
        from worker.reservation_worker import ReservationWorker

        worker = ReservationWorker.__new__(ReservationWorker)
        worker.card = card
        worker.korail = Mock()
        worker.user_id = 1
        worker.seat_strategy = "consecutive"
        worker.notifier = Mock()
        worker.storage = Mock()
        return worker

    def test_load_card_from_env_and_scrub(self):
        from worker.reservation_worker import ReservationWorker

        with patch.dict(os.environ, {"KORAIL_BOT_CARD": json.dumps(_card().to_dict())}):
            card = ReservationWorker._load_card_from_env()
            assert card == _card()
            assert "KORAIL_BOT_CARD" not in os.environ

    def test_load_card_absent_or_malformed(self):
        from worker.reservation_worker import ReservationWorker

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KORAIL_BOT_CARD", None)
            assert ReservationWorker._load_card_from_env() is None
        with patch.dict(os.environ, {"KORAIL_BOT_CARD": "{not json"}):
            assert ReservationWorker._load_card_from_env() is None

    def test_success_receipt(self):
        worker = self._worker(_card())
        worker.korail.pay_reservation.return_value = 59800
        paid, text = worker._try_auto_payment(_reservation())
        assert paid is True
        assert "자동 결제 완료" in text and "****1111" in text and "59,800원" in text
        assert "4111111111111111" not in text

    def test_failure_falls_back_to_manual_instructions(self):
        worker = self._worker(_card())
        worker.korail.pay_reservation.side_effect = PaymentError("카드 한도 초과 (WRP000123)")
        paid, text = worker._try_auto_payment(_reservation())
        assert paid is False
        assert "자동 결제 실패" in text and "한도 초과" in text
        assert "korailbot paid" in text  # manual payment instructions follow

    def test_access_restriction_is_reported_not_raised(self):
        worker = self._worker(_card())
        worker.korail.pay_reservation.side_effect = KorailAccessRestrictedError("blocked")
        paid, text = worker._try_auto_payment(_reservation())
        assert paid is False and "이용 제한" in text

    def test_no_card_means_no_attempt(self):
        worker = self._worker(None)
        assert worker._try_auto_payment(_reservation()) == (False, "")
        worker.korail.pay_reservation.assert_not_called()

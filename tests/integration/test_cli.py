"""Integration tests for the korailbot CLI (Redis-backed, worker spawn and Korail mocked)."""
import json
import time
from unittest.mock import Mock, patch

import pytest

import cli
from config.settings import settings
from models import CardInfo, KorailCredentials, PaymentStatus, ReservationResult, RunningReservation, TrainSearchParams
from storage import RedisStorage

USER = settings.LOCAL_USER_ID
FUTURE_EXPIRE = "3512"


def _next_week():
    from datetime import datetime, timedelta
    return (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")


@pytest.fixture
def storage():
    s = RedisStorage()
    yield s
    s.redis.flushdb()


@pytest.fixture
def run(capsys):
    def _run(*argv):
        code = cli.main(list(argv))
        out = capsys.readouterr().out
        return code, out
    return _run


class TestLogin:
    def test_login_saves_encrypted_credentials(self, storage, run):
        with patch('services.korail_service.KorailService.login', return_value=True):
            code, out = run("login", "--id", "010-1234-5678", "--password", "pw")
        assert code == 0 and "로그인 성공" in out
        creds = storage.get_credentials(USER)
        assert creds == KorailCredentials(korail_id="010-1234-5678", korail_pw="pw")
        assert "pw" not in storage.redis.get(f"credentials:{USER}")

    def test_login_failure(self, storage, run):
        with patch('services.korail_service.KorailService.login', return_value=False), \
             patch('services.korail_service.KorailService.last_login_error', "비밀번호 오류", create=True):
            code, out = run("login", "--id", "010-1234-5678", "--password", "bad")
        assert code == 1 and "로그인 실패" in out
        assert storage.get_credentials(USER) is None

    def test_logout(self, storage, run):
        storage.save_credentials(USER, KorailCredentials("a", "b"))
        code, out = run("logout")
        assert code == 0 and storage.get_credentials(USER) is None


class TestStart:
    def _login(self, storage):
        storage.save_credentials(USER, KorailCredentials(korail_id="010-1234-5678", korail_pw="pw"))

    def test_start_requires_saved_credentials(self, storage, run):
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900", "-y")
        assert code == 1 and "korailbot login" in out

    def test_start_validates_arguments(self, storage, run):
        self._login(storage)
        code, out = run("start", "--date", "2026", "--from", "서울", "--to", "부산", "--time", "0900", "-y")
        assert code == 1 and "--date" in out
        code, out = run("start", "--date", _next_week(), "--from", "없는역", "--to", "부산", "--time", "0900", "-y")
        assert code == 1 and "--from" in out
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900", "--until", "0800", "-y")
        assert code == 1 and "--until" in out
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "서울", "--time", "0900", "-y")
        assert code == 1 and "같습니다" in out

    @patch('services.reservation_service.ReservationService.start')
    def test_start_builds_params_and_uses_saved_card(self, mock_start, storage, run):
        self._login(storage)
        card = CardInfo(number="4111111111111111", expire=FUTURE_EXPIRE, password="12", verify_number="900101", saved_at=1.0)
        storage.save_saved_card(USER, card)
        mock_start.return_value = RunningReservation(chat_id=USER, process_id=77, korail_id="010-1234-5678",
                                                     search_params=TrainSearchParams("20991231", "서울", "부산", "090000"))

        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900",
                        "--until", "1800", "--train", "all", "--seat", "special-first", "--passengers", "3",
                        "--strategy", "random", "-y")

        assert code == 0 and "pid=77" in out
        creds, params, passed_card = mock_start.call_args.args
        assert creds.korail_id == "010-1234-5678"
        assert params.dep_time == "090000" and params.max_dep_time == "1800"
        assert params.train_type == "TrainType.ALL" and params.special_option == "ReserveOption.SPECIAL_FIRST"
        assert params.passenger_count == 3 and params.seat_strategy == "random"
        assert passed_card == card
        assert "카드 자동 결제 (****1111)" in out

    @patch('services.reservation_service.ReservationService.start')
    def test_no_pay_skips_card_and_single_passenger_forces_consecutive(self, mock_start, storage, run):
        self._login(storage)
        storage.save_saved_card(USER, CardInfo("4111111111111111", FUTURE_EXPIRE, "12", "900101"))
        mock_start.return_value = RunningReservation(USER, 1, "x", TrainSearchParams("20991231", "서울", "부산", "090000"))
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900",
                        "--strategy", "random", "--no-pay", "-y")
        assert code == 0
        _, params, passed_card = mock_start.call_args.args
        assert passed_card is None and params.seat_strategy == "consecutive"
        assert "직접 결제" in out

    def test_pay_without_card_fails(self, storage, run):
        self._login(storage)
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900", "--pay", "-y")
        assert code == 1 and "card set" in out

    @patch('services.reservation_service.ReservationService.start', side_effect=RuntimeError("이미 진행 중인 예약이 있습니다."))
    def test_start_while_running(self, mock_start, storage, run):
        self._login(storage)
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900", "-y")
        assert code == 1 and "이미 진행 중" in out

    @patch('builtins.input', return_value="n")
    @patch('services.reservation_service.ReservationService.start')
    def test_confirmation_declined(self, mock_start, _input, storage, run):
        self._login(storage)
        code, out = run("start", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900")
        assert code == 1 and "취소" in out
        mock_start.assert_not_called()


class TestStatusCancelPaid:
    def test_status_shows_running_and_last_result(self, storage, run):
        storage.save_running_reservation(RunningReservation(
            chat_id=USER, process_id=4242, korail_id="010-1234-5678",
            search_params=TrainSearchParams("20991231", "서울", "부산", "090000")))
        storage.save_last_result(USER, ReservationResult(status="partial", message="1/2번째 좌석 예약 성공"))
        storage.set_current_seat_index(USER, 0)
        with patch('services.reservation_service.ReservationService._process_alive', return_value=True):
            code, out = run("status")
        assert code == 0
        assert "진행 중" in out and "pid=4242" in out and "서울 → 부산" in out
        assert "1번째 좌석 결제 대기" in out and "1/2번째 좌석 예약 성공" in out

    def test_status_empty(self, storage, run):
        code, out = run("status")
        assert code == 0 and "진행 중인 예약 검색이 없습니다" in out and "korailbot login" in out

    def test_cancel(self, storage, run):
        storage.save_running_reservation(RunningReservation(USER, 999999999, "x", TrainSearchParams("20991231", "서울", "부산", "090000")))
        code, out = run("cancel")
        assert code == 0 and "중단했습니다" in out
        assert storage.get_running_reservation(USER) is None
        assert storage.get_last_result(USER).status == "cancelled"

    def test_cancel_nothing(self, storage, run):
        code, out = run("cancel")
        assert code == 0 and "없습니다" in out

    def test_paid(self, storage, run):
        storage.save_payment_status(PaymentStatus(chat_id=USER, completed=False, reminder_active=True))
        code, out = run("paid")
        assert code == 0 and "리마인더" in out
        assert storage.get_payment_status(USER).completed is True


class TestCard:
    def test_card_set_show_delete(self, storage, run):
        answers = iter(["4111-1111-1111-1111", "12/35", "12", "900101"])
        with patch('cli.getpass.getpass', side_effect=lambda prompt="": next(answers)):
            code, out = run("card", "set")
        assert code == 0 and "****1111" in out
        card = storage.get_saved_card(USER)
        assert (card.number, card.expire, card.password, card.verify_number) == ("4111111111111111", "3512", "12", "900101")
        assert "4111" not in storage.redis.get(f"saved_card:{USER}")  # encrypted at rest

        code, out = run("card", "show")
        assert code == 0 and "****1111" in out and "12/35" in out and "4111111111111111" not in out

        code, out = run("card", "delete")
        assert code == 0 and storage.get_saved_card(USER) is None

    def test_card_set_reprompts_on_invalid_input(self, storage, run):
        answers = iter(["1234", "4111111111111111", "2001", "3512", "1", "12", "991340", "900101"])
        with patch('cli.getpass.getpass', side_effect=lambda prompt="": next(answers)):
            code, out = run("card", "set")
        assert code == 0 and storage.get_saved_card(USER).expire == "3512"
        assert out.count("❌") == 4

    def test_card_set_disabled(self, storage, run):
        with patch('config.settings.settings.AUTO_PAYMENT_ENABLED', False):
            code, out = run("card", "set")
        assert code == 1 and "꺼져" in out


class TestKakao:
    def test_requires_configuration(self, storage, run):
        code, out = run("kakao", "status")
        assert code == 1 and "KAKAO_REST_API_KEY" in out

    def test_link_with_code(self, storage, run):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch('services.kakao_service.KakaoService.exchange_code') as exchange, \
             patch('services.kakao_service.KakaoService.send_memo') as memo:
            code, out = run("kakao", "link", "--code", "abc")
        exchange.assert_called_once_with(USER, "abc")
        memo.assert_called_once()
        assert code == 0 and "연동 완료" in out

    def test_link_via_local_redirect(self, storage, run):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch('cli.webbrowser.open'), \
             patch('services.kakao_service.wait_for_oauth_redirect') as wait, \
             patch('services.kakao_service.KakaoService.exchange_code') as exchange, \
             patch('services.kakao_service.KakaoService.send_memo'):
            from services.kakao_service import KakaoService
            wait.return_value = {"code": "from-browser", "state": KakaoService(storage).build_state(USER)}
            code, out = run("kakao", "link")
        exchange.assert_called_once_with(USER, "from-browser")
        assert code == 0 and "kauth.kakao.com" in out

    def test_link_rejects_bad_state(self, storage, run):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch('cli.webbrowser.open'), \
             patch('services.kakao_service.wait_for_oauth_redirect', return_value={"code": "x", "state": "1.bad"}), \
             patch('services.kakao_service.KakaoService.exchange_code') as exchange:
            code, out = run("kakao", "link")
        exchange.assert_not_called()
        assert code == 1 and "state" in out

    def test_unlink_and_status(self, storage, run):
        from models import KakaoTokens
        storage.save_kakao_tokens(KakaoTokens(USER, "AT", "RT", time.time() + 100, time.time() + 1000))
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'):
            code, out = run("kakao", "status")
            assert code == 0 and "연동됨" in out
            code, out = run("kakao", "unlink")
            assert code == 0 and storage.get_kakao_tokens(USER) is None

    def test_test_requires_link(self, storage, run):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'):
            code, out = run("kakao", "test")
        assert code == 1 and "kakao link" in out


class TestLogs:
    def test_logs_latest_file(self, storage, run, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "worker-20260101-000000.log").write_text("old\n")
        time.sleep(0.01)
        (log_dir / "worker-20260101-000001.log").write_text("line1\nline2\nline3\n")
        with patch('config.settings.settings.LOG_DIR', str(log_dir)):
            code, out = run("logs", "-n", "2")
        assert code == 0 and "line2" in out and "line3" in out and "line1" not in out and "old" not in out

    def test_logs_none(self, storage, run, tmp_path):
        with patch('config.settings.settings.LOG_DIR', str(tmp_path / "nope")):
            code, out = run("logs")
        assert code == 0 and "로그가 없습니다" in out


def _fake_train(dep="090000", general=True):
    from types import SimpleNamespace
    return SimpleNamespace(
        train_type_name="KTX", train_no="101", dep_time=dep, arr_time="113000", dep_name="서울", arr_name="부산",
        run_date=_next_week(), wait_reserve_flag=-1, has_general_seat=lambda: general, has_special_seat=lambda: False,
        has_seat=lambda: general, has_general_waiting_list=lambda: False, has_waiting_list=lambda: False,
    )


class TestSearch:
    def test_prints_timetable_without_login(self, storage, run):
        trains = [_fake_train("090000"), _fake_train("130000", general=False)]
        with patch('services.korail_service.KorailService.search_timetable', return_value=trains) as search, \
             patch('services.korail_service.KorailService.login') as login:
            code, out = run("search", "--date", _next_week(), "--from", "서울", "--to", "부산", "--time", "0900", "--until", "1200")
        assert code == 0
        assert "🟢 KTX 101  09:00→11:30  서울→부산  일반실 ○" in out
        assert "13:00" not in out  # --until filter applied
        assert "총 1편, 좌석 있음 1편" in out
        login.assert_not_called()
        assert search.call_args.kwargs["train_type"] == "100"

    def test_validates_arguments(self, storage, run):
        code, out = run("search", "--date", "20200101", "--from", "서울", "--to", "부산")
        assert code == 1 and "--date" in out
        code, out = run("search", "--date", _next_week(), "--from", "서울", "--to", "서울")
        assert code == 1 and "같습니다" in out

    def test_reports_korail_errors(self, storage, run):
        from services.korail_service import KorailAccessRestrictedError
        with patch('services.korail_service.KorailService.search_timetable', side_effect=KorailAccessRestrictedError("-2000 매크로")):
            code, out = run("search", "--date", _next_week(), "--from", "서울", "--to", "부산")
        assert code == 1 and "코레일 이용 제한" in out


class TestReservations:
    def test_requires_credentials(self, storage, run):
        code, out = run("reservations")
        assert code == 1 and "korailbot login" in out

    def test_lists_reservations(self, storage, run):
        from types import SimpleNamespace
        storage.save_credentials(USER, KorailCredentials("010-1234-5678", "pw"))
        reservation = SimpleNamespace(
            rsv_id="12345678", train=_fake_train(), price=59800, seat_no_count=1, is_waiting=False,
            buy_limit_date=_next_week(), buy_limit_time="233000", seats=(SimpleNamespace(car="3", seat="7A"),),
        )
        with patch('services.korail_service.KorailService.login', return_value=True) as login, \
             patch('services.korail_service.KorailService.list_reservations', return_value=[reservation]):
            code, out = run("reservations")
        assert code == 0 and "[12345678] KTX 101" in out and "3호차 7A · 59,800원" in out
        login.assert_called_once_with("010-1234-5678", "pw")

    def test_login_failure(self, storage, run):
        storage.save_credentials(USER, KorailCredentials("010-1234-5678", "pw"))
        with patch('services.korail_service.KorailService.login', return_value=False), \
             patch('services.korail_service.KorailService.last_login_error', "앱 업데이트", create=True):
            code, out = run("reservations")
        assert code == 1 and "로그인 실패" in out


class TestStations:
    def test_search(self, storage, run):
        code, out = run("stations", "광주")
        assert code == 0 and "광주송정" in out

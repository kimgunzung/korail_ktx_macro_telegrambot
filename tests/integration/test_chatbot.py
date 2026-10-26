"""Integration tests for the chat logic (pairing, commands, reservation wizard), Redis-backed."""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from chatbot.logic import ChatBot, HELP_TEXT, create_pairing_code
from chatbot.responses import Reply
from config.settings import settings
from models import CardInfo, KorailCredentials, RunningReservation, TrainSearchParams
from services import KakaoService, ReservationService
from storage import RedisStorage

USER = settings.LOCAL_USER_ID
KEY = "123456789"  # Telegram chat id as string


def _next_week():
    return (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")


def _labels(reply: Reply):
    return [label for label, _ in reply.quick_replies]


@pytest.fixture
def storage():
    s = RedisStorage()
    yield s
    s.redis.flushdb()


@pytest.fixture
def bot(storage):
    return ChatBot(storage, ReservationService(storage, USER), KakaoService(storage, session=Mock()))


@pytest.fixture
def paired(storage):
    storage.add_paired_user(KEY)
    storage.save_credentials(USER, KorailCredentials("010-1234-5678", "pw"))


class TestPairing:
    def test_unpaired_chat_is_refused(self, bot):
        r = bot.handle(KEY, "상태")
        assert isinstance(r, Reply)
        assert "등록되지 않은 사용자" in r.text and "korailbot pair" in r.text

    def test_pair_with_valid_code(self, bot, storage):
        code = create_pairing_code(storage)
        r = bot.handle(KEY, f"등록 {code}")
        assert "등록되었습니다" in r.text
        assert KEY in storage.get_paired_users()
        assert storage.get_pairing_code() is None  # single use

    def test_pair_with_wrong_or_expired_code(self, bot, storage):
        create_pairing_code(storage)
        assert "올바르지 않거나" in bot.handle(KEY, "등록 000000").text
        assert KEY not in storage.get_paired_users()
        storage.set_pairing_code(None)
        assert "올바르지 않거나" in bot.handle(KEY, "등록 123456").text

    def test_allowed_chat_ids_env(self, bot):
        with patch('config.settings.settings.TELEGRAM_ALLOWED_CHAT_IDS', [KEY]):
            assert "명령어" in bot.handle(KEY, "도움말").text

    def test_missing_chat_key(self, bot):
        assert "사용자 정보" in bot.handle(None, "상태").text


class TestCommands:
    def test_help(self, bot, paired):
        r = bot.handle(KEY, "도움말")
        assert r.text == HELP_TEXT and "예약" in _labels(r)
        assert "연동" not in r.text  # Kakao linking is CLI-only now

    def test_unknown(self, bot, paired):
        assert "모르겠어요" in bot.handle(KEY, "안녕").text

    def test_status(self, bot, paired):
        r = bot.handle(KEY, "상태")
        assert "진행 중인 예약 검색이 없습니다" in r.text and "010-1234-5678" in r.text
        assert "예약" in _labels(r)

    def test_status_while_running(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 4242, "010-1234-5678", TrainSearchParams("20991231", "서울", "부산", "090000")))
        with patch.object(ReservationService, '_process_alive', return_value=True):
            r = bot.handle(KEY, "상태")
        assert "진행 중" in r.text and "서울 → 부산" in r.text
        assert "취소" in _labels(r)

    def test_cancel(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 999999999, "x", TrainSearchParams("20991231", "서울", "부산", "090000")))
        assert "중단했습니다" in bot.handle(KEY, "취소").text
        assert storage.get_running_reservation(USER) is None
        assert storage.get_last_result(USER).status == "cancelled"
        assert "없습니다" in bot.handle(KEY, "취소").text

    def test_paid(self, bot, paired, storage):
        storage.set_current_seat_index(USER, 0)
        assert "1번째 좌석" in bot.handle(KEY, "결제완료").text
        assert storage.is_payment_ready(USER, 0)

    def test_card(self, bot, paired, storage):
        assert "저장된 카드가 없습니다" in bot.handle(KEY, "카드").text
        storage.save_saved_card(USER, CardInfo("4111111111111111", "3512", "12", "900101", saved_at=1.0))
        text = bot.handle(KEY, "카드").text
        assert "****1111" in text and "4111111111111111" not in text

    def test_timetable_lookup(self, storage, paired):
        from types import SimpleNamespace
        train = SimpleNamespace(
            train_type_name="KTX", train_no="101", dep_time="090000", arr_time="113000", dep_name="서울", arr_name="부산",
            run_date=_next_week(), wait_reserve_flag=-1, has_general_seat=lambda: False, has_special_seat=lambda: True,
            has_seat=lambda: True, has_general_waiting_list=lambda: False, has_waiting_list=lambda: False,
        )
        korail = Mock()
        korail.search_timetable.return_value = [train]
        korail.filter_until.return_value = [train]
        bot = ChatBot(storage, ReservationService(storage, USER), KakaoService(storage, session=Mock()),
                      korail_factory=lambda: korail)
        r = bot.handle(KEY, f"조회 {_next_week()} 서울 부산 0900 1200")
        assert "🟢 KTX 101  09:00→11:30  서울→부산  일반실 매진 / 특실 ○" in r.text
        assert r.quick_replies[0] == ("예약", f"예약 {_next_week()} 서울 부산 0900 1200")
        korail.search_timetable.assert_called_once_with(_next_week(), "서울", "부산", dep_time="090000")
        korail.login.assert_not_called()
        assert "형식: 조회" in bot.handle(KEY, "조회 서울 부산").text
        assert "❌" in bot.handle(KEY, "조회 20200101 서울 부산 0900").text

    def test_reservation_list(self, storage, paired):
        from types import SimpleNamespace
        korail = Mock()
        korail.login.return_value = True
        korail.list_reservations.return_value = []
        bot = ChatBot(storage, ReservationService(storage, USER), KakaoService(storage, session=Mock()),
                      korail_factory=lambda: korail)
        assert "결제 대기 중인 예약이 없습니다" in bot.handle(KEY, "예약목록").text
        korail.login.assert_called_once_with("010-1234-5678", "pw")
        korail.login.return_value = False
        korail.last_login_error = "앱 업데이트 필요"
        assert "로그인 실패: 앱 업데이트 필요" in bot.handle(KEY, "내예약").text

    def test_reservation_list_requires_credentials(self, bot, storage):
        storage.add_paired_user(KEY)
        assert "korailbot login" in bot.handle(KEY, "예약목록").text

    def test_error_is_reported_not_raised(self, bot, paired):
        with patch.object(ReservationService, 'running', side_effect=RuntimeError("redis down")):
            r = bot.handle(KEY, "상태")
        assert "오류" in r.text and "redis down" in r.text


class TestWizard:
    def test_requires_credentials(self, bot, storage):
        storage.add_paired_user(KEY)
        assert "korailbot login" in bot.handle(KEY, "예약").text

    def test_refused_while_running(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 4242, "x", TrainSearchParams("20991231", "서울", "부산", "090000")))
        with patch.object(ReservationService, '_process_alive', return_value=True):
            assert "이미 예약 검색이 진행 중" in bot.handle(KEY, "예약").text

    @patch.object(ReservationService, 'start')
    def test_full_wizard_single_passenger_no_card(self, start, bot, paired, storage):
        start.return_value = RunningReservation(USER, 7, "x", TrainSearchParams("20991231", "서울", "부산", "090000"))
        date = _next_week()
        assert "출발일" in bot.handle(KEY, "예약").text
        assert "출발역" in bot.handle(KEY, date).text
        assert "도착역" in bot.handle(KEY, "서울").text
        assert "시작 시각" in bot.handle(KEY, "부산").text
        assert "종료 시각" in bot.handle(KEY, "0900").text
        assert "열차 종류" in bot.handle(KEY, "없음").text
        assert "좌석 종류" in bot.handle(KEY, "1").text
        assert "탑승 인원" in bot.handle(KEY, "3").text
        r = bot.handle(KEY, "1")
        assert "이대로 시작할까요" in r.text and "직접 결제" in r.text
        assert "서울 → 부산" in r.text and "특실 우선" in r.text

        r = bot.handle(KEY, "예")
        assert "예약 검색을 시작했습니다" in r.text and "결제완료" in r.text and "이 채팅으로" in r.text
        creds, params, card = start.call_args.args
        assert creds.korail_id == "010-1234-5678"
        assert params.dep_date == date and params.dep_time == "090000" and params.max_dep_time == "2400"
        assert params.special_option == "ReserveOption.SPECIAL_FIRST" and params.passenger_count == 1
        assert card is None
        assert storage.get_chat_state(KEY) is None

    @patch.object(ReservationService, 'start')
    def test_one_line_form_with_card_and_random(self, start, bot, paired, storage):
        start.return_value = RunningReservation(USER, 7, "x", TrainSearchParams("20991231", "서울", "부산", "090000"))
        storage.save_saved_card(USER, CardInfo("4111111111111111", "3512", "12", "900101", saved_at=1.0))
        r = bot.handle(KEY, f"예약 {_next_week()} 광명 동대구 1700 2100")
        assert "열차 종류" in r.text
        bot.handle(KEY, "2")
        bot.handle(KEY, "2")
        assert "좌석 배치" in bot.handle(KEY, "2명").text
        r = bot.handle(KEY, "랜덤")
        assert "자동 결제할까요" in r.text and "****1111" in r.text
        assert "카드 자동 결제 (****1111)" in bot.handle(KEY, "예").text
        assert "시작했습니다" in bot.handle(KEY, "시작").text
        _, params, card = start.call_args.args
        assert (params.src_locate, params.dst_locate, params.dep_time, params.max_dep_time) == ("광명", "동대구", "170000", "2100")
        assert params.train_type == "TrainType.ALL" and params.special_option == "ReserveOption.GENERAL_ONLY"
        assert params.passenger_count == 2 and params.seat_strategy == "random"
        assert card.number == "4111111111111111"

    def test_one_line_form_validation_error(self, bot, paired, storage):
        r = bot.handle(KEY, "예약 2026 서울 부산")
        assert "❌" in r.text and "한 줄 형식" in r.text
        assert storage.get_chat_state(KEY) is None

    def test_invalid_input_repeats_step_with_error_prefix(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        r = bot.handle(KEY, "어제")
        assert r.prefix_lines and r.prefix_lines[0].startswith("❌")
        assert "출발일" in r.text and "❌" in r.full_text
        assert storage.get_chat_state(KEY)["step"] == "date"
        bot.handle(KEY, _next_week())
        bot.handle(KEY, "서울")
        assert "같습니다" in bot.handle(KEY, "서울").full_text
        bot.handle(KEY, "부산")
        bot.handle(KEY, "1800")
        assert "빠릅니다" in bot.handle(KEY, "0900").full_text

    @patch.object(ReservationService, 'start')
    def test_decline_at_confirmation(self, start, bot, paired, storage):
        bot.handle(KEY, f"예약 {_next_week()} 서울 부산 0900 없음")
        bot.handle(KEY, "1"); bot.handle(KEY, "1"); bot.handle(KEY, "1")
        assert "시작하지 않았습니다" in bot.handle(KEY, "아니오").text
        start.assert_not_called()
        assert storage.get_chat_state(KEY) is None

    def test_reset_clears_state(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        assert storage.get_chat_state(KEY) is not None
        bot.handle(KEY, "처음부터")
        assert storage.get_chat_state(KEY) is None

    def test_global_commands_work_inside_wizard(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        assert "진행 중인 예약 검색이 없습니다" in bot.handle(KEY, "상태").text
        assert storage.get_chat_state(KEY)["step"] == "date"

    @patch.object(ReservationService, 'start', side_effect=RuntimeError("이미 진행 중인 예약이 있습니다."))
    def test_start_failure_reported(self, start, bot, paired, storage):
        bot.handle(KEY, f"예약 {_next_week()} 서울 부산 0900 없음")
        bot.handle(KEY, "1"); bot.handle(KEY, "1"); bot.handle(KEY, "1")
        assert "이미 진행 중" in bot.handle(KEY, "예").text


class TestCliPairing:
    def test_pair_creates_code(self, storage, capsys):
        import cli
        assert cli.main(["pair"]) == 0
        out = capsys.readouterr().out
        code = storage.get_pairing_code()
        assert code and f"등록 {code}" in out
        assert "TELEGRAM_BOT_TOKEN" in out  # not configured in tests -> hint
        assert cli.main(["unpair"]) == 0

    def test_telegram_requires_token(self, storage, capsys):
        import cli
        assert cli.main(["telegram"]) == 1
        assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out

    def test_telegram_runs_polling(self, storage, capsys):
        import cli
        with patch('config.settings.settings.TELEGRAM_BOT_TOKEN', '123:abc'), \
             patch('telegram_bot.runner.run_polling', side_effect=KeyboardInterrupt) as run:
            assert cli.main(["telegram"]) == 0
        run.assert_called_once()
        assert "korailbot pair" in capsys.readouterr().out

"""Integration tests for the KakaoTalk chatbot (Open Builder skill): bot logic, dispatcher, HTTP."""
import json
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from config.settings import settings
from kakao_skill.bot import KakaoBot, create_pairing_code, HELP_TEXT
from kakao_skill.server import SkillApp, make_server
from models import CardInfo, KorailCredentials, RunningReservation, TrainSearchParams
from services import KakaoService, ReservationService
from storage import RedisStorage

USER = settings.LOCAL_USER_ID
KEY = "botUserKey-abc123"


def _next_week():
    return (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")


def _payload(text, key=KEY):
    return {"userRequest": {"utterance": text, "user": {"id": key, "type": "botUserKey"}}, "bot": {"id": "b"}}


def _texts(response):
    return "\n".join(o["simpleText"]["text"] for o in response["template"]["outputs"])


def _replies(response):
    return [q["label"] for q in response["template"].get("quickReplies", [])]


@pytest.fixture
def storage():
    s = RedisStorage()
    yield s
    s.redis.flushdb()


@pytest.fixture
def bot(storage):
    reservation = ReservationService(storage, USER)
    kakao = KakaoService(storage, session=Mock())
    return KakaoBot(storage, reservation, kakao)


@pytest.fixture
def paired(storage):
    storage.add_paired_user(KEY)
    storage.save_credentials(USER, KorailCredentials("010-1234-5678", "pw"))


class TestPairing:
    def test_unpaired_user_is_refused(self, bot):
        r = bot.handle(KEY, "상태")
        assert "등록되지 않은 사용자" in _texts(r)
        assert "korailbot kakao pair" in _texts(r)

    def test_pair_with_valid_code(self, bot, storage):
        code = create_pairing_code(storage)
        r = bot.handle(KEY, f"등록 {code}")
        assert "등록되었습니다" in _texts(r)
        assert KEY in storage.get_paired_users()
        assert storage.get_pairing_code() is None  # single use

    def test_pair_with_wrong_or_expired_code(self, bot, storage):
        create_pairing_code(storage)
        assert "올바르지 않거나" in _texts(bot.handle(KEY, "등록 000000"))
        assert KEY not in storage.get_paired_users()
        storage.set_pairing_code(None)
        assert "올바르지 않거나" in _texts(bot.handle(KEY, "등록 123456"))

    def test_allowed_users_env(self, bot):
        with patch('config.settings.settings.KAKAO_SKILL_ALLOWED_USERS', [KEY]):
            assert "명령어" in _texts(bot.handle(KEY, "도움말"))

    def test_missing_user_key(self, bot):
        assert "사용자 정보" in _texts(bot.handle(None, "상태"))


class TestCommands:
    def test_help(self, bot, paired):
        r = bot.handle(KEY, "도움말")
        assert _texts(r) == HELP_TEXT and "예약" in _replies(r)

    def test_unknown(self, bot, paired):
        assert "모르겠어요" in _texts(bot.handle(KEY, "안녕"))

    def test_status(self, bot, paired, storage):
        r = bot.handle(KEY, "상태")
        text = _texts(r)
        assert "진행 중인 예약 검색이 없습니다" in text and "010-1234-5678" in text
        assert "예약" in _replies(r)

    def test_status_while_running(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 4242, "010-1234-5678", TrainSearchParams("20991231", "서울", "부산", "090000")))
        with patch.object(ReservationService, '_process_alive', return_value=True):
            r = bot.handle(KEY, "상태")
        assert "진행 중" in _texts(r) and "서울 → 부산" in _texts(r)
        assert "취소" in _replies(r)

    def test_cancel(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 999999999, "x", TrainSearchParams("20991231", "서울", "부산", "090000")))
        r = bot.handle(KEY, "취소")
        assert "중단했습니다" in _texts(r)
        assert storage.get_running_reservation(USER) is None
        assert storage.get_last_result(USER).status == "cancelled"
        assert "없습니다" in _texts(bot.handle(KEY, "취소"))

    def test_paid(self, bot, paired, storage):
        storage.set_current_seat_index(USER, 0)
        r = bot.handle(KEY, "결제완료")
        assert "1번째 좌석" in _texts(r)
        assert storage.is_payment_ready(USER, 0)

    def test_card(self, bot, paired, storage):
        assert "저장된 카드가 없습니다" in _texts(bot.handle(KEY, "카드"))
        storage.save_saved_card(USER, CardInfo("4111111111111111", "3512", "12", "900101", saved_at=1.0))
        text = _texts(bot.handle(KEY, "카드"))
        assert "****1111" in text and "4111111111111111" not in text

    def test_link_requires_config(self, bot, paired):
        assert "KAKAO_REST_API_KEY" in _texts(bot.handle(KEY, "연동"))

    def test_link_gives_url_and_code_completes(self, bot, paired):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch('config.settings.settings.KAKAO_REDIRECT_URI', 'https://bot.example.com/kakao/oauth'):
            r = bot.handle(KEY, "연동")
            assert "kauth.kakao.com/oauth/authorize" in _texts(r)
            assert "localhost" not in _texts(r)
            with patch.object(KakaoService, 'exchange_code') as exchange, patch.object(KakaoService, 'send_memo'):
                r = bot.handle(KEY, "코드 abc")
            exchange.assert_called_once_with(USER, "abc")
            assert "연동 완료" in _texts(r)

    def test_link_warns_about_localhost_redirect(self, bot, paired):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch('config.settings.settings.KAKAO_REDIRECT_URI', 'http://localhost:8080/kakao/oauth'):
            assert "localhost" in _texts(bot.handle(KEY, "연동"))

    def test_error_is_reported_not_raised(self, bot, paired):
        with patch.object(ReservationService, 'running', side_effect=RuntimeError("redis down")):
            r = bot.handle(KEY, "상태")
        assert "오류" in _texts(r) and "redis down" in _texts(r)


class TestWizard:
    def _run(self, bot, *messages):
        r = None
        for m in messages:
            r = bot.handle(KEY, m)
        return r

    def test_requires_credentials(self, bot, storage):
        storage.add_paired_user(KEY)
        assert "korailbot login" in _texts(bot.handle(KEY, "예약"))

    def test_refused_while_running(self, bot, paired, storage):
        storage.save_running_reservation(RunningReservation(USER, 4242, "x", TrainSearchParams("20991231", "서울", "부산", "090000")))
        with patch.object(ReservationService, '_process_alive', return_value=True):
            assert "이미 예약 검색이 진행 중" in _texts(bot.handle(KEY, "예약"))

    @patch.object(ReservationService, 'start')
    def test_full_wizard_single_passenger_no_card(self, start, bot, paired, storage):
        start.return_value = RunningReservation(USER, 7, "x", TrainSearchParams("20991231", "서울", "부산", "090000"))
        date = _next_week()
        r = bot.handle(KEY, "예약")
        assert "출발일" in _texts(r)
        assert "출발역" in _texts(bot.handle(KEY, date))
        assert "도착역" in _texts(bot.handle(KEY, "서울"))
        assert "시작 시각" in _texts(bot.handle(KEY, "부산"))
        assert "종료 시각" in _texts(bot.handle(KEY, "0900"))
        assert "열차 종류" in _texts(bot.handle(KEY, "없음"))
        assert "좌석 종류" in _texts(bot.handle(KEY, "1"))
        assert "탑승 인원" in _texts(bot.handle(KEY, "3"))
        r = bot.handle(KEY, "1")
        # single passenger: strategy skipped, no card: pay skipped -> confirmation
        assert "이대로 시작할까요" in _texts(r) and "직접 결제" in _texts(r)
        assert "서울 → 부산" in _texts(r) and "특실 우선" in _texts(r)

        r = bot.handle(KEY, "예")
        assert "예약 검색을 시작했습니다" in _texts(r) and "결제완료" in _texts(r)
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
        date = _next_week()
        r = bot.handle(KEY, f"예약 {date} 광명 동대구 1700 2100")
        assert "열차 종류" in _texts(r)      # date/from/to/time/until already filled
        bot.handle(KEY, "2")                 # all trains
        bot.handle(KEY, "2")                 # general only
        r = bot.handle(KEY, "2명")
        assert "좌석 배치" in _texts(r)
        r = bot.handle(KEY, "랜덤")
        assert "자동 결제할까요" in _texts(r) and "****1111" in _texts(r)
        r = bot.handle(KEY, "예")
        assert "카드 자동 결제 (****1111)" in _texts(r)
        r = bot.handle(KEY, "시작")
        assert "시작했습니다" in _texts(r)
        _, params, card = start.call_args.args
        assert (params.src_locate, params.dst_locate, params.dep_time, params.max_dep_time) == ("광명", "동대구", "170000", "2100")
        assert params.train_type == "TrainType.ALL" and params.special_option == "ReserveOption.GENERAL_ONLY"
        assert params.passenger_count == 2 and params.seat_strategy == "random"
        assert card.number == "4111111111111111"

    def test_one_line_form_validation_error(self, bot, paired, storage):
        r = bot.handle(KEY, "예약 2026 서울 부산")
        assert "❌" in _texts(r) and "한 줄 형식" in _texts(r)
        assert storage.get_chat_state(KEY) is None

    def test_invalid_input_repeats_step(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        r = bot.handle(KEY, "어제")
        assert "❌" in _texts(r) and "출발일" in _texts(r)
        assert storage.get_chat_state(KEY)["step"] == "date"
        bot.handle(KEY, _next_week())
        bot.handle(KEY, "서울")
        r = bot.handle(KEY, "서울")
        assert "같습니다" in _texts(r)
        bot.handle(KEY, "부산")
        bot.handle(KEY, "1800")
        r = bot.handle(KEY, "0900")
        assert "빠릅니다" in _texts(r)

    @patch.object(ReservationService, 'start')
    def test_decline_at_confirmation(self, start, bot, paired, storage):
        bot.handle(KEY, f"예약 {_next_week()} 서울 부산 0900 없음")
        bot.handle(KEY, "1"); bot.handle(KEY, "1"); bot.handle(KEY, "1")
        r = bot.handle(KEY, "아니오")
        assert "시작하지 않았습니다" in _texts(r)
        start.assert_not_called()
        assert storage.get_chat_state(KEY) is None

    def test_reset_clears_state(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        assert storage.get_chat_state(KEY) is not None
        bot.handle(KEY, "처음부터")
        assert storage.get_chat_state(KEY) is None

    def test_global_commands_work_inside_wizard(self, bot, paired, storage):
        bot.handle(KEY, "예약")
        assert "진행 중인 예약 검색이 없습니다" in _texts(bot.handle(KEY, "상태"))
        assert storage.get_chat_state(KEY)["step"] == "date"  # wizard still waiting

    @patch.object(ReservationService, 'start', side_effect=RuntimeError("이미 진행 중인 예약이 있습니다."))
    def test_start_failure_reported(self, start, bot, paired, storage):
        bot.handle(KEY, f"예약 {_next_week()} 서울 부산 0900 없음")
        bot.handle(KEY, "1"); bot.handle(KEY, "1"); bot.handle(KEY, "1")
        assert "이미 진행 중" in _texts(bot.handle(KEY, "예"))


class TestSkillApp:
    @pytest.fixture
    def app(self, storage):
        storage.add_paired_user(KEY)
        storage.save_credentials(USER, KorailCredentials("010-1234-5678", "pw"))
        return SkillApp(storage, ReservationService(storage, USER), KakaoService(storage, session=Mock()))

    def test_skill_dispatch(self, app):
        status, ctype, body = app.dispatch("POST", "/kakao/skill", {}, json.dumps(_payload("도움말")).encode())
        assert status == 200 and ctype.startswith("application/json")
        response = json.loads(body)
        assert response["version"] == "2.0" and "명령어" in _texts(response)

    def test_secret_path(self, app):
        with patch('config.settings.settings.KAKAO_SKILL_SECRET', 'abc'):
            assert app.dispatch("POST", "/kakao/skill", {}, b"{}")[0] == 404
            assert app.dispatch("POST", "/kakao/skill/abc", {}, json.dumps(_payload("도움말")).encode())[0] == 200

    def test_bad_json_is_handled(self, app):
        status, _, body = app.dispatch("POST", "/kakao/skill", {}, b"not json")
        assert status == 200 and "올바르지 않습니다" in _texts(json.loads(body))

    def test_healthz_and_404(self, app):
        assert app.dispatch("GET", "/healthz", {}, None)[0] == 200
        assert app.dispatch("GET", "/nope", {}, None)[0] == 404

    def test_oauth_redirect(self, app):
        with patch('config.settings.settings.KAKAO_REST_API_KEY', 'k'), \
             patch.object(KakaoService, 'exchange_code') as exchange, patch.object(KakaoService, 'send_memo'):
            state = app.kakao.build_state(USER)
            status, _, body = app.dispatch("GET", "/kakao/oauth", {"code": "xyz", "state": state}, None)
            assert status == 200 and "연동 완료" in body.decode()
            exchange.assert_called_once_with(USER, "xyz")
            status, _, body = app.dispatch("GET", "/kakao/oauth", {"code": "xyz", "state": "1.bad"}, None)
            assert status == 400
            status, _, body = app.dispatch("GET", "/kakao/oauth", {"error": "access_denied", "state": state}, None)
            assert status == 400 and "access_denied" in body.decode()

    def test_over_http(self, app):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = make_server("127.0.0.1", port, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/kakao/skill", data=json.dumps(_payload("상태")).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                response = json.loads(r.read().decode())
            assert "진행 중인 예약 검색이 없습니다" in _texts(response)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
                assert r.status == 200
        finally:
            server.shutdown()
            server.server_close()


class TestCliPairAndServe:
    def test_pair_creates_code(self, storage, capsys):
        import cli
        assert cli.main(["kakao", "pair"]) == 0
        out = capsys.readouterr().out
        code = storage.get_pairing_code()
        assert code and f"등록 {code}" in out
        assert cli.main(["kakao", "unpair"]) == 0

    def test_serve_wiring(self, storage, capsys):
        import cli
        with patch('kakao_skill.server.serve_forever', side_effect=KeyboardInterrupt) as serve:
            assert cli.main(["serve", "--port", "18090"]) == 0
        serve.assert_called_once()
        assert serve.call_args.args[1] == 18090
        assert "kakao pair" in capsys.readouterr().out

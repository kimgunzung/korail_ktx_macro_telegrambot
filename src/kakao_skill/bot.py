"""KakaoTalk chatbot logic: commands and the reservation wizard.

The Open Builder skill server (``server.py``) turns every utterance into
``KakaoBot.handle(bot_user_key, text)`` and returns the SkillResponse. State for the
step-by-step reservation wizard lives in Redis (``chat_state``), keyed by the Open
Builder user id, so any number of server workers/restarts keep the conversation.

Security model: only *paired* users may control the bot. Pairing is done once by
sending ``등록 <코드>`` where the code comes from ``korailbot kakao pair`` on the
machine that runs the bot (or by listing the user id in ``KAKAO_SKILL_ALLOWED_USERS``).
Credentials and card numbers are never entered through KakaoTalk (chat history is
stored on Kakao's servers); the CLI handles those.
"""
import secrets
from datetime import datetime
from typing import List, Optional, Tuple

from config.settings import settings
from kakao_skill.responses import text_response
from models import TrainSearchParams
from utils.logger import get_logger
from utils.validators import InputValidator

logger = get_logger(__name__)

PAIRING_TTL_SECONDS = 10 * 60
USER_ID = settings.LOCAL_USER_ID

HELP_TEXT = """🚄 코레일 예매 봇 명령어

• 예약 — 조건을 하나씩 물어보며 예약 검색 시작
• 예약 20261225 서울 부산 0900 1200 — 한 줄로 시작 (날짜 출발 도착 시작시각 [종료시각])
• 상태 — 진행 상황 / 마지막 결과
• 취소 — 진행 중인 검색 중단
• 결제완료 — 직접 결제 후 알림 중단 (랜덤 배치는 다음 좌석 진행)
• 카드 — 저장된 자동 결제 카드 확인
• 연동 — 예약 알림을 이 카카오 계정의 '나와의 채팅'으로 받기
• 처음부터 — 입력 중인 예약 조건 초기화
• 도움말 — 이 안내

🔒 코레일 계정과 카드 등록은 보안상 서버에서 `korailbot login`, `korailbot card set` 으로만 합니다."""

MAIN_REPLIES = ["예약", "상태", "취소", "결제완료", "도움말"]

TRAIN_OPTIONS = {"1": ("TrainType.KTX", "KTX"), "2": ("TrainType.ALL", "ALL")}
SEAT_OPTIONS = {"1": "GENERAL_FIRST", "2": "GENERAL_ONLY", "3": "SPECIAL_FIRST", "4": "SPECIAL_ONLY"}
SEAT_LABELS = {"1": "일반실 우선", "2": "일반실만", "3": "특실 우선", "4": "특실만"}

# Wizard steps in order
STEP_DATE, STEP_FROM, STEP_TO, STEP_TIME, STEP_UNTIL, STEP_TRAIN, STEP_SEAT, STEP_PASSENGERS, STEP_STRATEGY, \
    STEP_PAY, STEP_CONFIRM = ("date", "from", "to", "time", "until", "train", "seat", "passengers", "strategy",
                              "pay", "confirm")


def create_pairing_code(storage) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    storage.set_pairing_code(code, PAIRING_TTL_SECONDS)
    return code


def clear_paired_users(storage) -> int:
    return storage.clear_paired_users()


def _normalize(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def _is(text: str, *words: str) -> bool:
    n = _normalize(text)
    return any(n == _normalize(w) for w in words)


class KakaoBot:
    """Stateless entry point; conversation state is read/written through ``storage``."""

    def __init__(self, storage, reservation_service, kakao, notifier=None):
        self.storage = storage
        self.reservation = reservation_service
        self.kakao = kakao

    # ------------------------------------------------------------ dispatch

    def handle(self, bot_user_key: Optional[str], text: str) -> dict:
        """Return the SkillResponse for an utterance."""
        if not bot_user_key:
            return text_response("요청에 사용자 정보가 없습니다.")
        text = (text or "").strip()
        try:
            if not self._is_paired(bot_user_key):
                return self._handle_unpaired(bot_user_key, text)
            return self._handle_paired(bot_user_key, text)
        except Exception as e:  # never let Open Builder see a 500
            logger.error(f"Chatbot error for {bot_user_key[:6]}…: {e}", exc_info=True)
            return text_response(f"⚠️ 처리 중 오류가 발생했습니다: {type(e).__name__}: {e}", MAIN_REPLIES)

    # ------------------------------------------------------------- pairing

    def _is_paired(self, key: str) -> bool:
        return key in settings.KAKAO_SKILL_ALLOWED_USERS or key in self.storage.get_paired_users()

    def _handle_unpaired(self, key: str, text: str) -> dict:
        parts = text.split()
        if len(parts) == 2 and _is(parts[0], "등록", "pair"):
            expected = self.storage.get_pairing_code()
            if expected and secrets.compare_digest(parts[1], expected):
                self.storage.add_paired_user(key)
                self.storage.set_pairing_code(None)
                logger.info(f"Chatbot user paired: {key[:6]}…")
                return text_response("✅ 등록되었습니다! 이제 이 채팅으로 예약을 조작할 수 있습니다.\n\n" + HELP_TEXT, MAIN_REPLIES)
            logger.warning(f"Chatbot pairing failed from {key[:6]}…")
            return text_response("❌ 등록 코드가 올바르지 않거나 만료되었습니다. 서버에서 `korailbot kakao pair` 로 새 코드를 만든 뒤 다시 보내주세요.")
        return text_response(
            "🔒 등록되지 않은 사용자입니다.\n\n봇을 실행하는 컴퓨터에서 `korailbot kakao pair` 를 실행해 나온 코드를 "
            "'등록 123456' 형식으로 보내주세요. (10분 안에)"
        )

    # ------------------------------------------------------------ commands

    def _handle_paired(self, key: str, text: str) -> dict:
        state = self.storage.get_chat_state(key)

        # Global commands work in any state
        if _is(text, "도움말", "help", "?", "명령어"):
            return text_response(HELP_TEXT, MAIN_REPLIES)
        if _is(text, "처음부터", "리셋", "reset", "초기화"):
            self.storage.save_chat_state(key, None)
            return text_response("🔄 입력 중인 내용을 지웠습니다.", MAIN_REPLIES)
        if _is(text, "상태", "status", "현황"):
            return self._status()
        if _is(text, "취소", "cancel", "중단"):
            return self._cancel(key)
        if _is(text, "결제완료", "결제", "paid", "결제했어", "결제함"):
            return self._paid()
        if _is(text, "카드", "card"):
            return self._card()
        if _is(text, "연동", "link", "알림연동", "카카오연동"):
            return self._link(key)
        if _is(text, "연동해제", "unlink"):
            return self._unlink()
        if text.startswith(("코드 ", "code ")):
            return self._link_code(text.split(None, 1)[1])

        first = text.split()[0] if text.split() else ""
        if _is(first, "예약", "reserve", "검색"):
            return self._start_wizard(key, text.split()[1:])

        if state:
            return self._wizard_step(key, state, text)

        return text_response("무슨 말인지 모르겠어요. 아래 버튼을 누르거나 '도움말'을 보내주세요.", MAIN_REPLIES)

    def _status(self) -> dict:
        from services.status_view import status_lines
        lines = status_lines(self.storage, self.reservation, self.kakao, USER_ID, for_chat=True)
        running = self.reservation.running()
        replies = (["상태", "취소", "결제완료"] if running else ["예약", "상태"]) + ["도움말"]
        return text_response("\n".join(lines), replies)

    def _cancel(self, key: str) -> dict:
        self.storage.save_chat_state(key, None)
        cancelled = self.reservation.cancel()
        if not cancelled:
            return text_response("ℹ️ 진행 중인 예약 검색이 없습니다.", MAIN_REPLIES)
        from models import ReservationResult
        self.storage.save_last_result(USER_ID, ReservationResult(status="cancelled", message="카카오톡에서 예약 검색을 중단했습니다."))
        p = cancelled.search_params
        return text_response(f"✅ 예약 검색을 중단했습니다. ({p.src_locate}→{p.dst_locate} {p.dep_date})", MAIN_REPLIES)

    def _paid(self) -> dict:
        text = self.reservation.confirm_payment()
        return text_response("✅ " + text.replace("\n", "\n✅ "), ["상태", "도움말"])

    def _card(self) -> dict:
        card = self.storage.get_saved_card(USER_ID)
        if not card or not card.is_complete:
            return text_response("ℹ️ 저장된 카드가 없습니다. 서버에서 `korailbot card set` 으로 등록하세요.", MAIN_REPLIES)
        saved_at = datetime.fromtimestamp(card.saved_at).strftime("%Y-%m-%d") if card.saved_at else "-"
        return text_response(
            f"💳 저장된 자동 결제 카드\n카드번호: {card.masked}{' (법인)' if card.is_corporate else ''}\n"
            f"유효기간: {card.expire_display}\n저장일: {saved_at}\n\n교체/삭제는 서버에서 `korailbot card set` / `card delete`",
            MAIN_REPLIES,
        )

    # ------------------------------------------------------------- linking

    def _link(self, key: str) -> dict:
        if not self.kakao.enabled:
            return text_response("⚠️ 서버에 카카오 알림(KAKAO_REST_API_KEY)이 설정되어 있지 않습니다.", MAIN_REPLIES)
        linked = self.kakao.link_summary(USER_ID)
        if linked:
            return text_response(f"✅ 이미 알림이 연동되어 있습니다. (연동일 {linked})\n해제하려면 '연동해제'를 보내세요.", MAIN_REPLIES)
        url = self.kakao.get_authorize_url(USER_ID)
        hint = ""
        if not settings.KAKAO_REDIRECT_URI.startswith(("https://", "http://localhost", "http://127.0.0.1")) or \
                "localhost" in settings.KAKAO_REDIRECT_URI or "127.0.0.1" in settings.KAKAO_REDIRECT_URI:
            hint = ("\n\n⚠️ 서버의 Redirect URI가 localhost 로 설정되어 있어 휴대폰에서는 마지막 페이지가 열리지 않습니다. "
                    "그래도 주소창의 code= 뒤 값을 복사해 '코드 <값>' 으로 보내면 연동됩니다. "
                    "(서버 .env 의 KAKAO_REDIRECT_URI 를 https://<공개주소>/kakao/oauth 로 바꾸면 자동 완료)")
        return text_response(
            "💬 아래 링크를 열어 카카오 로그인 후 '카카오톡 메시지 전송' 권한에 동의하면 예약 알림이 '나와의 채팅'으로 옵니다.\n\n"
            + url + hint,
            ["상태", "도움말"],
        )

    def _link_code(self, code: str) -> dict:
        from services.kakao_service import KakaoError
        try:
            self.kakao.exchange_code(USER_ID, code.strip())
            self.kakao.send_memo(USER_ID, "🚄 코레일 예매 봇 알림 연동이 완료되었습니다.")
        except KakaoError as e:
            return text_response(f"❌ 연동 실패: {e}\n'연동'을 보내 다시 시도하세요.", MAIN_REPLIES)
        return text_response("✅ 알림 연동 완료! '나와의 채팅'에 확인 메시지를 보냈습니다.", MAIN_REPLIES)

    def _unlink(self) -> dict:
        if not self.kakao.enabled:
            return text_response("⚠️ 서버에 카카오 알림이 설정되어 있지 않습니다.", MAIN_REPLIES)
        return text_response("✅ 알림 연동을 해제했습니다." if self.kakao.unlink(USER_ID) else "ℹ️ 연동되어 있지 않습니다.", MAIN_REPLIES)

    # -------------------------------------------------------------- wizard

    def _start_wizard(self, key: str, args: List[str]) -> dict:
        if self.reservation.running():
            return text_response("⚠️ 이미 예약 검색이 진행 중입니다. '취소' 후 다시 시작하세요.", ["상태", "취소", "도움말"])
        if not self.storage.get_credentials(USER_ID):
            return text_response("⚠️ 저장된 코레일 계정이 없습니다. 서버에서 `korailbot login` 을 먼저 실행하세요.", MAIN_REPLIES)

        state: dict = {"step": STEP_DATE, "data": {}}
        # One-line form: 예약 날짜 출발 도착 시작시각 [종료시각]
        if args:
            fields = list(zip((STEP_DATE, STEP_FROM, STEP_TO, STEP_TIME, STEP_UNTIL), args[:5]))
            for step, value in fields:
                ok, error = self._apply(state, step, value)
                if not ok:
                    return text_response(f"❌ {error}\n\n한 줄 형식: 예약 20261225 서울 부산 0900 [1200]", MAIN_REPLIES)
            state["step"] = self._next_step(state)
        self.storage.save_chat_state(key, state)
        return self._prompt(state)

    STEP_ORDER = [STEP_DATE, STEP_FROM, STEP_TO, STEP_TIME, STEP_UNTIL, STEP_TRAIN, STEP_SEAT, STEP_PASSENGERS,
                  STEP_STRATEGY, STEP_PAY, STEP_CONFIRM]

    def _next_step(self, state: dict) -> str:
        data = state["data"]
        for step in self.STEP_ORDER:
            if step == STEP_STRATEGY and data.get("passengers", 1) == 1:
                data.setdefault("strategy", "consecutive")
                continue
            if step == STEP_PAY and not self._card_available():
                data.setdefault("pay", False)
                continue
            if step not in data:
                return step
        return STEP_CONFIRM

    def _card_available(self) -> bool:
        card = self.storage.get_saved_card(USER_ID)
        return bool(settings.AUTO_PAYMENT_ENABLED and card and card.is_complete)

    def _apply(self, state: dict, step: str, value: str) -> Tuple[bool, Optional[str]]:
        """Validate ``value`` for ``step`` and store it. Returns (ok, error)."""
        data = state["data"]
        value = value.strip()
        if step == STEP_DATE:
            ok, error = InputValidator.validate_date(value)
            if ok:
                data["date"] = value
            return ok, error
        if step in (STEP_FROM, STEP_TO):
            ok, error = InputValidator.validate_station_name(value)
            if ok and step == STEP_TO and value == data.get("from"):
                return False, "출발역과 도착역이 같습니다."
            if ok:
                data[step] = value
            return ok, error
        if step == STEP_TIME:
            ok, error = InputValidator.validate_time(value)
            if ok:
                data["time"] = value
            return ok, error
        if step == STEP_UNTIL:
            if _is(value, "없음", "제한없음", "2400", "무제한"):
                data["until"] = "2400"
                return True, None
            ok, error = InputValidator.validate_time(value)
            if ok and value < data.get("time", "0000"):
                return False, f"검색 종료 시각({value})이 시작 시각({data.get('time')})보다 빠릅니다."
            if ok:
                data["until"] = value
            return ok, error
        if step == STEP_TRAIN:
            choice = {"1": "1", "ktx": "1", "2": "2", "모든열차": "2", "전체": "2", "all": "2"}.get(_normalize(value))
            if not choice:
                return False, "1(KTX) 또는 2(모든 열차)를 선택해주세요."
            data["train"] = choice
            return True, None
        if step == STEP_SEAT:
            choice = _normalize(value)
            mapping = {"1": "1", "일반실우선": "1", "2": "2", "일반실만": "2", "3": "3", "특실우선": "3", "4": "4", "특실만": "4"}
            if choice not in mapping:
                return False, "1~4 중에서 선택해주세요."
            data["seat"] = mapping[choice]
            return True, None
        if step == STEP_PASSENGERS:
            digits = value.replace("명", "").strip()
            ok, error = InputValidator.validate_passenger_count(digits)
            if ok:
                data["passengers"] = int(digits)
            return ok, error
        if step == STEP_STRATEGY:
            choice = _normalize(value)
            if choice in ("1", "연속", "연속좌석", "consecutive"):
                data["strategy"] = "consecutive"
            elif choice in ("2", "랜덤", "랜덤배치", "random"):
                data["strategy"] = "random"
            else:
                return False, "1(연속 좌석) 또는 2(랜덤 배치)를 선택해주세요."
            return True, None
        if step == STEP_PAY:
            is_yes, error = InputValidator.validate_yes_no(value)
            if is_yes is None:
                return False, "예 또는 아니오로 답해주세요."
            data["pay"] = bool(is_yes)
            return True, None
        return False, "알 수 없는 단계입니다."

    def _prompt(self, state: dict) -> dict:
        step = state["step"]
        data = state["data"]
        if step == STEP_DATE:
            return text_response("📅 출발일을 8자리로 보내주세요. (예: 20261225)", ["처음부터"])
        if step == STEP_FROM:
            return text_response("🚉 출발역을 보내주세요. ('역' 제외, 예: 서울)", ["서울", "광명", "용산", "부산", "동대구", "처음부터"])
        if step == STEP_TO:
            return text_response("🏁 도착역을 보내주세요. (예: 부산)", ["부산", "서울", "동대구", "광주송정", "처음부터"])
        if step == STEP_TIME:
            return text_response("🕐 검색 시작 시각을 HHMM 으로 보내주세요. (예: 0900 → 9시 이후 출발 열차)", ["0600", "0900", "1200", "1800"])
        if step == STEP_UNTIL:
            return text_response("⏰ 검색 종료 시각을 HHMM 으로 보내주세요. 제한이 없으면 '없음'.", ["없음", "1200", "1800", "2100"])
        if step == STEP_TRAIN:
            return text_response("🚄 열차 종류: 1) KTX만  2) 모든 열차", [("1 KTX", "1"), ("2 모든 열차", "2")])
        if step == STEP_SEAT:
            return text_response("💺 좌석 종류: 1) 일반실 우선  2) 일반실만  3) 특실 우선  4) 특실만",
                                 [("1 일반실우선", "1"), ("2 일반실만", "2"), ("3 특실우선", "3"), ("4 특실만", "4")])
        if step == STEP_PASSENGERS:
            return text_response("👥 탑승 인원(성인)을 보내주세요. (1~9)", ["1", "2", "3", "4"])
        if step == STEP_STRATEGY:
            return text_response(f"🪑 {data['passengers']}명 좌석 배치: 1) 연속 좌석 (없으면 대기)  2) 랜덤 배치 (한 자리씩)",
                                 [("1 연속", "1"), ("2 랜덤", "2")])
        if step == STEP_PAY:
            card = self.storage.get_saved_card(USER_ID)
            return text_response(f"💳 저장된 카드({card.masked})로 예약 즉시 자동 결제할까요?", [("예", "예"), ("아니오", "아니오")])
        # confirm
        params = self._params(data)
        pay = data.get("pay", False)
        card = self.storage.get_saved_card(USER_ID) if pay else None
        lines = ["📋 예약 검색 조건"] + params.summary_lines() + [
            f"💳 결제: {'카드 자동 결제 (' + card.masked + ')' if card else '직접 결제 (예약 후 안내)'}",
            "", "이대로 시작할까요?",
        ]
        return text_response("\n".join(lines), [("시작", "예"), ("취소", "아니오"), "처음부터"])

    def _params(self, data: dict) -> TrainSearchParams:
        train_type, train_display = TRAIN_OPTIONS[data.get("train", "1")]
        seat = SEAT_OPTIONS[data.get("seat", "1")]
        return TrainSearchParams(
            dep_date=data["date"],
            src_locate=data["from"],
            dst_locate=data["to"],
            dep_time=data["time"] + "00",
            max_dep_time=data.get("until", "2400"),
            train_type=train_type,
            train_type_display=train_display,
            special_option=f"ReserveOption.{seat}",
            special_option_display=seat,
            passenger_count=data.get("passengers", 1),
            seat_strategy=data.get("strategy", "consecutive") if data.get("passengers", 1) > 1 else "consecutive",
        )

    def _wizard_step(self, key: str, state: dict, text: str) -> dict:
        step = state["step"]
        if step == STEP_CONFIRM:
            is_yes, _ = InputValidator.validate_yes_no(text)
            if is_yes is None and not _is(text, "시작", "go"):
                return text_response("'예'(시작) 또는 '아니오'(취소)로 답해주세요.", [("시작", "예"), ("취소", "아니오")])
            if is_yes is False:
                self.storage.save_chat_state(key, None)
                return text_response("🚫 예약 검색을 시작하지 않았습니다.", MAIN_REPLIES)
            return self._launch(key, state)

        ok, error = self._apply(state, step, text)
        if not ok:
            prompt = self._prompt(state)
            prompt["template"]["outputs"].insert(0, {"simpleText": {"text": f"❌ {error}"}})
            prompt["template"]["outputs"] = prompt["template"]["outputs"][:3]
            return prompt
        state["step"] = self._next_step(state)
        self.storage.save_chat_state(key, state)
        return self._prompt(state)

    def _launch(self, key: str, state: dict) -> dict:
        data = state["data"]
        credentials = self.storage.get_credentials(USER_ID)
        if not credentials:
            self.storage.save_chat_state(key, None)
            return text_response("⚠️ 저장된 코레일 계정이 없습니다. 서버에서 `korailbot login` 을 실행하세요.", MAIN_REPLIES)
        params = self._params(data)
        card = self.storage.get_saved_card(USER_ID) if data.get("pay") else None
        if card and not card.is_complete:
            card = None
        try:
            running = self.reservation.start(credentials, params, card)
        except RuntimeError as e:
            return text_response(f"⚠️ {e}", ["상태", "취소"])
        except Exception as e:
            logger.error(f"Failed to start worker from chatbot: {e}", exc_info=True)
            return text_response(f"❌ 예약 검색을 시작하지 못했습니다: {e}", MAIN_REPLIES)
        self.storage.save_chat_state(key, None)
        notice = ("예약 결과는 '나와의 채팅'으로 알려드립니다." if self.kakao.enabled and self.kakao.is_linked(USER_ID)
                  else "알림 연동이 없어 결과는 '상태'로 확인해야 합니다. '연동'을 보내면 카카오톡으로 알림을 받을 수 있습니다.")
        return text_response(
            f"🎯 예약 검색을 시작했습니다! ({params.src_locate}→{params.dst_locate} {params.dep_date} {params.dep_time[:4]}~{params.max_dep_time})\n"
            f"{notice}" + ("" if card else f"\n예약 성공 후 {settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제하고 '결제완료'를 보내주세요."),
            ["상태", "취소", "도움말"],
        )

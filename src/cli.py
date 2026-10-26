"""
korailbot - 코레일 KTX 매진 열차 자동 예매 (명령줄 도구)

Usage: python src/cli.py <command> [options]   (or `korailbot` via the wrapper scripts)

Commands:
  login / logout      코레일 계정 저장(암호화) / 삭제
  start               예약 검색 시작 (백그라운드 워커 실행)
  status              진행 상황과 마지막 결과 확인
  cancel              진행 중인 검색 중단
  paid                직접 결제 완료를 알림 (리마인더 중단 / 다음 좌석 진행)
  logs                워커 로그 보기
  card                자동 결제 카드 확인 / 등록 / 삭제
  kakao               카카오톡 '나에게 보내기' 연동 관리
  stations            역 이름 검색
"""
import argparse
import getpass
import os
import sys
import time
import webbrowser
from datetime import datetime
from typing import Optional

# Allow `python src/cli.py` from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import settings  # noqa: E402
from models import TrainSearchParams, KorailCredentials, CardInfo  # noqa: E402
from utils.logger import get_logger, LoggerFactory  # noqa: E402
from utils.validators import InputValidator  # noqa: E402

logger = get_logger("korailbot")

USER_ID = settings.LOCAL_USER_ID

TRAIN_CHOICES = {"ktx": ("TrainType.KTX", "KTX"), "all": ("TrainType.ALL", "ALL")}
SEAT_CHOICES = {
    "general-first": "GENERAL_FIRST",
    "general-only": "GENERAL_ONLY",
    "special-first": "SPECIAL_FIRST",
    "special-only": "SPECIAL_ONLY",
}


class CliError(Exception):
    """User-facing error; printed without a traceback and exits with status 1."""


def say(text: str = "") -> None:
    print(text)


def fail(text: str) -> None:
    raise CliError(text)


def confirm(question: str, default_yes: bool = True) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip()
    except EOFError:
        return default_yes
    is_yes, _ = InputValidator.validate_yes_no(answer)
    if is_yes is None:
        return default_yes
    return is_yes


def prompt_required(label: str, secret: bool = False, validator=None) -> str:
    """Prompt until a non-empty (and valid) value is entered."""
    while True:
        value = (getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")).strip()
        if not value:
            say("값을 입력해주세요.")
            continue
        if validator:
            ok, error = validator(value)
            if not ok:
                say(f"❌ {error}")
                continue
        return value


# ----------------------------------------------------------------- wiring

def _storage():
    from storage.redis import RedisStorage
    try:
        return RedisStorage()
    except Exception as e:
        fail(
            f"Redis에 연결할 수 없습니다 ({settings.REDIS_HOST}:{settings.REDIS_PORT}): {e}\n"
            "Redis를 실행한 뒤 다시 시도하세요. (docs/INSTALL.md 참고)"
        )


def _services(storage):
    from services import KakaoService, ReservationService
    return ReservationService(storage, USER_ID), KakaoService(storage)


# ------------------------------------------------------------------ login

def cmd_login(args) -> int:
    storage = _storage()
    korail_id = args.id or prompt_required("코레일 아이디 (휴대폰 010-1234-5678 / 이메일 / 회원번호)")
    password = args.password or prompt_required("코레일 비밀번호", secret=True)

    from services import KorailService
    say("코레일 로그인 확인 중...")
    korail = KorailService()
    if not korail.login(korail_id, password):
        fail(f"코레일 로그인 실패: {korail.last_login_error or '알 수 없음'}")

    storage.save_credentials(USER_ID, KorailCredentials(korail_id=korail_id, korail_pw=password))
    say(f"✅ 로그인 성공. 계정 정보를 암호화하여 저장했습니다. ({korail_id})")
    return 0


def cmd_logout(args) -> int:
    storage = _storage()
    had = storage.get_credentials(USER_ID) is not None
    storage.delete_credentials(USER_ID)
    say("✅ 저장된 코레일 계정 정보를 삭제했습니다." if had else "ℹ️ 저장된 계정 정보가 없습니다.")
    return 0


# ------------------------------------------------------------------ start

def _build_search_params(args) -> TrainSearchParams:
    ok, error = InputValidator.validate_date(args.date)
    if not ok:
        fail(f"--date: {error}")
    for label, station in (("--from", args.src), ("--to", args.dst)):
        ok, error = InputValidator.validate_station_name(station)
        if not ok:
            fail(f"{label}: {error}")
    if args.src == args.dst:
        fail("출발역과 도착역이 같습니다.")
    ok, error = InputValidator.validate_time(args.time)
    if not ok:
        fail(f"--time: {error}")
    until = args.until
    if until != "2400":
        ok, error = InputValidator.validate_time(until)
        if not ok:
            fail(f"--until: {error}")
        if until < args.time:
            fail(f"--until({until})이 --time({args.time})보다 빠릅니다.")
    ok, error = InputValidator.validate_passenger_count(str(args.passengers))
    if not ok:
        fail(f"--passengers: {error}")

    train_type, train_display = TRAIN_CHOICES[args.train]
    seat_display = SEAT_CHOICES[args.seat]
    strategy = args.strategy if args.passengers > 1 else "consecutive"
    return TrainSearchParams(
        dep_date=args.date,
        src_locate=args.src,
        dst_locate=args.dst,
        dep_time=args.time + "00",
        max_dep_time=until,
        train_type=train_type,
        train_type_display=train_display,
        special_option=f"ReserveOption.{seat_display}",
        special_option_display=seat_display,
        passenger_count=args.passengers,
        seat_strategy=strategy,
    )


def cmd_start(args) -> int:
    storage = _storage()
    reservation_service, kakao = _services(storage)

    params = _build_search_params(args)

    credentials = None
    if args.id and args.password:
        credentials = KorailCredentials(korail_id=args.id, korail_pw=args.password)
    elif args.id:
        credentials = KorailCredentials(korail_id=args.id, korail_pw=prompt_required("코레일 비밀번호", secret=True))
    else:
        credentials = storage.get_credentials(USER_ID)
        if not credentials:
            fail("저장된 코레일 계정이 없습니다. 먼저 `korailbot login` 을 실행하거나 --id/--password 를 지정하세요.")

    card: Optional[CardInfo] = None
    if args.pay is not False and settings.AUTO_PAYMENT_ENABLED:
        saved = storage.get_saved_card(USER_ID)
        if saved and saved.is_complete:
            card = saved
        elif args.pay is True:
            fail("저장된 카드가 없습니다. `korailbot card set` 으로 카드를 먼저 등록하세요.")
    elif args.pay is True:
        fail("이 서버에서는 자동 결제가 꺼져 있습니다 (AUTO_PAYMENT_ENABLED=False).")

    say("📋 예약 검색 조건")
    say("━━━━━━━━━━━━━━━━━━━━")
    for line in params.summary_lines():
        say(line)
    say(f"👤 계정: {credentials.korail_id}")
    say(f"💳 결제: {'카드 자동 결제 (' + card.masked + ')' if card else '직접 결제 (예약 후 안내)'}")
    say(f"📣 알림: {'카카오톡 연동됨' if kakao.enabled and kakao.is_linked(USER_ID) else '카카오톡 미연동 (로그만 기록)'}")
    say("━━━━━━━━━━━━━━━━━━━━")
    if not args.yes and not confirm("예약 검색을 시작할까요?"):
        say("취소했습니다.")
        return 1

    try:
        running = reservation_service.start(credentials, params, card)
    except RuntimeError as e:
        fail(str(e))
    say(f"🎯 예약 검색을 시작했습니다. (pid={running.process_id})")
    say(f"   로그: {reservation_service.worker_log_path(running.started_at)}")
    say("   진행 상황: korailbot status  |  로그 보기: korailbot logs -f  |  중단: korailbot cancel")
    if not card:
        say(f"   예약 성공 후 {settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제하고 `korailbot paid` 를 실행하세요.")
    return 0


# ----------------------------------------------------------------- status

def cmd_status(args) -> int:
    storage = _storage()
    reservation_service, kakao = _services(storage)

    running = reservation_service.running()
    if running:
        elapsed = datetime.now() - running.started_at
        say(f"🔍 예약 검색 진행 중 (pid={running.process_id}, {int(elapsed.total_seconds() // 60)}분 경과)")
        for line in running.search_params.summary_lines():
            say("   " + line)
        say(f"   👤 계정: {running.korail_id}")
        seat = storage.get_current_seat_index(USER_ID)
        if seat is not None:
            say(f"   💳 {seat + 1}번째 좌석 결제 대기 중 → 결제 후 `korailbot paid`")
    else:
        say("ℹ️ 진행 중인 예약 검색이 없습니다.")

    last = storage.get_last_result(USER_ID)
    if last:
        label = {"success": "✅ 성공", "failed": "❌ 실패", "partial": "⏳ 진행", "cancelled": "🚫 취소"}.get(last.status, last.status)
        say(f"\n마지막 결과 ({last.reported_at.strftime('%Y-%m-%d %H:%M:%S')}): {label}"
            + (" · 결제 완료" if last.paid else ""))
        say("   " + last.message.strip().replace("\n", "\n   "))

    payment = storage.get_payment_status(USER_ID)
    if payment and payment.reminder_active and not payment.completed:
        say("\n⏰ 결제 리마인더 동작 중 — 결제 후 `korailbot paid` 를 실행하세요.")

    multi = storage.get_multi_reservation_status(USER_ID)
    if multi and multi.reservations:
        say("\n🪑 랜덤 배치 좌석 현황")
        for r in sorted(multi.reservations, key=lambda x: x.seat_number):
            state = {"pending": f"⏳ 결제 대기 (남은 시간 {r.get_remaining_minutes_display()})",
                     "paid": "✅ 결제 완료", "expired": "❌ 만료", "cancelled": "🚫 취소"}[r.status.value]
            say(f"   좌석 {r.seat_number}: {state}")

    credentials = storage.get_credentials(USER_ID)
    card = storage.get_saved_card(USER_ID)
    say("")
    say(f"👤 계정: {credentials.korail_id if credentials else '없음 (korailbot login)'}")
    say(f"💳 카드: {card.masked if card and card.is_complete else '없음 (korailbot card set)'}")
    if kakao.enabled:
        say(f"📣 카카오톡: {'연동됨 (' + kakao.link_summary(USER_ID) + ')' if kakao.is_linked(USER_ID) else '미연동 (korailbot kakao link)'}")
    else:
        say("📣 카카오톡: 서버 미설정 (KAKAO_REST_API_KEY)")
    return 0


def cmd_cancel(args) -> int:
    storage = _storage()
    reservation_service, _ = _services(storage)
    cancelled = reservation_service.cancel()
    if not cancelled:
        say("ℹ️ 진행 중인 예약 검색이 없습니다.")
        return 0
    from models import ReservationResult
    storage.save_last_result(USER_ID, ReservationResult(status="cancelled", message="사용자가 예약 검색을 중단했습니다."))
    say(f"✅ 예약 검색을 중단했습니다. (pid={cancelled.process_id}, {cancelled.search_params.src_locate}→{cancelled.search_params.dst_locate})")
    return 0


def cmd_paid(args) -> int:
    storage = _storage()
    reservation_service, _ = _services(storage)
    say("✅ " + reservation_service.confirm_payment().replace("\n", "\n✅ "))
    return 0


# ------------------------------------------------------------------- logs

def _latest_log() -> Optional[str]:
    if not os.path.isdir(settings.LOG_DIR):
        return None
    logs = sorted(
        (os.path.join(settings.LOG_DIR, f) for f in os.listdir(settings.LOG_DIR) if f.startswith("worker-")),
        key=os.path.getmtime,
    )
    return logs[-1] if logs else None


def cmd_logs(args) -> int:
    path = args.file or _latest_log()
    if not path or not os.path.isfile(path):
        say(f"ℹ️ 워커 로그가 없습니다. ({settings.LOG_DIR})")
        return 0
    say(f"📄 {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
        for line in lines[-args.lines:]:
            sys.stdout.write(line)
        if not args.follow:
            return 0
        say("--- 실시간 출력 (Ctrl+C 로 종료) ---")
        try:
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


# ------------------------------------------------------------------- card

def cmd_card(args) -> int:
    storage = _storage()
    action = args.action

    if action == "show":
        card = storage.get_saved_card(USER_ID)
        if not card or not card.is_complete:
            say("ℹ️ 저장된 카드가 없습니다. `korailbot card set` 으로 등록하세요.")
            return 0
        saved_at = datetime.fromtimestamp(card.saved_at).strftime("%Y-%m-%d %H:%M") if card.saved_at else "-"
        say("💳 저장된 자동 결제 카드")
        say(f"   카드번호: {card.masked}{' (법인)' if card.is_corporate else ''}")
        say(f"   유효기간: {card.expire_display}")
        say(f"   저장일:   {saved_at}")
        return 0

    if action == "delete":
        had = storage.get_saved_card(USER_ID) is not None
        storage.delete_saved_card(USER_ID)
        say("✅ 저장된 카드를 삭제했습니다." if had else "ℹ️ 저장된 카드가 없습니다.")
        return 0

    # set
    if not settings.AUTO_PAYMENT_ENABLED:
        fail("이 서버에서는 자동 결제가 꺼져 있습니다 (AUTO_PAYMENT_ENABLED=False).")
    say("💳 자동 결제 카드 등록 (입력 내용은 화면에 표시되지 않습니다)")
    number = InputValidator.normalize_card_number(
        prompt_required("카드번호 (하이픈 생략 가능)", secret=True, validator=InputValidator.validate_card_number)
    )
    expire = InputValidator.normalize_card_expire(
        prompt_required("유효기간 (YYMM 또는 MM/YY)", secret=True, validator=InputValidator.validate_card_expire)
    )
    password = prompt_required("카드 비밀번호 앞 2자리", secret=True, validator=InputValidator.validate_card_password)
    verify = prompt_required(
        "소유자 확인번호 (개인: 생년월일 6자리 / 법인: 사업자등록번호 10자리)", secret=True,
        validator=InputValidator.validate_card_verify_number,
    ).replace("-", "")
    card = CardInfo(number=number, expire=expire, password=password, verify_number=verify, saved_at=time.time())
    storage.save_saved_card(USER_ID, card)
    say(f"✅ 카드가 암호화되어 저장되었습니다. ({card.masked}{', 법인카드' if card.is_corporate else ''})")
    say("   이후 `korailbot start` 는 이 카드로 자동 결제합니다. (--no-pay 로 끄기, `card delete` 로 삭제)")
    return 0


# ------------------------------------------------------------------ kakao

def cmd_kakao(args) -> int:
    storage = _storage()
    _, kakao = _services(storage)
    from services.kakao_service import KakaoError, wait_for_oauth_redirect

    if not kakao.enabled:
        fail("카카오톡 연동이 설정되어 있지 않습니다. .env 에 KAKAO_REST_API_KEY 를 설정하세요. (docs/INSTALL.md 참고)")

    action = args.action
    if action == "status":
        linked = kakao.link_summary(USER_ID)
        say(f"📣 카카오톡: {'연동됨 (연동일 ' + linked + ')' if linked else '미연동'}")
        say(f"   Redirect URI: {settings.KAKAO_REDIRECT_URI}")
        return 0

    if action == "unlink":
        say("✅ 카카오톡 연동을 해제했습니다." if kakao.unlink(USER_ID) else "ℹ️ 연동되어 있지 않습니다.")
        return 0

    if action == "test":
        if not kakao.is_linked(USER_ID):
            fail("카카오톡이 연동되어 있지 않습니다. `korailbot kakao link` 를 먼저 실행하세요.")
        try:
            kakao.send_memo(USER_ID, "🚄 코레일 예매 봇 카카오톡 연동 테스트 메시지입니다. 예약 결과 알림이 이 채팅으로 전달됩니다.")
        except KakaoError as e:
            fail(f"카카오톡 전송 실패: {e}")
        say("✅ 카카오톡 '나와의 채팅'으로 테스트 메시지를 보냈습니다.")
        return 0

    # link
    code = args.code
    if not code:
        url = kakao.get_authorize_url(USER_ID)
        say("💬 아래 주소를 브라우저에서 열어 카카오 로그인 후 '카카오톡 메시지 전송' 권한에 동의하세요.")
        say(f"\n{url}\n")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        redirect_local = settings.KAKAO_REDIRECT_URI.startswith(("http://localhost", "http://127.0.0.1"))
        if redirect_local:
            say(f"로컬 포트 {settings.KAKAO_LOCAL_PORT} 에서 인증 응답을 기다립니다 (최대 3분)...")
            result = wait_for_oauth_redirect(settings.KAKAO_LOCAL_PORT, timeout=180)
            if not result:
                fail("인증 응답을 받지 못했습니다. 다시 시도하거나, 주소창의 code= 값을 `korailbot kakao link --code <코드>` 로 전달하세요.")
            if result.get("error"):
                fail(f"카카오 인증이 거부되었습니다: {result.get('error_description') or result['error']}")
            if kakao.parse_state(result.get("state")) != USER_ID:
                fail("인증 응답의 state 값이 일치하지 않습니다. 다시 시도하세요.")
            code = result.get("code", "")
        else:
            code = prompt_required("리다이렉트된 주소의 code= 값")

    try:
        kakao.exchange_code(USER_ID, code)
        kakao.send_memo(USER_ID, "🚄 코레일 예매 봇과 카카오톡 연동이 완료되었습니다. 예약 결과 알림이 이 채팅으로 전달됩니다.")
    except KakaoError as e:
        fail(f"카카오톡 연동 실패: {e}")
    say("✅ 카카오톡 연동 완료! '나와의 채팅'에서 확인 메시지를 확인하세요.")
    return 0


# --------------------------------------------------------------- stations

def cmd_stations(args) -> int:
    from utils.station_codes import get_valid_stations, get_similar_stations
    if args.query:
        matches = sorted(s for s in get_valid_stations() if args.query in s)
        if not matches:
            matches = get_similar_stations(args.query, max_results=10)
        say(", ".join(matches) if matches else "일치하는 역이 없습니다.")
    else:
        say(", ".join(sorted(get_valid_stations())))
    return 0


# ----------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="korailbot",
        description="코레일 KTX 매진 열차 자동 예매 (카카오톡 알림)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p = sub.add_parser("login", help="코레일 계정을 확인하고 암호화하여 저장")
    p.add_argument("--id", help="코레일 아이디 (생략하면 입력 요청)")
    p.add_argument("--password", help="코레일 비밀번호 (생략하면 숨김 입력)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="저장된 코레일 계정 삭제")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("start", help="예약 검색 시작")
    p.add_argument("--date", required=True, help="출발일 YYYYMMDD (예: 20261225)")
    p.add_argument("--from", dest="src", required=True, help="출발역 ('역' 제외, 예: 서울)")
    p.add_argument("--to", dest="dst", required=True, help="도착역 (예: 부산)")
    p.add_argument("--time", required=True, help="검색 시작 시각 HHMM (예: 0900)")
    p.add_argument("--until", default="2400", help="검색 종료 시각 HHMM, 기본 2400(제한 없음)")
    p.add_argument("--train", choices=sorted(TRAIN_CHOICES), default="ktx", help="열차 종류 (기본 ktx)")
    p.add_argument("--seat", choices=list(SEAT_CHOICES), default="general-first", help="좌석 종류 (기본 general-first)")
    p.add_argument("--passengers", type=int, default=1, help="성인 인원 1~9 (기본 1)")
    p.add_argument("--strategy", choices=["consecutive", "random"], default="consecutive",
                   help="2명 이상일 때 좌석 배치: consecutive(연속, 기본) / random(한 자리씩)")
    pay = p.add_mutually_exclusive_group()
    pay.add_argument("--pay", dest="pay", action="store_true", default=None, help="저장된 카드로 자동 결제 (카드가 없으면 오류)")
    pay.add_argument("--no-pay", dest="pay", action="store_false", help="자동 결제 사용 안 함")
    p.add_argument("--id", help="저장된 계정 대신 사용할 코레일 아이디")
    p.add_argument("--password", help="--id 와 함께 쓸 비밀번호 (생략하면 숨김 입력)")
    p.add_argument("-y", "--yes", action="store_true", help="확인 질문 없이 시작")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status", help="진행 상황 / 마지막 결과 / 설정 확인")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("cancel", help="진행 중인 예약 검색 중단")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("paid", help="직접 결제를 완료했음을 알림 (리마인더 중단, 다음 좌석 진행)")
    p.set_defaults(func=cmd_paid)

    p = sub.add_parser("logs", help="워커 로그 보기")
    p.add_argument("-n", "--lines", type=int, default=50, help="마지막 N줄 (기본 50)")
    p.add_argument("-f", "--follow", action="store_true", help="실시간으로 계속 출력")
    p.add_argument("--file", help="특정 로그 파일 경로")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("card", help="자동 결제 카드 관리")
    p.add_argument("action", choices=["show", "set", "delete"], help="show: 확인, set: 등록/교체, delete: 삭제")
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("kakao", help="카카오톡 알림 연동 관리")
    p.add_argument("action", choices=["link", "status", "test", "unlink"])
    p.add_argument("--code", help="link: 브라우저 주소창의 code= 값을 직접 전달")
    p.add_argument("--no-browser", action="store_true", help="link: 브라우저를 자동으로 열지 않음")
    p.set_defaults(func=cmd_kakao)

    p = sub.add_parser("stations", help="역 이름 목록/검색")
    p.add_argument("query", nargs="?", help="검색어 (예: 광주)")
    p.set_defaults(func=cmd_stations)

    return parser


def main(argv: Optional[list] = None) -> int:
    try:
        settings.validate()
    except ValueError as e:
        say(f"❌ 설정 오류: {e}")
        return 2
    # The CLI prints its own messages; library INFO logs only clutter the terminal.
    # The worker keeps LOG_LEVEL (its output goes to the log file).
    if settings.LOG_LEVEL.upper() != "DEBUG":
        LoggerFactory.set_log_level("WARNING")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as e:
        say(f"❌ {e}")
        return 1
    except KeyboardInterrupt:
        say("\n중단했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

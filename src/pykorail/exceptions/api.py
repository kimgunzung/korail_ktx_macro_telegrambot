"""코레일 응답 코드(``h_msg_cd``)에 대응하는 구체 예외들.

새 코드를 다룰 때는 :class:`~pykorail.exceptions.base.KorailError` 를 상속하고
``codes``·``default_msg`` 만 채우면 됩니다 — :func:`error_for_code` 가 서브클래스
트리를 훑어 자동으로 찾아가므로 등록 테이블을 따로 손댈 필요가 없습니다.
"""

from __future__ import annotations

from pykorail.exceptions.base import KorailError


class NeedToLoginError(KorailError):
    """세션이 없거나 만료됐습니다. :meth:`~pykorail.client.Korail.login` 을 다시 호출하세요."""

    codes = frozenset({"P058"})
    default_msg = "Need to Login"


class NoResultsError(KorailError):
    """조건에 맞는 열차·예약·승차권이 없습니다."""

    codes = frozenset({"P100", "WRG000000", "WRD000061", "WRT300005"})
    default_msg = "No Results"


class SoldOutError(KorailError):
    """좌석이 매진됐습니다."""

    codes = frozenset({"IRT010110", "ERR211161"})
    default_msg = "Sold out"


class LoginFailedError(KorailError):
    """로그인에 실패했습니다.

    입력 검증(빈 자격증명), 준비 단계(암호화 키 발급), 서버의
    자격증명 거부를 **모두** 이 타입 하나로 올립니다 —
    :meth:`~pykorail.client.Korail.login` 이 성공 여부를 반환하지 않으므로,
    "로그인이 안 됐다" 를 잡는 지점이 여기 하나입니다.

    서버가 거부한 경우에는 응답의 ``h_msg_txt``·``h_msg_cd`` 가 :attr:`msg`·
    :attr:`code` 에 실립니다. 이 타입 자체는 ``codes`` 가 없어 자동 승격되지 않고
    클라이언트가 직접 던집니다. 사용자가 할 일이 다른 실패는 하위 타입으로 갈라 두고,
    그쪽은 코드를 가집니다 (:class:`PasswordChangeRequiredError`).

    요청 자체가 거절된 경우(HTTP 4xx·5xx, 또는 상태와 무관한 이용제한 봉투)는 자격증명을 판정한
    결과가 아니므로 이 타입이 아니라
    :class:`~pykorail.exceptions.network.HttpStatusError` 로 올라옵니다.
    """

    default_msg = "Login failed"


class PasswordChangeRequiredError(LoginFailedError):
    """비밀번호 변경 안내에 걸려 로그인이 끝나지 않았습니다.

    같은 비밀번호를 6개월 넘게 쓰면 서버가 로그인 응답에 이 코드를 실어 보냅니다.
    응답은 ``strResult=SUCC`` 이고 회원번호도 오지만 **세션은 인증되지 않습니다** —
    그대로 로그인된 것으로 치면 이후의 모든 인증 호출이 ``P058`` 로 거절되고, 원인은
    어디에도 드러나지 않습니다 (2026-10-13 실서버 확인).

    자격증명이 틀린 것이 아니므로 같은 비밀번호로 다시 시도해도 결과는 같습니다.
    코레일톡 앱이나 홈페이지에서 안내를 처리한 뒤 다시 로그인하세요. 재시도 루프라면
    이 타입만 따로 잡아 멈추면 됩니다.

    :class:`LoginFailedError` 의 하위 타입이라 기존의 ``except LoginFailedError`` 도
    이 실패를 계속 잡습니다. 서버가 준 안내문이 :attr:`msg` 에 실립니다.

    코드가 등록돼 있으므로, 로그인이 아닌 호출이 같은 코드로 거절돼도 이 타입으로
    올라옵니다 — "이 계정은 안내를 처리하기 전에는 쓸 수 없다" 는 뜻은 같습니다. 그런
    응답을 본 적은 없고, 관측된 것은 로그인의 성공 봉투에 실려 온 경우뿐입니다.
    """

    codes = frozenset({"WRC000420"})
    default_msg = "Password change required"


def _coded_subclasses(root: type[KorailError]) -> list[type[KorailError]]:
    """``codes`` 가 비어 있지 않은 :class:`KorailError` 하위 타입을 깊이 우선으로 모읍니다."""
    found: list[type[KorailError]] = []
    for subclass in root.__subclasses__():
        if subclass.codes:
            found.append(subclass)
        found.extend(_coded_subclasses(subclass))
    return found


def error_for_code(code: str | None, msg: str | None = None) -> KorailError:
    """응답 코드를 가장 구체적인 예외 인스턴스로 바꿉니다.

    매칭되는 코드가 없으면 서버 메시지를 담은 :class:`KorailError` 를 돌려줍니다.
    반환만 하고 raise 하지 않으므로 호출부에서 ``raise error_for_code(...)`` 하세요.

    서버 메시지는 **코드가 매칭돼도** 싣습니다. 같은 타입으로 묶인 코드라도 서버가 하는
    말은 다를 수 있고("로그아웃되었습니다" 와 "회원로그인 후 사용하시기 바랍니다" 는 둘 다
    ``P058`` 입니다), 타입의 ``default_msg`` 는 그 차이를 지웁니다. 서버가 메시지를 주지
    않았거나 빈 문자열이면 ``default_msg`` 가 남습니다.
    """
    if code is not None:
        for error_type in _coded_subclasses(KorailError):
            if code in error_type.codes:
                return error_type(msg or None, code)
    return KorailError(msg, code)

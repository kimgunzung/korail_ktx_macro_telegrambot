"""DynaPath 요청 서명 (``x-dynapath-m-token``) — 코레일톡+ 7.0.10 (SDK 2.0.1).

7.0.10 의 토큰은 Java 가 아니라 네이티브 ``libdp_ffi_android.so`` 에서만 만들어지고,
그 알고리즘과 키는 복원되지 않았습니다. Java 에 평문으로 남아 있는
``2!v=2&es=…&sl=`` 봉투는 네이티브 실패 시의 폴백일 뿐입니다.

실서버 관측(2026-10-26, ``seatMovie.ScheduleView``, 읽기 전용, 본인 계정)에서 서버는
토큰의 **버전 프리픽스 ``201`` 만** 검사했습니다 — ``201`` + 임의 문자도, 문자열
``201`` 하나만 보내도 200 이었고, 7.0.8 의 ``bEeEP…``(v1) 위조는 403 ``-1406``
이었습니다. 즉 본문은 검증되지 않습니다(근거: ``docs/protocol-evidence-7.0.10.json``).

그래서 이 모듈은 네이티브 서명을 재현하지 않습니다. **``201`` 프리픽스 + 임의
영숫자 본문**만 냅니다. 본문은 요청마다 ``secrets`` 로 새로 뽑으므로 실제 토큰처럼
매번 달라지고, 7.0.10 실측 길이(로그인 324자·그 외 222자)를 지킵니다. 다만 이 방식은
per-request 토큰의 **Flow 2**(하드웨어 키 서명 챌린지-응답:
``beginRequest``→``clientDataHash``→StrongBox/TEE 서명→``finishRequest``)를 재현하지
못합니다. 코레일이 지금은 소프트웨어 declaration 토큰도 통과시키지만, 서버가 하드웨어
서명 토큰만 허용하는 때가 오면 동작하지 않습니다(그때는 범위 밖인 하드웨어 서명이 필요).

역공학한 네이티브 입력 계약(``declarationToken`` 16-인자)과 실측 토큰 형식은 근거
문서에 남겨 두었습니다 — 토큰 본문을 만드는 데는 쓰지 않습니다.
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from typing import Final

from pykorail.constants import TOKEN_PREFIX

#: 토큰 본문에 쓰는 영숫자 집합([0-9A-Za-z], 62기호 — '62'는 집합 크기이지 길이가
#: 아닙니다). 실측: 로그인·조회 토큰 모두 이 집합만 씁니다.
_ALPHABET: Final = string.digits + string.ascii_uppercase + string.ascii_lowercase

#: 요청 바인딩이 없는 토큰의 총 길이(실측: ``seatMovie.ScheduleView`` 222자).
_PLAIN_TOKEN_LENGTH: Final = 222
#: 요청 바인딩이 실리는 로그인 토큰의 총 길이(실측: ``login.Login`` 324자).
_BOUND_TOKEN_LENGTH: Final = 324


@dataclass(frozen=True)
class DynaPathMasterEngine:
    """DynaPath 토큰 생성기.

    네이티브 서명이 아니라 ``201`` 프리픽스 + 임의 영숫자 본문을 냅니다. 서버가
    프리픽스만 보는 동안만 유효합니다. 하드웨어 서명 경로(Flow 2)는 재현하지 못하므로,
    서버가 그것만 허용하는 때가 오면 무효입니다. 상태가 없어 어디서든 같은 인스턴스
    규칙으로 동작합니다.
    """

    def generate_token(self, *, request_bound: bool = False) -> str:
        """``x-dynapath-m-token`` 헤더 값을 만듭니다.

        Args:
            request_bound: 네이티브 ``declarationToken`` 에 요청 바인딩이 실리는지.
                로그인 op 만 True 이며, 그러면 토큰이 더 깁니다(324 vs 222).
        """
        length = _BOUND_TOKEN_LENGTH if request_bound else _PLAIN_TOKEN_LENGTH
        return TOKEN_PREFIX + _random_body(length - len(TOKEN_PREFIX))

    def generate_token_with_timestamp(self, *, request_bound: bool = False) -> tuple[str, int]:
        """현재 시각으로 토큰을 만들고 ``Sid`` 에 쓸 시각을 함께 돌려줍니다."""
        return self.generate_token(request_bound=request_bound), int(time.time() * 1000)


def _random_body(width: int) -> str:
    """``width`` 자리 영숫자 난수를 뽑습니다."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(width))

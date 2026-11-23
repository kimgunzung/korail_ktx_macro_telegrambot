"""요청 서명 — 어떤 경로에 어떤 인증 재료를 붙일지 결정합니다."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pykorail.auth.dynapath import DynaPathMasterEngine
from pykorail.constants import DEVICE, DYNAPATH_LOGIN_OP, DYNAPATH_PATHS, SID_KEY
from pykorail.crypto import encrypt_sid
from pykorail.device.android_id import generate_android_id, validate_android_id

if TYPE_CHECKING:
    from pykorail.device import DeviceProfileLike

#: op id 를 확보하지 못한 엔드포인트의 자리표시자.
_DEFAULT_OP_ID: Final = 0
#: 요청 바인딩(그래서 더 긴 토큰)이 실리는 op. APK ``a/J0.java`` 에서 확인한 로그인 op 입니다.
_OP_IDS: Final[dict[str, int]] = {
    "/classes/com.korail.mobile.login.Login": DYNAPATH_LOGIN_OP,
}


def _op_id_for(url: str) -> int:
    """URL 조각으로 op id 를 고릅니다. 모르면 자리표시자를 돌려줍니다."""
    return next((op_id for fragment, op_id in _OP_IDS.items() if fragment in url), _DEFAULT_OP_ID)


class RequestSigner:
    """DynaPath 서명이 필요한 요청에 헤더와 ``Sid`` 를 만들어 줍니다.

    DynaPath 토큰에는 기기 신원이 실리지 않습니다(서버가 ``201`` 프리픽스만 봅니다).
    ``android_id`` 는 ``Korail.android_id`` 의 저장·복원 계약을 위해 들고 있을 뿐,
    지금은 어떤 요청 값에도 들어가지 않습니다.
    """

    def __init__(
        self,
        profile: DeviceProfileLike | None = None,
        device: str = DEVICE,
        device_id: str | None = None,
        sid_key: bytes = SID_KEY,
    ) -> None:
        # 명시적 device_id는 검증하지 않는 저수준 오버라이드입니다 — 기존 서명
        # API를 유지하려는 것이며, 형식 검증은 Korail(android_id=...)이 먼저
        # 합니다. 외부 프로파일의 선택 필드는 getattr로 읽어 3필드
        # DeviceProfileLike와도 호환됩니다.
        profile_id = getattr(profile, "android_id", None)
        if device_id is not None:
            self._device_id = device_id
        elif profile_id is not None:
            self._device_id = validate_android_id(profile_id)
        else:
            self._device_id = generate_android_id()
        self._engine = DynaPathMasterEngine()
        self._device = device
        self._sid_key = sid_key

    @property
    def android_id(self) -> str:
        """이 서명기가 들고 다니는 기기 ID 입니다. 토큰에는 실리지 않습니다."""
        return self._device_id

    def sign(self, url: str, *, include_sid: bool = True) -> tuple[dict[str, str], str | None]:
        """``url`` 에 필요한 ``(헤더, Sid)`` 를 만듭니다.

        서명 대상이 아닌 경로면 ``({}, None)`` 을 돌려줍니다. Sid를 사용하는
        요청에서는 토큰과 같은 시각으로 생성합니다. 로그인처럼 Sid를 보내지
        않는 호출은 ``include_sid=False`` 로 불필요한 암호화도 생략합니다.
        """
        if not any(path in url for path in DYNAPATH_PATHS):
            return {}, None

        request_bound = _op_id_for(url) == DYNAPATH_LOGIN_OP
        token, ts = self._engine.generate_token_with_timestamp(request_bound=request_bound)
        sid = encrypt_sid(self._device, ts, self._sid_key) if include_sid else None
        return {"x-dynapath-m-token": token}, sid

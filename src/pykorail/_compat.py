"""파이썬 최소 버전 강제.

``requires-python`` 은 패키지 매니저를 통해 설치할 때만 걸립니다. 소스를 그대로
복사해 오거나 ``PYTHONPATH`` 로 끌어다 쓰는 경우를 대비해 임포트 시점에도 막습니다.
``slots=True`` 데이터클래스 같은 3.10 기능을 실제로 쓰기 때문에, 그 미만에서는
``TypeError: dataclass() got an unexpected keyword argument 'slots'`` 처럼 원인을
알아보기 힘든 오류로 죽습니다. 그 대신 명확한 메시지를 냅니다.

3.10 은 코드가 돌 수는 있어도 지원이 끝나(EOL 2026-10) 더는 테스트하지 않는 버전이라
함께 막습니다 — 검증되지 않은 조합에서 조용히 돌다 깨지는 것보다 여기서 멈추는 편이
낫습니다.
"""

from __future__ import annotations

import sys

MIN_PYTHON = (3, 11)

if sys.version_info < MIN_PYTHON:  # pragma: no cover - 낮은 버전에서만 실행
    raise RuntimeError(
        f"pykorail 은 파이썬 {'.'.join(map(str, MIN_PYTHON))} 이상이 필요합니다 "
        f"(현재 {'.'.join(map(str, sys.version_info[:3]))})."
    )

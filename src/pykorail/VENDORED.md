# pykorail (저장소 내 보관 소스)

코레일 스마트 예매 비공식 클라이언트 **pykorail 0.5.1** 의 소스입니다. 설치하지 않고 `src/` 에서 바로 임포트됩니다.

| 항목 | 값 |
|------|----|
| 원본 | PyPI 배포본 `pykorail-0.5.1-py3-none-any.whl` (2026-10-26 수신) |
| 배포본 sha256 | `198c89042a56cbcbdc86e970a58b613b928dc1922b80f4a2c97a168f0bd7d49c` |
| 추출 검증 | 78개 `.py` 와 `py.typed` 를 배포본의 `RECORD` 해시와 대조해 일치 확인. 추출 직후 상태가 한 커밋으로 남아 있어 이후 수정은 `git log -- src/pykorail` 로 구분됩니다 |
| 라이선스 | pykorail License (MIT + 상업적 이득 금지). 전문은 이 폴더의 `LICENSE` |
| 의존성 | `certifi`, `curl_cffi`, `pycryptodome` (`requirements.txt` 에 고정) |

## 왜 여기 있나

pykorail 은 2026년 11월 초 PyPI 에서 삭제되었고 원 저장소(`devgyurak/pykorail`)도 열리지 않습니다.
`pip install pykorail` 이 불가능해져, 받아 둔 배포본을 풀어 소스로 보관합니다.

## 수정할 때

- 코레일이 앱 규격을 바꾸면(`-8204 앱 업데이트 필요` 등) 여기 코드를 직접 고쳐야 합니다. 갱신본을 받을 곳이 없습니다.
- 요청 서명은 `auth/dynapath.py`, `auth/signer.py`, 앱 버전·엔드포인트는 `constants.py`, 로그인은 `_sync/client.py`, 조회·예약은 `_sync/resources/` 에 있습니다.
- `_sync/` 는 원래 `_async/` 에서 자동 생성된 코드이고 생성 스크립트는 배포본에 없습니다. 이 봇은 `_sync/` 만 쓰므로 `_sync/` 를 고치면 됩니다. 두 쪽을 맞추려면 같은 수정을 `_async/` 에도 적용하세요.
- `_version.py` 의 버전은 원본 버전(0.5.1)을 그대로 둡니다. 수정 내역은 git 이력으로 추적합니다.
- 라이선스 조건: `LICENSE` 를 유지하고, 승차권 재판매·유료 예매 대행 등 상업적 이득에 쓰지 않습니다.

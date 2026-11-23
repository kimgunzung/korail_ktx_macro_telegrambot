# vendor/

pip 로 받을 수 없게 된 의존성의 배포본을 보관합니다.

## pykorail 0.5.1

| 항목 | 값 |
|------|----|
| 파일 | `pykorail-0.5.1-py3-none-any.whl` |
| sha256 | `198c89042a56cbcbdc86e970a58b613b928dc1922b80f4a2c97a168f0bd7d49c` |
| 받은 곳 | PyPI (2026-10-26). 위 해시는 그때 PyPI 가 내려준 값으로, `Pipfile.lock` 에 기록됐던 값과 같습니다 |
| 라이선스 | pykorail License (MIT + 상업적 이득 금지). 전문은 `pykorail-LICENSE` 와 wheel 안의 `LICENSE` |

**보관 이유**: 2026년 11월 초 pykorail 이 PyPI 에서 삭제되었고 원 저장소(`devgyurak/pykorail`)도 열리지 않습니다.
`pip install pykorail` 이 더는 동작하지 않아 `requirements.txt` 가 `--find-links vendor` 로 이 파일을 설치합니다.

**주의**

- 원 배포처가 사라져 이후 수정본을 받을 수 없습니다. 코레일이 앱 규격을 다시 바꾸면 이 버전은 `-8204` 등으로 거부될 수 있고, 그때는 대체 라이브러리나 직접 수정이 필요합니다.
- 라이선스상 승차권 재판매, 유료 예매 대행 등 상업적 이득을 위한 사용은 금지됩니다. 본인 여행용 예매와 비상업적 개인 프로젝트는 허용됩니다.
- 다른 버전으로 바꿀 때는 출처와 해시를 확인할 수 있는 파일만 넣고, 이 표와 `requirements.txt` 의 버전을 함께 고치세요.
- `Pipfile`/`Pipfile.lock` 은 아직 PyPI 의 `pykorail==0.5.1` 을 가리켜 `pipenv install` 로는 설치되지 않습니다. `pip install -r requirements.txt` 를 쓰세요. `make requirements` 로 `requirements.txt` 를 다시 만들면 `--find-links vendor` 줄이 사라지니 다시 넣어야 합니다.

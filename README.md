# 코레일 KTX 자동 예매 (korailbot)

매진된 KTX 열차를 자동으로 모니터링하여 좌석이 나오면 예약하고, 저장된 카드로 바로 결제까지 해 주는 도구입니다.
명령줄(`korailbot`)과 **텔레그램 봇**으로 조작하고, 예약 결과와 결제 마감 알림은 텔레그램으로 받습니다(카카오톡 알림은 선택).

```bash
korailbot login                                  # 코레일 계정 저장 (암호화)
korailbot card set                               # (선택) 자동 결제 카드 등록
korailbot telegram                               # 텔레그램 봇 실행 (상시) → korailbot pair 로 내 채팅 등록
korailbot start --date 20261225 --from 서울 --to 부산 --time 0900 --until 1200
korailbot status                                 # 진행 상황 / 마지막 결과
korailbot cancel                                 # 검색 중단
```

> 설치부터 첫 예약까지의 단계별 안내(Ubuntu / Windows)는 **[docs/INSTALL.md](docs/INSTALL.md)**, 새 서버에 처음부터 다시 구축하는 체크리스트는 **[docs/RUNBOOK.md](docs/RUNBOOK.md)** 를 보세요.

## 동작 방식

1. `korailbot start` 가 조건을 검증하고 **백그라운드 워커**를 띄운 뒤 바로 종료합니다. 워커는 터미널을 닫아도 계속 돌아갑니다.
2. 워커는 코레일에 로그인한 뒤 `SEARCH_INTERVAL`(기본 3초) 간격으로 좌석을 조회하고, 자리가 나오면 즉시 예약합니다.
3. 카드가 저장되어 있으면 예약 직후 **자동 결제**해 승차권을 발권합니다. 카드가 없거나 결제가 실패하면 결제 링크와 함께 안내하고, `PAYMENT_TIMEOUT_MINUTES`(기본 10분) 동안 텔레그램으로 리마인더를 보냅니다. 직접 결제한 뒤 `korailbot paid`(텔레그램에서는 `결제완료`)를 보내면 알림이 멈춥니다.
4. 결과는 텔레그램 알림, `korailbot status`, `korailbot logs` 로 확인합니다.

CLI와 워커는 Redis로 상태를 공유합니다. 계정과 카드 정보는 Redis에 **AES-256-GCM으로 암호화**되어 저장됩니다.

## 참고

- 코레일 API 클라이언트로 [devgyurak/pykorail](https://github.com/devgyurak/pykorail) **0.5.1** 을 사용합니다 (코레일+ 7.0.10 / DynaPath v2 규격, Python 3.11+). 코레일은 2026-10-21 앱 보안 모듈을 v2로 바꿔 그 이전 버전(0.2.x~0.4.x)은 로그인·조회가 `-8204 앱 업데이트 필요` 로 거부됩니다. 이 오류가 보이면 `git pull` 후 `pip install -r requirements.txt` 로 갱신하세요.
- 2026-09 KTX·SRT 통합(코레일+) 이후에도 pykorail 은 코레일 열차만 조회합니다(앱의 "SRT 함께 보기" 옵션은 꺼진 채 요청). SRT 열차 조회·예매는 아직 지원하지 않습니다.
- 과거에 사용했던 [carpedm20/korail2](https://github.com/carpedm20/korail2) 계열은 2026-09 코레일+ 앱 업데이트 이후 `-2000 이용 제한` 응답을 받아 동작하지 않습니다.
- 코레일은 AWS 등 데이터센터·VPN IP를 차단(`-8202`)하고, 매크로로 판정한 공인 IP도 차단합니다. **가정용 회선에서 실행하세요.** 한 번 차단된 IP는 코드를 고쳐도 풀리지 않으며 공인 IP가 바뀌어야 합니다.
- DynaPath 기기 식별자는 `KORAIL_DEVICE_FILE`(기본 `~/.korail_bot_device.json`)에 저장되어 모든 프로세스가 같은 기기로 보이게 합니다. 고정하려면 `KORAIL_DEVICE_PROFILE_ID`/`KORAIL_ANDROID_ID`를 환경변수로 지정하세요.

## 주의사항

1. 귀경길 기차 예매를 하지 못한 안타까운 영혼들을 위해 만든 프로그램이므로, 개인용 목적이 아닌 상업적 목적등으로 이용하는 것을 엄중히 금합니다.
2. 기본으로 설정된 3초에 1번(`SEARCH_INTERVAL=3`) 조회 요청보다 빠르게 설정하지 마십시오. 짧은 조회 간격은 매크로 판정과 IP 차단의 주된 원인입니다.
3. 사이트 구성이나 변수명 변경에 따라 언제든 동작하지 않을 수 있습니다.

## 명령어

| 명령어 | 설명 |
|--------|------|
| `login [--id ID] [--password PW]` | 코레일 로그인을 확인하고 계정을 암호화 저장. 생략하면 입력 요청 |
| `logout` | 저장된 계정 삭제 |
| `start --date YYYYMMDD --from 역 --to 역 --time HHMM [옵션]` | 예약 검색 시작 (아래 옵션 참고) |
| `search --date YYYYMMDD --from 역 --to 역 [--time HHMM] [--until HHMM] [--train ktx\|all]` | **조회 결과 표시**: 열차 시간표와 일반실/특실/예약대기 좌석 현황 (로그인·예약 없음) |
| `reservations` | **예약 결과 표시**: 코레일에 잡혀 있는 결제 전 예약 목록 (예약번호, 좌석, 금액, 결제기한) |
| `status` | 진행 중인 검색, 마지막 결과, 결제 대기, 계정/카드/카카오 설정 상태 |
| `cancel` | 진행 중인 검색 중단 |
| `paid` | 직접 결제를 완료했음을 알림: 리마인더 중단, 랜덤 배치에서는 다음 좌석 검색 진행 |
| `logs [-n N] [-f]` | 최신 워커 로그 출력 (`-f` 실시간) |
| `card show / set / delete` | 자동 결제 카드 확인 / 등록·교체 / 삭제 |
| `telegram` | 텔레그램 봇 실행 (롱 폴링, 공개 서버 불필요) |
| `pair / unpair` | 텔레그램 채팅 등록 코드 발급 / 전체 해제 |
| `kakao link / status / test / unlink` | (선택) 카카오톡 알림 연동 관리 |
| `stations [검색어]` | 역 이름 목록/검색 |

### `start` 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--date` | 필수 | 출발일 `YYYYMMDD` (오늘 ~ 1년 이내) |
| `--from`, `--to` | 필수 | 출발역, 도착역 (`역` 제외: `서울`, `광명`, `동대구`) |
| `--time` | 필수 | 검색 시작 시각 `HHMM` (이 시각 이후 출발 열차) |
| `--until` | `2400` | 검색 종료 시각 `HHMM` (포함). `2400`이면 제한 없음 |
| `--train` | `ktx` | `ktx` (KTX/KTX-산천) 또는 `all` (모든 열차) |
| `--seat` | `general-first` | `general-first`(일반실 우선) / `general-only` / `special-first`(특실 우선) / `special-only` |
| `--passengers` | `1` | 성인 인원 1~9 |
| `--strategy` | `consecutive` | 2명 이상일 때 `consecutive`(연속 좌석, 없으면 대기) 또는 `random`(한 자리씩 개별 예약) |
| `--pay` / `--no-pay` | 저장된 카드가 있으면 자동 결제 | `--pay`는 카드가 없으면 오류, `--no-pay`는 이번 예약만 직접 결제 |
| `--id`, `--password` | 저장된 계정 | 이번 예약에만 다른 계정 사용 |
| `-y`, `--yes` | | 확인 질문 없이 시작 |

예시:

```bash
# 12월 25일 서울→부산, 09:00~12:00 출발 KTX, 1명, 저장된 카드로 자동 결제
korailbot start --date 20261225 --from 서울 --to 부산 --time 0900 --until 1200

# 3명 랜덤 배치(한 자리씩), 특실 우선, 결제는 직접
korailbot start --date 20261225 --from 광명 --to 동대구 --time 1700 --passengers 3 --strategy random --seat special-first --no-pay
```

## 카드 자동 결제

`korailbot card set` 으로 카드번호(Luhn 검증), 유효기간(`YYMM` 또는 `MM/YY`), 카드 비밀번호 앞 2자리, 소유자 확인번호(개인: 생년월일 6자리, 법인: 사업자등록번호 10자리)를 입력하면 카드가 저장되고, 이후 `start` 는 예약 직후 코레일 결제 API(pykorail `reservations.pay`)로 **일시불 결제**해 승차권을 발권합니다.

- 연속 좌석: 예약 1건을 결제합니다. 랜덤 배치: 좌석마다 예약 직후 결제하고 바로 다음 좌석을 검색합니다.
- 결제 성공 시 리마인더는 보내지 않습니다. 결제 실패(한도 초과, 카드 정보 오류, 코레일 거부 등) 시 예약은 그대로 두고 사유와 함께 직접 결제 안내(리마인더 포함)로 넘어갑니다.
- 서버 전체에서 끄려면 `AUTO_PAYMENT_ENABLED=False`.

**카드 정보 취급**
- 카드와 코레일 계정은 Redis에 **AES-256-GCM 암호화**로 저장됩니다. 키는 `CARD_ENCRYPTION_KEY` 환경변수를 쓰고, 없으면 `~/.korail_bot/secret.key`에 무작위 키를 한 번 생성해 보관합니다(권한 600). 이 파일을 지우거나 키를 바꾸면 저장 정보가 복호화되지 않아 다시 등록해야 합니다.
- 워커에는 환경변수로 전달되며 argv(`ps`)에 노출되지 않습니다. 입력은 화면에 표시되지 않고(getpass), 로그와 알림에는 뒤 4자리(`****1234`)만 남습니다.

## 텔레그램 봇 (휴대폰에서 예약 조작 + 알림)

텔레그램 봇으로 휴대폰에서 예약을 시작·조회·취소하고 결과 알림을 받습니다. **롱 폴링** 방식이라 공개 서버, 도메인, 인증서가 전혀 필요 없고 집 PC에서 그대로 동작합니다.

```
예약                              # 조건을 하나씩 물어보는 안내 (출발일 → 출발역 → … → 확인), 버튼 제공
예약 20261225 서울 부산 0900 1200   # 한 줄로 시작
조회 20261225 서울 부산 0900 1200   # 시간표와 좌석 현황만 확인 (예약 안 함)
예약목록                           # 코레일에 잡혀 있는 결제 전 예약 확인
상태 / 취소 / 결제완료 / 카드 / 처음부터 / 도움말     (또는 /status /cancel /paid /card /reset /help /search /reservations)
```

1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `.env` 의 `TELEGRAM_BOT_TOKEN` 에 넣습니다.
2. `korailbot telegram` 을 실행해 둡니다(systemd 로 상시 실행 권장, `docs/INSTALL.md`).
3. `korailbot pair` 로 코드를 만들고, 텔레그램에서 봇에게 `등록 123456` 을 보냅니다. 그 채팅만 봇을 조작하고 알림을 받습니다(`TELEGRAM_ALLOWED_CHAT_IDS` 로 고정도 가능). 등록되지 않은 채팅의 메시지는 거부됩니다.

- **보안**: 코레일 비밀번호와 카드번호는 채팅으로 입력받지 않습니다. 계정·카드 등록은 서버에서 `korailbot login`, `korailbot card set` 으로만 합니다. 봇 토큰은 `.env` 에만 두세요.
- **알림**: 예약 성공/실패, 결제 리마인더, 오류가 등록된 채팅으로 옵니다. 카카오톡으로도 받고 싶으면 아래 카카오톡 알림을 추가로 연동합니다.

## (선택) 카카오톡 알림

텔레그램 대신, 또는 텔레그램과 함께 카카오 "나에게 보내기" API(`talk_message` 권한)로 예약 성공/실패, 결제 리마인더, 오류를 카카오톡 "나와의 채팅"으로 보낼 수 있습니다. 카카오톡 채널이나 알림톡 비용은 없지만 카카오 개발자 앱 설정이 필요합니다. 텔레그램을 쓴다면 건너뛰어도 됩니다.

1. [Kakao Developers](https://developers.kakao.com)에서 애플리케이션을 만들고 **REST API 키**를 확인합니다.
2. **카카오 로그인**을 활성화하고 Redirect URI에 `http://localhost:8080/kakao/oauth` 를 등록합니다 (포트는 `KAKAO_LOCAL_PORT`로 변경 가능).
3. **동의항목**에서 `카카오톡 메시지 전송 (talk_message)` 을 활성화합니다.
4. `.env`에 `KAKAO_REST_API_KEY` 를 넣고 `korailbot kakao link` 를 실행합니다. 브라우저가 열리고 로그인·동의를 마치면 자동으로 연동됩니다. 브라우저 없는 서버라면 출력된 주소를 다른 기기에서 열고, 리다이렉트된 주소의 `code=` 값을 `korailbot kakao link --code <값>` 으로 전달하세요.

- 토큰은 Redis에 저장되며 access token은 자동 갱신됩니다. refresh token이 만료되면(약 2개월) 연동이 해제되고 로그에 안내가 남습니다.
- 카카오 텍스트 템플릿은 200자 제한이 있어 긴 알림은 `(1/3)` 처럼 나뉘어 전송됩니다.
- 리마인더 주기는 `PAYMENT_REMINDER_INTERVAL`(기본 120초)입니다.

## 환경변수

`.env` 파일(레포 루트)은 실행 시 자동으로 읽히며, 셸에서 export 한 값이 우선합니다. 전체 목록은 [.env.example](.env.example)에 있습니다.

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `localhost` / `6379` / `0` | 상태 저장용 Redis |
| `SEARCH_INTERVAL` | `3` | 조회 간격(초). 줄이지 마세요 |
| `PAYMENT_TIMEOUT_MINUTES` | `10` | 미결제 예약 유지 시간 (리마인더 종료 기준) |
| `PAYMENT_REMINDER_INTERVAL` | `120` | 카카오톡 결제 리마인더 간격(초) |
| `AUTO_PAYMENT_ENABLED` | `True` | 카드 자동 결제 기능 |
| `CARD_ENCRYPTION_KEY` | (자동 생성) | 저장 카드/계정 암호화 키. 운영에서는 `openssl rand -hex 32` 값 지정 권장 |
| `KAKAO_REST_API_KEY` | | 카카오 앱 REST API 키 (없으면 알림 비활성화) |
| `KAKAO_CLIENT_SECRET` | | 카카오 앱에서 Client Secret을 켠 경우 |
| `KAKAO_LOCAL_PORT` | `8080` | `kakao link` 가 인증 응답을 받을 로컬 포트 |
| `KAKAO_REDIRECT_URI` | `http://localhost:8080/kakao/oauth` | Kakao Developers에 등록한 Redirect URI |
| `TELEGRAM_BOT_TOKEN` | | @BotFather 토큰. 설정하면 `telegram` 명령과 텔레그램 알림이 활성화 |
| `TELEGRAM_ALLOWED_CHAT_IDS` | | `pair` 대신 허용할 텔레그램 채팅 id 목록 |
| `TELEGRAM_POLL_TIMEOUT` | `30` | 롱 폴링 대기 시간(초) |
| `KORAIL_BOT_DATA_DIR` | `~/.korail_bot` | 워커 로그(`logs/`)와 `secret.key` 위치 |
| `LOG_LEVEL` | `INFO` | `DEBUG`로 올리면 조회 로그가 상세해집니다 |

## 개발

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest pytest-mock freezegun
redis-server --daemonize yes        # 또는 docker compose up -d redis
python -m pytest tests -q           # Docker가 있으면 테스트용 Redis 컨테이너를 자동으로 띄움
```

```
src/
├── cli.py                  # korailbot 명령줄 진입점
├── chatbot/logic.py        # 채팅 명령·예약 안내 (전송 수단 무관)
├── telegram_bot/           # 텔레그램 Bot API 클라이언트(api.py), 롱 폴링 루프(runner.py)
├── config/settings.py      # 환경변수 / .env
├── models/                 # TrainSearchParams, CardInfo, KakaoTokens ...
├── services/
│   ├── korail_service.py       # pykorail 래퍼: 로그인, 조회, 예약, 결제
│   ├── reservation_service.py  # 워커 프로세스 시작/중단/상태
│   ├── kakao_service.py        # (선택) 카카오 OAuth, 나에게 보내기
│   └── notifier.py             # 로그 + 텔레그램 + 카카오톡 알림
├── storage/redis.py        # Redis 상태 저장 (카드/계정은 암호화)
├── worker/reservation_worker.py  # 백그라운드 검색·예약·결제·리마인더
└── utils/                  # 검증, 역 코드, 암호화, 로깅
tests/                      # unit (Redis 불필요) / integration (Redis)
```

## 기술 스택

- **Korail API**: [devgyurak/pykorail](https://github.com/devgyurak/pykorail)
- **State**: Redis
- **Chat/Notifications**: Telegram Bot API (long polling), Kakao REST API (optional)
- **Crypto**: pycryptodome (AES-256-GCM)
- **Testing**: pytest

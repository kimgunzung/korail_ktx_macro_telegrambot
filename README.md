# 코레일 KTX 자동 예매 (korailbot)

매진된 KTX 열차를 자동으로 모니터링하여 좌석이 나오면 예약하고, 저장된 카드로 바로 결제까지 해 주는 **명령줄 도구**입니다.
예약 결과와 결제 마감 알림은 카카오톡 "나와의 채팅"으로 받습니다.

```bash
korailbot login                                  # 코레일 계정 저장 (암호화)
korailbot card set                               # (선택) 자동 결제 카드 등록
korailbot kakao link                             # (선택) 카카오톡 알림 연동
korailbot start --date 20261225 --from 서울 --to 부산 --time 0900 --until 1200
korailbot status                                 # 진행 상황 / 마지막 결과
korailbot cancel                                 # 검색 중단
```

> 설치부터 첫 예약까지의 단계별 안내(Ubuntu / Windows)는 **[docs/INSTALL.md](docs/INSTALL.md)** 를 보세요.

## 동작 방식

1. `korailbot start` 가 조건을 검증하고 **백그라운드 워커**를 띄운 뒤 바로 종료합니다. 워커는 터미널을 닫아도 계속 돌아갑니다.
2. 워커는 코레일에 로그인한 뒤 `SEARCH_INTERVAL`(기본 3초) 간격으로 좌석을 조회하고, 자리가 나오면 즉시 예약합니다.
3. 카드가 저장되어 있으면 예약 직후 **자동 결제**해 승차권을 발권합니다. 카드가 없거나 결제가 실패하면 결제 링크와 함께 안내하고, `PAYMENT_TIMEOUT_MINUTES`(기본 10분) 동안 카카오톡으로 리마인더를 보냅니다. 직접 결제한 뒤 `korailbot paid` 를 실행하면 알림이 멈춥니다.
4. 결과는 카카오톡 알림, `korailbot status`, `korailbot logs` 로 확인합니다.

CLI와 워커는 Redis로 상태를 공유합니다. 계정과 카드 정보는 Redis에 **AES-256-GCM으로 암호화**되어 저장됩니다.

## 참고

- 코레일 API 클라이언트로 [devgyurak/pykorail](https://github.com/devgyurak/pykorail)을 사용합니다 (코레일+ 7.0.8 규격, Python 3.10+).
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
| `status` | 진행 중인 검색, 마지막 결과, 결제 대기, 계정/카드/카카오 설정 상태 |
| `cancel` | 진행 중인 검색 중단 |
| `paid` | 직접 결제를 완료했음을 알림: 리마인더 중단, 랜덤 배치에서는 다음 좌석 검색 진행 |
| `logs [-n N] [-f]` | 최신 워커 로그 출력 (`-f` 실시간) |
| `card show / set / delete` | 자동 결제 카드 확인 / 등록·교체 / 삭제 |
| `kakao link / status / test / unlink` | 카카오톡 알림 연동 관리 |
| `kakao pair / unpair` | 카카오톡 챗봇 사용자 등록 코드 발급 / 전체 해제 |
| `serve [--host H] [--port P]` | 카카오톡 챗봇(오픈빌더 스킬) 서버 실행 |
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

## 카카오톡 챗봇 (카카오톡에서 예약 조작)

카카오톡 채널 챗봇으로 휴대폰에서 예약을 시작·조회·취소할 수 있습니다. `korailbot serve` 가 카카오 i 오픈빌더의 **스킬 서버**로 동작하며, 채널에 보낸 말을 받아 답합니다.

```
예약                              # 조건을 하나씩 물어보는 안내 (출발일 → 출발역 → … → 확인)
예약 20261225 서울 부산 0900 1200   # 한 줄로 시작
상태 / 취소 / 결제완료 / 카드 / 연동 / 처음부터 / 도움말
```

- **본인만 사용**: 처음 한 번 서버에서 `korailbot kakao pair` 로 코드를 만들고, 채널에 `등록 123456` 을 보내면 그 카카오 사용자만 봇을 조작할 수 있습니다 (`KAKAO_SKILL_ALLOWED_USERS` 로 고정도 가능). 등록되지 않은 사용자의 메시지는 거부됩니다.
- **보안**: 코레일 비밀번호와 카드번호는 카카오톡으로 입력받지 않습니다(채팅 내용은 카카오 서버에 남습니다). 계정·카드 등록은 서버에서 `korailbot login`, `korailbot card set` 으로만 합니다.
- **알림**: 예약 결과는 '나에게 보내기'(아래 알림 연동)로 옵니다. 챗봇에서 `연동` 을 보내면 연동 링크를 줍니다. 스킬 서버가 공개 주소를 갖고 있으므로 `KAKAO_REDIRECT_URI=https://<공개주소>/kakao/oauth` 로 두면 링크만 눌러도 연동이 끝납니다.
- **필요한 것**: 카카오톡 채널 + 오픈빌더 봇 + 공개 HTTPS 주소(도메인 + 인증서 또는 Cloudflare Tunnel). 설정 절차는 [docs/INSTALL.md 'B. 카카오톡 챗봇'](docs/INSTALL.md) 참고.
- **운영**: `korailbot serve` 는 `127.0.0.1:8090` 에서 HTTP로 듣습니다. 앞에 Caddy/nginx 로 HTTPS 를 붙이고 스킬 URL 을 `https://<도메인>/kakao/skill/<KAKAO_SKILL_SECRET>` 로 등록하세요. 오픈빌더는 5초 안에 응답을 요구하므로 서버와 Redis 는 같은 기기에 두는 것이 좋습니다.

## 카카오톡 알림

카카오 "나에게 보내기" API(`talk_message` 권한)를 사용해 예약 성공/실패, 결제 리마인더, 오류를 카카오톡 "나와의 채팅"으로 보냅니다. 카카오톡 채널이나 알림톡 비용은 없습니다.

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
| `KAKAO_SKILL_HOST` / `KAKAO_SKILL_PORT` | `127.0.0.1` / `8090` | 챗봇 스킬 서버 바인드 주소 |
| `KAKAO_SKILL_SECRET` | | 스킬 URL 비밀 경로 (`/kakao/skill/<secret>`), `openssl rand -hex 16` |
| `KAKAO_SKILL_ALLOWED_USERS` | | `kakao pair` 대신 허용할 오픈빌더 사용자 id(botUserKey) 목록 |
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
├── kakao_skill/            # 카카오톡 챗봇: server.py(HTTP), bot.py(명령·예약 안내), responses.py
├── config/settings.py      # 환경변수 / .env
├── models/                 # TrainSearchParams, CardInfo, KakaoTokens ...
├── services/
│   ├── korail_service.py       # pykorail 래퍼: 로그인, 조회, 예약, 결제
│   ├── reservation_service.py  # 워커 프로세스 시작/중단/상태
│   ├── kakao_service.py        # 카카오 OAuth, 나에게 보내기
│   └── notifier.py             # 로그 + 카카오톡 알림
├── storage/redis.py        # Redis 상태 저장 (카드/계정은 암호화)
├── worker/reservation_worker.py  # 백그라운드 검색·예약·결제·리마인더
└── utils/                  # 검증, 역 코드, 암호화, 로깅
tests/                      # unit (Redis 불필요) / integration (Redis)
```

## 기술 스택

- **Korail API**: [devgyurak/pykorail](https://github.com/devgyurak/pykorail)
- **State**: Redis
- **Notifications**: Kakao REST API (나에게 보내기)
- **Crypto**: pycryptodome (AES-256-GCM)
- **Testing**: pytest

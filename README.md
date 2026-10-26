# 코레일 KTX 예매 텔레그램 챗봇

매진된 KTX 열차를 자동으로 모니터링하여 좌석이 나오면 예약해주는 텔레그램 봇입니다.
카카오 계정을 연동하면 예약 결과와 결제 마감 알림을 카카오톡 "나와의 채팅"으로도 받을 수 있습니다.
예약 정보 입력 시 카드를 등록하면 좌석이 잡히는 즉시 자동으로 결제해 승차권까지 확정합니다.

## 빠른 시작

> **Ubuntu / Windows 설치부터 웹훅 등록까지의 단계별 안내는 [docs/INSTALL.md](docs/INSTALL.md)를 보세요.**

```bash
# 프로젝트 클론
git clone https://github.com/kimgunzung/korail_ktx_macro_telegrambot.git
cd korail_ktx_macro_telegrambot

# 환경변수 (.env 는 앱 시작 시 자동으로 읽힙니다)
cp .env.example .env     # BOTTOKEN, REDIS_HOST 등 수정

# 의존성 설치 및 실행 (Redis가 먼저 떠 있어야 합니다)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/app.py

# 텔레그램 웹훅 등록 (공개 HTTPS 주소 필요, 자세한 방법은 docs/INSTALL.md 3단계)
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<공개주소>/telebot"
```

## 참고

- 코레일 API 클라이언트로 [devgyurak/pykorail](https://github.com/devgyurak/pykorail)을 사용합니다 (코레일+ 7.0.8 규격, Python 3.10+).
- 과거에 사용했던 [carpedm20/korail2](https://github.com/carpedm20/korail2) 계열은 2026-09 코레일+ 앱 업데이트 이후 `-2000 이용 제한` 응답을 받아 동작하지 않습니다.
- 코레일은 AWS 등 데이터센터·VPN IP를 차단(`-8202`)하고, 매크로로 판정한 공인 IP도 차단합니다. 봇은 가정용 회선에서 실행하세요. 한 번 차단된 IP는 코드를 고쳐도 풀리지 않으며 공인 IP가 바뀌어야 합니다.
- DynaPath 기기 식별자는 `KORAIL_DEVICE_FILE`(기본 `~/.korail_bot_device.json`)에 저장되어 모든 프로세스가 같은 기기로 보이게 합니다. 고정하려면 `KORAIL_DEVICE_PROFILE_ID`/`KORAIL_ANDROID_ID`를 환경변수로 지정하세요.

## 주의사항

1. 귀경길 기차 예매를 하지 못한 안타까운 영혼들을 위해 만든 프로그램이므로, 개인용 목적이 아닌 상업적 목적등으로 이용하는 것을 엄중히 금합니다.
2. 본 프로그램을 사용할 경우, 기본으로 설정된 3초에 1번(`SEARCH_INTERVAL=3`) 조회 요청보다 빠르게 설정하지 마십시오. 짧은 조회 간격은 매크로 판정과 IP 차단의 주된 원인입니다. 코레일 서버에 무리가 갈 뿐 아니라, 단위 시간내에 보다 빠른 값으로 조회를 요청할 경우, 계정이 정지될 수 있습니다.
3. 본 프로그램은 2026-04-08일 기준으로 정상 동작하지만, 사이트의 구성이나 변수명 변경등에 따라 언제든 동작하지 않을 수 있습니다.

## 설정법

### 로컬 개발 (macOS/Linux)

#### 방법 1: mise 사용 (권장)

```bash
# 1. mise 설치 (없는 경우)
brew install mise

# 2. 환경 변수 설정
cp .env.default .env
# .env 파일을 열어서 BOTTOKEN 등 실제 값으로 수정

# 3. mise 활성화 (자동으로 .env.default와 .env 로드)
mise trust
mise install

# 4. 의존성 설치 및 실행
pipenv install
pipenv run python src/app.py
```

**환경변수 로딩 순서:**
1. 셸/Docker Compose/mise가 export 한 값 (최우선)
2. 레포 루트의 `.env` (앱이 시작할 때 자동으로 읽음, `ENV_FILE` 로 경로 변경 가능)

#### 방법 2: pipenv 직접 사용

```bash
# 1. 개발 환경 설정 (처음 한 번만)
make setup         # pipenv, pyenv 설치 (없는 경우)

# 2. 환경 변수 설정
export BOTTOKEN=your_telegram_bot_token

# 3. 의존성 설치
make install       # pipenv install 실행

# 4. 실행
make run           # 애플리케이션 실행

# 또는 쉘에 진입하여 실행
make shell         # pipenv shell 실행
python src/app.py
```

**사용 가능한 명령어:**
- `make help` - 사용 가능한 명령어 목록 확인
- `make setup` - 개발 환경 설정
- `make install` - 의존성 설치
- `make run` - 애플리케이션 실행
- `make shell` - pipenv shell 진입
- `make test` - 전체 테스트 실행
- `make test-unit` / `make test-integration` / `make test-e2e` - 단계별 테스트 실행
- `make requirements` - requirements.txt 생성

### Docker 배포

```bash
# 1. requirements.txt 생성 (Pipfile에서)
make requirements

# 2. Docker 이미지 빌드
make build

# 3. 실행
docker run -dit \
  -e BOTTOKEN=[텔레그램봇토큰] \
  -e ALLOW_LIST=[허용할전화번호목록] \
  -p 8080:8080 \
  geunsam2/korailbot:v3

# 또는 (관리자 편의 로그인 사용)
docker run -dit \
  -e BOTTOKEN=[텔레그램봇토큰] \
  -e ALLOW_LIST=[허용할전화번호목록] \
  -e USERID=[코레일ID] \
  -e USERPW=[코레일비밀번호] \
  -p 8080:8080 \
  geunsam2/korailbot:v3
```

### 환경변수

| 변수명 | 필수 | 설명 |
|--------|------|------|
| `BOTTOKEN` | ✅ | 텔레그램 봇 토큰 |
| `ALLOW_LIST` | ❌ | 허용할 사용자 전화번호 목록 (쉼표로 구분) |
| `USERID` | ❌ | 관리자 편의 로그인용 코레일 ID |
| `USERPW` | ❌ | 관리자 편의 로그인용 코레일 비밀번호 (관리자 명령어 비밀번호로도 사용) |
| `AUTO_PAYMENT_ENABLED` | ❌ | 기본 `True`. `False`면 예약 흐름에서 카드 자동 결제 단계를 건너뜀 |
| `CARD_ENCRYPTION_KEY` | ⚠️ | 저장 카드 암호화 키. 미설정 시 `BOTTOKEN`에서 파생(운영 환경에서는 반드시 설정) |
| `FLASK_DEBUG` | ❌ | 기본 `False`. `True`면 Werkzeug 디버거가 외부에 노출되므로 운영 환경에서는 켜지 마세요 |
| `PUBLIC_BASE_URL` | ❌ | 이 서버의 공개 URL (예: `https://bot.example.com`). 카카오 OAuth 리다이렉트에 사용 |
| `KAKAO_REST_API_KEY` | ❌ | 카카오 개발자 앱의 REST API 키. 설정하면 카카오톡 연동이 활성화됨 |
| `KAKAO_CLIENT_SECRET` | ❌ | 카카오 앱에서 Client Secret을 켠 경우 |
| `KAKAO_REDIRECT_URI` | ❌ | 기본값 `{PUBLIC_BASE_URL}/kakao/oauth` |

## 카드 자동 결제

예약 정보 입력의 마지막 단계(좌석 배치 선택 뒤)에서 **"예약 성공 시 카드로 자동 결제할까요?"** 라고 묻습니다.
처음 `Y`를 선택하면 카드번호 → 유효기간(YYMM 또는 MM/YY) → 카드 비밀번호 앞 2자리 → 소유자 확인번호(개인: 생년월일 6자리, 법인: 사업자등록번호 10자리)를 차례로 입력받아 **저장**하고,
이후 예약부터는 "저장된 카드(****1234)로 자동 결제할까요?"에 `Y`만 답하면 됩니다(`변경`을 입력하면 새 카드로 교체).
좌석이 잡히면 코레일 결제 API(pykorail `reservations.pay`)로 **일시불 결제**해 승차권을 발권합니다.

| 명령어 | 설명 |
|--------|------|
| `/card` | 저장된 카드 확인 (뒤 4자리, 유효기간, 저장일) |
| `/card_new` | 예약과 별개로 카드 등록 또는 교체 |
| `/card_delete` | 저장된 카드 삭제 |

- 연속 좌석: 예약 1건을 결제합니다. 랜덤 배치: 좌석마다 예약 직후 결제하고, 결제가 끝나면 사용자 확인 없이 바로 다음 좌석을 검색합니다.
- 결제에 성공하면 결제 리마인더는 시작되지 않습니다. 결제에 실패하면(한도 초과, 카드 정보 오류, 코레일 거부 등) 예약은 그대로 두고 사유와 함께 기존의 직접 결제 안내(리마인더 포함)로 넘어갑니다. 저장된 카드는 유지되므로 잘못된 카드라면 `/card_new`로 교체하세요.
- `N`을 선택하면 지금까지와 동일하게 예약만 하고 직접 결제합니다. 서버 전체에서 끄려면 `AUTO_PAYMENT_ENABLED=False`.

**카드 정보 취급**
- 저장된 카드(`saved_card:<chat_id>`)와 입력 중인 카드(`card_info:<chat_id>`, TTL 1시간)는 모두 **AES-256-GCM으로 암호화**되어 Redis에 저장됩니다. 키는 `CARD_ENCRYPTION_KEY` 환경변수에서 가져오며, 미설정 시 `BOTTOKEN`에서 파생하므로 운영 환경에서는 반드시 별도 키를 설정하세요(`openssl rand -hex 32`). 키를 바꾸면 기존 저장 카드는 복호화되지 않아 다시 등록해야 합니다.
- 예약 검색 프로세스에는 환경변수로 전달되며 이후 프로세스 메모리에서만 사용됩니다. argv에는 노출되지 않습니다.
- 카드번호·유효기간·비밀번호·확인번호를 입력한 텔레그램 메시지는 봇이 채팅에서 자동으로 삭제합니다(1:1 채팅, 48시간 이내 메시지는 봇이 삭제 가능). 삭제에 실패하면 직접 지우라고 안내합니다.
- 로그와 알림에는 카드번호 뒤 4자리(`****1234`)만 남고, 비밀번호·카드 입력 단계의 수신 메시지는 로그에 `<redacted>`로 기록됩니다.
- `/cancel`이나 최종 확인에서 `N`을 선택해도 저장된 카드는 유지됩니다. 삭제는 `/card_delete`로만 합니다.

## 카카오톡 연동

텔레그램 대화는 그대로 두고, **중요 알림(예약 성공/실패, 결제 마감, 오류)** 을 카카오톡 "나와의 채팅"으로도 복사해 보내줍니다.
카카오 "나에게 보내기" API(`talk_message` 권한)를 사용하므로 카카오톡 채널/알림톡 비용 없이 동작합니다.

### 서버 설정 (1회)

1. [Kakao Developers](https://developers.kakao.com)에서 애플리케이션을 만들고 **REST API 키**를 확인합니다.
2. **카카오 로그인**을 활성화하고 Redirect URI에 `https://<서버주소>/kakao/oauth` 를 등록합니다.
3. **동의항목**에서 `카카오톡 메시지 전송 (talk_message)` 을 활성화합니다.
4. 환경변수 `KAKAO_REST_API_KEY`, `PUBLIC_BASE_URL`(또는 `KAKAO_REDIRECT_URI`)을 설정하고 봇을 재시작합니다.
   서버가 공개 URL을 갖지 않는 경우에도 Kakao Developers에 등록한 아무 URI(예: `https://localhost:8080/kakao/oauth`)를 `KAKAO_REDIRECT_URI`로 지정하면 아래 수동 방식으로 연동할 수 있습니다.

### 사용자 연동

| 명령어 | 설명 |
|--------|------|
| `/kakao` | 카카오 로그인 링크를 받아 연동 시작 (이미 연동된 경우 상태 표시) |
| `/kakao_code <코드>` | 리다이렉트 페이지가 열리지 않을 때, 주소창의 `code=` 값을 복사해 수동 연동 |
| `/kakao_test` | 카카오톡으로 테스트 메시지 전송 |
| `/kakao_off` | 연동 해제 (저장된 토큰 삭제) |

- 토큰은 Redis(`kakao_tokens:<chat_id>`)에 저장되며 access token 은 자동 갱신됩니다. refresh token 이 만료되면(약 2개월) 연동이 해제되고 텔레그램으로 안내합니다.
- 카카오 텍스트 템플릿은 200자 제한이 있어 긴 알림은 `(1/3)` 처럼 여러 개로 나뉘어 전송됩니다.
- 10초마다 오는 결제 리마인더 자체는 카카오톡으로 보내지 않습니다(텔레그램 전용). 마감/중단 알림만 전달됩니다.

## 개발 워크플로우

### 테스트 실행

```bash
# 전체 테스트 실행 (Docker가 있으면 Redis 컨테이너를 자동으로 띄우고,
# 없으면 REDIS_HOST/REDIS_PORT(기본 localhost:6379)의 로컬 Redis를 사용)
make test

make test-unit          # tests/unit
make test-integration   # tests/integration
make test-e2e           # tests/e2e
```

자세한 구성은 [tests/README.md](tests/README.md)를 참고하세요.

### 의존성 추가 시
```bash
# 1. Pipfile에 패키지 추가
pipenv install [패키지명]

# 2. requirements.txt 재생성 (Docker 배포용)
make requirements

# 3. 커밋
git add Pipfile Pipfile.lock requirements.txt
git commit -m "feat: Add new dependency"
```

### pykorail 라이브러리 업데이트

```bash
pipenv update pykorail
```
src/
├── app.py                          # Flask 앱 진입점
├── config/                         # 설정 관리
├── models/                         # 데이터 모델
├── services/                       # 비즈니스 로직
├── storage/                        # 상태 관리
├── handlers/                       # 요청 처리
├── api/                            # API 엔드포인트
├── utils/                          # 유틸리티
└── telegramBot/                    # 레거시 코드

tests/                              # 테스트
```

## 기술 스택

- **Backend**: Flask, Flask-RESTful, Flask-CORS
- **Telegram**: python-telegram-bot
- **Korail API**: [devgyurak/pykorail](https://github.com/devgyurak/pykorail)
- **Testing**: pytest
- **Deployment**: Docker

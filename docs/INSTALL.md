# 설치 및 실행 가이드 (Ubuntu / Windows)

이 문서는 레포지토리를 클론한 뒤 텔레그램 봇이 실제로 메시지를 받고 예약 검색을 시작할 때까지의 전 과정을 다룹니다.

## 0. 시작하기 전에

| 준비물 | 설명 |
|--------|------|
| 텔레그램 봇 토큰 | 텔레그램에서 `@BotFather` → `/newbot` → 발급된 `123456:ABC...` 토큰 |
| Python 3.12 | pykorail이 3.10 이상을 요구하며, 프로젝트는 3.12로 고정되어 있습니다 |
| Redis | 세션·예약 상태 저장. Ubuntu는 패키지, Windows는 WSL2/Docker/Memurai |
| 공개 HTTPS 주소 | 텔레그램은 **웹훅(webhook)** 방식으로 봇에 메시지를 전달하므로, 인터넷에서 HTTPS로 접근 가능한 주소가 필요합니다 (아래 3단계 참고) |
| 가정용 인터넷 회선 | 코레일은 데이터센터·VPN IP를 차단(`-8202`)합니다. AWS 같은 클라우드에서는 동작하지 않습니다 |

봇은 두 종류의 프로세스로 동작합니다.

1. **웹 앱** (`src/app.py`, Flask, 기본 8080 포트): 텔레그램 웹훅과 카카오 OAuth 콜백을 받습니다.
2. **예약 검색 프로세스**: 사용자가 예약을 시작하면 웹 앱이 사용자별로 하나씩 띄우며, 결과를 웹 앱의 `/telebot` 콜백으로 보고합니다.

둘은 Redis를 통해 상태를 공유합니다.

---

## 1. Ubuntu (20.04 / 22.04 / 24.04)

### 1-1. 시스템 패키지

```bash
sudo apt update
sudo apt install -y git redis-server python3.12 python3.12-venv python3-pip
# Ubuntu 22.04 이하에서 python3.12가 없으면:
#   sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install -y python3.12 python3.12-venv

sudo systemctl enable --now redis-server
redis-cli ping        # PONG 이 나오면 정상
```

### 1-2. 클론 및 가상환경

```bash
git clone https://github.com/kimgunzung/korail_ktx_macro_telegrambot.git
cd korail_ktx_macro_telegrambot

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> pipenv를 쓰고 싶다면 `make setup && make install` (Pipfile 기준, Python 3.12 필요). 두 방법 중 하나만 쓰면 됩니다.

### 1-3. 환경변수 (`.env`)

```bash
cp .env.example .env
nano .env
```

최소한 아래 값을 채웁니다. `.env`는 앱 시작 시 자동으로 읽히며, 셸에서 export 한 값이 있으면 그 값이 우선합니다.

```dotenv
BOTTOKEN=123456:ABC-DEF...          # 필수
REDIS_HOST=localhost                # 로컬 Redis
REDIS_PORT=6379
ALLOW_LIST=010-1234-5678            # 비우면 모든 사용자 허용
USERID=                             # (선택) "근삼이최고" 관리자 편의 로그인용 코레일 ID
USERPW=                             # (선택) 관리자 비밀번호 겸 관리자 명령어 비밀번호
CARD_ENCRYPTION_KEY=$(openssl rand -hex 32 로 생성한 값)   # 카드 자동 결제를 쓸 경우 권장
PUBLIC_BASE_URL=https://xxxx.ngrok-free.app                # 3단계에서 얻은 공개 주소 (카카오 연동 시 필수)
```

### 1-4. 실행

```bash
source .venv/bin/activate
python src/app.py
```

정상이면 다음과 같은 로그가 보입니다.

```
Redis connected: localhost:6379
Korail KTX Telegram Bot - Redis Version
.env variables loaded: 7
Flask host: 0.0.0.0 / Flask port: 8080
KakaoTalk integration: disabled
Starting Flask application...
```

다른 터미널에서 `curl "http://localhost:8080/check_payment?chatId=1"` 이 `{"completed": false}` 를 돌려주면 웹 앱은 준비된 것입니다. 이어서 **3단계(웹훅 등록)** 로 넘어갑니다.

### 1-5. 상시 실행 (systemd, 선택)

```bash
sudo tee /etc/systemd/system/korailbot.service > /dev/null <<EOF
[Unit]
Description=Korail KTX Telegram Bot
After=network-online.target redis-server.service

[Service]
User=$USER
WorkingDirectory=$HOME/korail_ktx_macro_telegrambot
ExecStart=$HOME/korail_ktx_macro_telegrambot/.venv/bin/python src/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now korailbot
journalctl -u korailbot -f
```

### 1-6. Docker Compose로 실행 (대안)

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker

cp .env.example .env && nano .env        # REDIS_HOST=redis 로 두세요 (compose가 덮어씁니다)
docker compose up -d --build             # 로컬 소스로 빌드하려면 docker-compose.yml의 image 대신 build: . 사용
docker compose logs -f app
```

기본 `docker-compose.yml`은 Docker Hub의 `geunsam2/korailbot:latest` 이미지를 받아 8000 포트로 노출합니다. 직접 수정한 코드를 띄우려면 `image:` 줄을 `build: .` 로 바꾸세요.

---

## 2. Windows 10 / 11

PowerShell(관리자 권한 불필요)을 기준으로 설명합니다.

### 2-1. Git, Python 설치

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
```

새 PowerShell 창을 열고 확인합니다.

```powershell
git --version
py -3.12 --version
```

### 2-2. Redis 준비 (셋 중 하나)

**A. WSL2 (권장)**

```powershell
wsl --install -d Ubuntu          # 처음 한 번, 재부팅 후 Ubuntu 초기 설정
wsl sudo apt update
wsl sudo apt install -y redis-server
wsl sudo service redis-server start
wsl redis-cli ping               # PONG
```

WSL2의 Redis는 Windows 쪽에서 `localhost:6379` 로 접근할 수 있습니다. (Windows를 재시작하면 `wsl sudo service redis-server start` 를 다시 실행해야 합니다.)

**B. Docker Desktop**

```powershell
docker run -d --name korail_redis -p 6379:6379 --restart unless-stopped redis:7-alpine
```

**C. Memurai (네이티브 Windows Redis 호환 서버)**
https://www.memurai.com 에서 Developer 에디션을 설치하면 서비스로 등록되어 `localhost:6379` 에서 동작합니다.

### 2-3. 클론 및 가상환경

```powershell
git clone https://github.com/kimgunzung/korail_ktx_macro_telegrambot.git
cd korail_ktx_macro_telegrambot

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> `Activate.ps1` 실행이 막히면(실행 정책) 한 번만: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2-4. 환경변수 (`.env`)

```powershell
Copy-Item .env.example .env
notepad .env
```

내용은 Ubuntu와 같습니다(1-3 참고). `REDIS_HOST=localhost` 로 두고, `CARD_ENCRYPTION_KEY` 는 PowerShell에서 아래처럼 만들 수 있습니다.

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
```

### 2-5. 실행

```powershell
.\.venv\Scripts\Activate.ps1
python src\app.py
```

Windows 방화벽 알림이 뜨면 "개인 네트워크"에서 허용합니다. 다른 PowerShell 창에서 확인:

```powershell
Invoke-RestMethod "http://localhost:8080/check_payment?chatId=1"     # completed : False
```

이어서 **3단계(웹훅 등록)** 로 넘어갑니다.

### 2-6. 부팅 시 자동 실행 (선택)

작업 스케줄러에 "로그온 시" 트리거로 아래 명령을 등록합니다.

```
프로그램:   C:\경로\korail_ktx_macro_telegrambot\.venv\Scripts\python.exe
인수:       src\app.py
시작 위치:  C:\경로\korail_ktx_macro_telegrambot
```

Redis를 WSL2로 띄웠다면 같은 방식으로 `wsl.exe -u root service redis-server start` 도 등록해 둡니다.

---

## 3. 텔레그램 웹훅 등록 (두 OS 공통, 필수)

텔레그램 서버가 봇의 `/telebot` 엔드포인트로 메시지를 POST 하려면 **인터넷에서 접근 가능한 HTTPS 주소**가 필요합니다. 가정용 PC에서 가장 쉬운 방법은 터널 서비스입니다.

### 3-1. 공개 주소 만들기 (하나 선택)

**ngrok**

```bash
# Ubuntu: https://ngrok.com/download 참고 (snap install ngrok 도 가능)
# Windows: winget install --id ngrok.ngrok -e
ngrok config add-authtoken <ngrok 토큰>
ngrok http 8080
```

출력된 `https://xxxx.ngrok-free.app` 이 공개 주소입니다. 무료 플랜은 재시작마다 주소가 바뀌므로 바뀔 때마다 3-2를 다시 합니다. 고정 도메인이 필요하면 ngrok의 무료 static domain 또는 Cloudflare Tunnel을 쓰세요.

**Cloudflare Tunnel** (도메인이 있을 때)

```bash
cloudflared tunnel --url http://localhost:8080
```

**자체 서버 + 리버스 프록시**: 공인 IP와 도메인이 있다면 nginx/caddy로 TLS를 종단하고 `localhost:8080` 으로 프록시합니다.

### 3-2. 웹훅 설정

공개 주소를 얻었으면 텔레그램에 등록합니다. (`<TOKEN>`은 봇 토큰, `<PUBLIC>`은 공개 주소)

```bash
# Ubuntu
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC>/telebot&drop_pending_updates=true"
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

```powershell
# Windows
Invoke-RestMethod "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC>/telebot&drop_pending_updates=true"
Invoke-RestMethod "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

`getWebhookInfo` 의 `url` 이 내 주소이고 `last_error_message` 가 없으면 완료입니다. 텔레그램에서 봇에게 `/start` 를 보내면 안내 메시지가 와야 합니다.

카카오톡 연동을 쓰려면 같은 공개 주소를 `.env` 의 `PUBLIC_BASE_URL` 에 넣고 Kakao Developers의 Redirect URI에 `<PUBLIC>/kakao/oauth` 를 등록합니다 (README "카카오톡 연동" 참고).

---

## 4. 첫 예약 흐름 점검

1. 텔레그램에서 `/start` → `Y` → 코레일 휴대폰번호(`010-1234-5678`) → 비밀번호 → 출발일(`20261225`) → 출발역/도착역 → 검색 시작/종료 시각 → 열차 종류 → 좌석 종류 → 인원 → (2명 이상이면 좌석 배치) → 자동 결제 여부 → 최종 확인 `Y`.
2. 웹 앱 로그에 `Started reservation process for chat_id=..., pid=...` 가 찍히고, 예약 검색 프로세스 로그(`Background Process Initialized`)가 이어집니다.
3. `/status` 로 진행 상황을, `/cancel` 로 중단을 확인합니다.

---

## 5. 테스트 실행

```bash
# Ubuntu (로컬 Redis 사용)
source .venv/bin/activate
pip install pytest pytest-mock freezegun
python -m pytest tests -q
```

```powershell
# Windows
.\.venv\Scripts\Activate.ps1
pip install pytest pytest-mock freezegun
python -m pytest tests -q
```

Docker가 설치되어 있고 `testcontainers` 가 있으면 테스트용 Redis 컨테이너를 자동으로 띄우고, 없으면 `REDIS_HOST:REDIS_PORT` 의 Redis를 사용합니다. 통합 테스트는 매 테스트 후 해당 DB를 `FLUSHDB` 하므로 운영용 Redis와는 다른 포트/DB 번호를 쓰세요 (`REDIS_DB=15` 등).

---

## 6. 문제 해결

| 증상 | 원인 / 조치 |
|------|-------------|
| `Configuration error: BOTTOKEN environment variable is required` | `.env` 가 레포 루트에 없거나 `BOTTOKEN` 이 비어 있음. `ENV_FILE=/경로/.env` 로 위치를 지정할 수도 있음 |
| `Failed to initialize Redis storage` | Redis 미실행. Ubuntu `sudo systemctl start redis-server`, WSL `wsl sudo service redis-server start`, Docker `docker start korail_redis` |
| 봇에 메시지를 보내도 반응 없음 | `getWebhookInfo` 의 `last_error_message` 확인. 터널 주소가 바뀌었으면 `setWebhook` 재실행. 로컬에서 `curl -X POST localhost:8080/telebot -H 'Content-Type: application/json' -d '{"message":{"chat":{"id":1},"text":"/help"}}'` 로 앱 자체는 동작하는지 확인 |
| `코레일 이용 제한` / `-8202` / `-2000` | 클라우드·VPN IP 또는 매크로 판정으로 차단됨. 가정용 회선에서 실행하고 `SEARCH_INTERVAL` 을 3초 미만으로 낮추지 말 것. 차단된 공인 IP는 바뀌어야 풀림 |
| 로그인은 성공인데 `P058` / `WRC000420` 안내 | 코레일+ 앱에서 로그인해 비밀번호 변경 안내를 처리(변경 또는 다음에 하기)한 뒤 재시도 |
| 역 이름 검증 실패 | 역 목록을 코레일에서 받아오지 못하면 내장 목록으로 대체됨. `'역'` 을 빼고 입력 (`서울`, `광명`) |
| Windows: `Activate.ps1` 실행 불가 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Windows: 예약 검색 프로세스가 바로 종료 | 웹 앱과 같은 `python.exe`(.venv)로 기동되는지, Redis가 `localhost:6379` 에서 응답하는지 확인 |
| 카드 저장 후 `Cannot decrypt saved_card` | `CARD_ENCRYPTION_KEY` 가 바뀜. `/card_delete` 후 `/card_new` 로 재등록 |

로그를 더 자세히 보려면 텔레그램에서 관리자 인증 후 `/debug_on` 을 보내거나 `.env` 에 `LOG_LEVEL=DEBUG` 를 설정합니다.

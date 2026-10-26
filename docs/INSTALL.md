# 설치 및 실행 가이드 (Ubuntu / Windows)

레포지토리를 클론한 뒤 첫 예약 검색을 시작할 때까지의 전 과정입니다. 이 도구는 **명령줄(CLI)** 로 조작하고 결과는 **카카오톡**으로 받습니다. 공개 서버나 도메인은 필요 없습니다.

## 0. 시작하기 전에

| 준비물 | 설명 |
|--------|------|
| Python 3.12 | pykorail이 3.10 이상을 요구하며, 프로젝트는 3.12로 고정되어 있습니다 |
| Redis | CLI와 백그라운드 워커가 상태를 공유하는 저장소. Ubuntu는 패키지, Windows는 WSL2/Docker/Memurai |
| 코레일 계정 | 휴대폰 번호(`010-1234-5678`), 이메일 또는 회원번호 + 비밀번호 |
| 가정용 인터넷 회선 | 코레일은 데이터센터·VPN IP를 차단(`-8202`)합니다. 클라우드 서버에서는 동작하지 않습니다 |
| (선택) 카카오 개발자 앱 | 카카오톡 알림을 받으려면 필요 (3단계) |

실행 구조: `korailbot start` 는 조건을 확인하고 **백그라운드 워커**를 띄운 뒤 끝납니다. 워커가 조회·예약·결제·알림을 담당하며 터미널을 닫아도 계속 실행됩니다. 로그는 `~/.korail_bot/logs/` 에 남습니다.

---

## 1. Ubuntu (20.04 / 22.04 / 24.04)

### 1-1. 시스템 패키지

```bash
sudo apt update
sudo apt install -y git redis-server python3.12 python3.12-venv
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

레포 루트의 `./korailbot` 스크립트가 `.venv`를 자동으로 사용합니다. 어디서든 쓰려면 PATH에 추가하세요.

```bash
sudo ln -s "$PWD/korailbot" /usr/local/bin/korailbot
korailbot --help
```

### 1-3. 환경변수 (`.env`)

```bash
cp .env.example .env
nano .env
```

로컬 Redis면 기본값(`REDIS_HOST=localhost`)으로 충분합니다. 운영에서는 `CARD_ENCRYPTION_KEY`를 지정하는 것을 권장합니다.

```bash
echo "CARD_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env
```

### 1-4. 계정 저장과 첫 실행

```bash
korailbot login          # 코레일 아이디/비밀번호 입력 → 로그인 확인 후 암호화 저장
korailbot status         # Redis 연결과 설정 상태 확인
```

이어서 **3단계(카카오톡 알림, 선택)** 와 **4단계(첫 예약)** 로 넘어갑니다.

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

WSL2의 Redis는 Windows 쪽에서 `localhost:6379` 로 접근됩니다. (재부팅 후에는 `wsl sudo service redis-server start` 를 다시 실행)

**B. Docker Desktop**

```powershell
docker run -d --name korail_redis -p 127.0.0.1:6379:6379 --restart unless-stopped redis:7-alpine
```

**C. Memurai** (네이티브 Windows Redis 호환): https://www.memurai.com 에서 Developer 에디션을 설치하면 서비스로 등록되어 `localhost:6379` 에서 동작합니다.

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

레포 루트의 `korailbot.cmd` 가 `.venv`를 자동으로 사용합니다. 레포 폴더에서 `.\korailbot <명령>` 으로 실행하거나, 폴더를 PATH에 추가하세요.

### 2-4. 환경변수 (`.env`)

```powershell
Copy-Item .env.example .env
notepad .env
```

내용은 Ubuntu와 같습니다. `CARD_ENCRYPTION_KEY` 는 아래처럼 만들 수 있습니다.

```powershell
-join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
```

### 2-5. 계정 저장과 첫 실행

```powershell
.\korailbot login
.\korailbot status
```

Windows 방화벽 알림이 뜨면(카카오 연동 시 로컬 포트) "개인 네트워크"에서 허용합니다.

---

## 3. 카카오톡 알림 연동 (선택, 두 OS 공통)

예약 성공/실패와 결제 리마인더를 카카오톡 "나와의 채팅"으로 받습니다. 연동하지 않으면 결과는 `korailbot status` / `korailbot logs` 로만 확인합니다.

1. [Kakao Developers](https://developers.kakao.com) → **내 애플리케이션** → 앱 추가. **앱 키 > REST API 키**를 복사합니다.
2. **카카오 로그인** 메뉴에서 활성화 ON, **Redirect URI** 에 `http://localhost:8080/kakao/oauth` 등록.
3. **카카오 로그인 > 동의항목** 에서 `카카오톡 메시지 전송 (talk_message)` 을 "이용 중 동의"로 설정.
4. `.env` 에 추가:
   ```dotenv
   KAKAO_REST_API_KEY=발급받은_REST_API_키
   # KAKAO_CLIENT_SECRET=   (보안 > Client Secret 을 '사용함'으로 켠 경우만)
   ```
5. 연동:
   ```bash
   korailbot kakao link
   ```
   브라우저가 열리면 카카오 로그인 후 권한에 동의합니다. 브라우저가 `localhost:8080`으로 돌아오면 자동으로 연동이 끝나고 카카오톡으로 확인 메시지가 옵니다.
   브라우저가 없는 환경이면 출력된 주소를 다른 기기에서 열고, 마지막에 도착한 주소창의 `code=` 뒤 값을 복사해 `korailbot kakao link --code <값>` 으로 전달하세요.
6. 확인: `korailbot kakao test`

---

## 4. 첫 예약 검색

```bash
korailbot stations 광주                 # 역 이름 확인 ('역' 제외)
korailbot card set                      # (선택) 자동 결제 카드 등록 - 화면에 표시되지 않음
korailbot start --date 20261225 --from 서울 --to 부산 --time 0900 --until 1200
```

조건 요약이 출력되고 `Y`를 입력하면 워커가 시작됩니다 (`-y`로 질문 생략).

```bash
korailbot status        # 진행 중 여부, 마지막 결과, 결제 대기
korailbot logs -f       # 워커 로그 실시간 보기
korailbot cancel        # 중단
```

- 카드가 저장되어 있으면 좌석이 잡히는 즉시 결제되어 "💳 자동 결제 완료" 알림이 옵니다.
- 카드가 없거나 결제에 실패하면 결제 링크와 함께 안내가 오고, 10분 동안 2분마다 리마인더가 옵니다. 코레일 앱/홈페이지에서 결제한 뒤 `korailbot paid` 를 실행하세요.
- 랜덤 배치(`--strategy random`)에서 직접 결제하는 경우, 좌석이 하나 잡힐 때마다 `korailbot paid` 를 실행해야 다음 좌석 검색이 시작됩니다 (10분이 지나면 자동 진행).

### 자동 실행 / Docker (선택)

워커는 `start` 가 띄운 뒤 스스로 종료되므로 상주 서비스는 필요 없습니다. 재부팅 후 Redis만 다시 켜 주세요 (Ubuntu는 systemd가 자동 실행). Docker를 선호하면:

```bash
docker compose up -d redis                    # Redis만 컨테이너로
# 또는 CLI까지 컨테이너로
docker compose build
docker compose run --rm bot login
docker compose run --rm -d bot start --date 20261225 --from 서울 --to 부산 --time 0900 -y
docker compose run --rm bot status
```

---

## 5. 테스트 실행

```bash
# Ubuntu
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

Docker와 `testcontainers` 가 있으면 테스트용 Redis 컨테이너를 자동으로 띄우고, 없으면 `REDIS_HOST:REDIS_PORT` 의 Redis를 사용합니다. 통합 테스트는 매 테스트 후 해당 DB를 `FLUSHDB` 하므로 운영용과 다른 DB 번호를 쓰세요 (`REDIS_DB=15`).

---

## 6. 문제 해결

| 증상 | 원인 / 조치 |
|------|-------------|
| `Redis에 연결할 수 없습니다` | Redis 미실행. Ubuntu `sudo systemctl start redis-server`, WSL `wsl sudo service redis-server start`, Docker `docker start korail_redis`. `.env`의 `REDIS_HOST/PORT` 확인 |
| `korailbot: command not found` | 레포 루트에서 `./korailbot` 또는 `python src/cli.py` 로 실행, 혹은 1-2의 심볼릭 링크 생성 |
| `코레일 로그인 실패` | 아이디/비밀번호 확인. 로그인은 성공인데 `P058`/`WRC000420` 안내가 나오면 코레일+ 앱에서 로그인해 비밀번호 변경 안내를 처리(변경 또는 다음에 하기)한 뒤 재시도 |
| `코레일 이용 제한` / `-8202` / `-2000` | 클라우드·VPN IP 또는 매크로 판정으로 차단. 가정용 회선에서 실행하고 `SEARCH_INTERVAL`을 3초 미만으로 낮추지 말 것. 차단된 공인 IP는 바뀌어야 풀림 |
| 역 이름 검증 실패 | `korailbot stations <검색어>` 로 정확한 이름 확인. `'역'`을 빼고 입력 (`서울`, `광명`) |
| `start` 후 아무 알림이 없음 | 매진이면 자리가 날 때까지 조회만 계속합니다. `korailbot logs -f` 로 조회 로그 확인, `korailbot status` 로 워커 생존 확인 |
| 카카오 `kakao link` 가 응답을 못 받음 | Redirect URI가 `http://localhost:8080/kakao/oauth` 로 정확히 등록됐는지, 8080 포트를 다른 프로그램이 쓰고 있지 않은지(`KAKAO_LOCAL_PORT` 변경) 확인. 수동으로 `--code` 전달 가능 |
| 카카오 `insufficient scopes (-402)` | 동의항목에서 `talk_message` 가 꺼져 있음. 켠 뒤 `korailbot kakao unlink` → `kakao link` 재연동 |
| `Cannot decrypt saved_card` / 계정 복호화 실패 | `CARD_ENCRYPTION_KEY` 가 바뀌었거나 `~/.korail_bot/secret.key` 가 삭제됨. `korailbot login`, `korailbot card set` 으로 재등록 |
| Windows: `Activate.ps1` 실행 불가 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Windows: 워커가 바로 종료 | `korailbot logs` 로 오류 확인. Redis가 `localhost:6379`에서 응답하는지 확인 |

로그를 더 자세히 보려면 `.env` 에 `LOG_LEVEL=DEBUG` 를 설정하고 다음 `start` 부터 `korailbot logs -f` 로 확인합니다.

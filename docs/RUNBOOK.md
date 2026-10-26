# 서버 재구축 런북 (Ubuntu, 텔레그램 봇 기준)

코레일에 IP가 차단되거나 서버를 바꿔야 할 때, **빈 Ubuntu 기계에서 텔레그램 봇이 돌아갈 때까지**
순서대로 실행하는 체크리스트입니다. 처음 구축할 때도 이 문서만 따라 하면 됩니다.

> 전제: 새 서버는 **가정용 회선**(차단된 서버와 다른 공인 IP)에 있고, 사용자는 `sudo`를 쓸 수 있습니다.
> 클라우드·VPN IP에서는 코레일이 조회를 막습니다.

---

## 0. 예전 서버에서 챙길 것 (접속이 되는 경우)

| 항목 | 위치 | 필요성 |
|------|------|--------|
| `.env` | 레포 루트 | **필수**. 텔레그램 토큰, `CARD_ENCRYPTION_KEY` 등 설정 전체 |
| `~/.korail_bot/secret.key` | 홈 | `.env`에 `CARD_ENCRYPTION_KEY`가 **없을 때만** 필요. 없으면 새 서버에서 계정·카드를 다시 등록하면 됨 |
| 코레일 아이디/비밀번호, 카드 정보 | 머릿속/안전한 곳 | 새 서버에서 `korailbot login`, `korailbot card set`으로 다시 입력 |

복사하지 **않는** 것:
- `~/.korail_bot_device.json` (코레일 기기 프로필). 차단된 서버의 식별 정보일 수 있으니 새로 생성되게 둡니다.
- Redis 데이터. 계정·카드는 다시 등록하는 편이 안전하고 빠릅니다.

예전 서버에서 봇을 **반드시 끕니다**. 같은 토큰으로 두 곳에서 폴링하면 메시지를 번갈아 놓칩니다.

```bash
systemctl --user disable --now korailbot-telegram     # 사용자 서비스로 등록한 경우
sudo systemctl disable --now korailbot-telegram       # 시스템 서비스로 등록한 경우
korailbot cancel                                      # 진행 중인 검색이 있으면 중단
```

`.env`를 가져올 수 없으면 텔레그램 `@BotFather` → `/mybots` → 봇 선택 → `API Token`에서 토큰을 다시 볼 수 있습니다.
토큰이 유출됐다고 의심되면 같은 메뉴의 `Revoke current token`으로 새로 발급합니다.

---

## 1. 시스템 패키지

```bash
sudo apt update
sudo apt install -y git redis-server python3.12 python3.12-venv
# Ubuntu 22.04 이하에서 python3.12가 없으면:
#   sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install -y python3.12 python3.12-venv

sudo systemctl enable --now redis-server
redis-cli ping        # PONG
```

## 2. 클론과 가상환경

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/kimgunzung/korail_ktx_macro_telegrambot.git
cd korail_ktx_macro_telegrambot

python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

sudo ln -sf "$PWD/korailbot" /usr/local/bin/korailbot
korailbot --help
```

`./korailbot` 래퍼가 `.venv`를 자동으로 사용하므로 가상환경을 매번 활성화할 필요는 없습니다.

## 3. 설정 파일 `.env`

예전 `.env`가 있으면 레포 루트에 복사합니다. 없으면 새로 만듭니다.

```bash
cp .env.example .env
echo "CARD_ENCRYPTION_KEY=$(openssl rand -hex 32)" >> .env
echo "TELEGRAM_BOT_TOKEN=BotFather에서_받은_토큰" >> .env
chmod 600 .env
```

확인할 값:

| 키 | 값 |
|----|----|
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` (로컬 Redis) |
| `SEARCH_INTERVAL` | `3` 이상. 줄이면 차단 위험 |
| `TELEGRAM_BOT_TOKEN` | `123456789:AAE...` 형태 전체 |
| `CARD_ENCRYPTION_KEY` | 예전 서버 값 그대로, 또는 새로 생성 |

## 4. 코레일 계정과 카드 등록 (서버 터미널에서만)

```bash
korailbot login        # 아이디/비밀번호 입력 → 로그인 확인 후 암호화 저장
korailbot card set     # 자동 결제 카드 (카드번호, 유효기간, 비밀번호 앞 2자리, 생년월일/사업자번호)
korailbot status       # Redis 연결, 계정, 카드, 텔레그램 상태 확인
```

`login`이 `코레일 이용 제한`, `-8202`, `-2000`으로 실패하면 이 서버의 IP도 차단된 것입니다. 다른 회선이 필요합니다.

## 5. 텔레그램 봇을 사용자 systemd 서비스로 등록

터미널을 닫거나 재부팅해도 봇이 계속 돌도록 서비스로 등록합니다. `sudo`는 linger 한 줄에만 필요합니다.

```bash
mkdir -p ~/.config/systemd/user
tee ~/.config/systemd/user/korailbot-telegram.service > /dev/null <<'UNIT'
[Unit]
Description=Korail KTX bot (Telegram)
After=network-online.target

[Service]
WorkingDirectory=%h/projects/korail_ktx_macro_telegrambot
ExecStart=%h/projects/korail_ktx_macro_telegrambot/korailbot telegram
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now korailbot-telegram
sudo loginctl enable-linger "$USER"        # 로그아웃 후에도 유지, 재부팅 시 자동 시작

systemctl --user status korailbot-telegram --no-pager
journalctl --user -u korailbot-telegram -n 20 --no-pager
```

`%h`는 홈 디렉터리입니다. 2단계와 다른 위치에 클론했다면 두 경로를 맞춥니다.
상태가 `active (running)`이고 로그에 `봇: @이름` 줄이 보이면 텔레그램 연결까지 된 것입니다.
`203/EXEC`가 반복되면 `ExecStart` 경로가 틀렸거나 `korailbot`에 실행 권한이 없는 것입니다 (`chmod +x korailbot`).

## 6. 휴대폰 텔레그램 채팅 등록

```bash
korailbot pair        # 10분 유효한 1회용 6자리 코드 출력
```

아이폰/안드로이드 텔레그램에서 봇 대화방을 열고 `/start` → `등록 123456`(출력된 코드)을 보냅니다.
"등록되었습니다"가 오면 끝입니다. 등록 정보는 서버 Redis에 저장되므로 **서버를 옮기면 다시 등록**해야 합니다.

## 7. 동작 확인

텔레그램에서:

```
상태                                → 계정, 카드, 검색 상태 표시
조회 20261225 서울 부산 0900 1200     → 조회 결과: 시간표와 좌석 현황 (예약 안 함)
예약 20261225 서울 부산 0900 1200     → 한 줄로 검색 시작 (또는 `예약` 만 보내고 하나씩 답함)
예약목록                             → 예약 결과: 코레일에 잡힌 결제 전 예약
취소                                → 검색 중단
```

서버에서:

```bash
korailbot search --date 20261225 --from 서울 --to 부산 --time 0900 --until 1200   # 조회 결과
korailbot reservations                                                        # 예약 결과
korailbot status
korailbot logs -f        # 워커 조회 로그 실시간 확인
```

---

## 운영 중 자주 쓰는 명령

| 할 일 | 명령 |
|------|------|
| 봇 재시작 (`.env` 수정 후) | `systemctl --user restart korailbot-telegram` |
| 봇 중지 / 시작 | `systemctl --user stop korailbot-telegram` / `start` |
| 봇 로그 | `journalctl --user -u korailbot-telegram -f` |
| 코드 업데이트 | `cd ~/projects/korail_ktx_macro_telegrambot && git pull && .venv/bin/pip install -r requirements.txt && systemctl --user restart korailbot-telegram` |
| 검색 중단 | `korailbot cancel` 또는 텔레그램 `취소` |
| 등록 채팅 전체 해제 | `korailbot unpair` |
| 카드 삭제 / 계정 삭제 | `korailbot card delete` / `korailbot logout` |

`SEARCH_INTERVAL` 같은 설정을 바꾸면 봇을 재시작하고, 이미 돌고 있는 검색은 `취소` 후 다시 `예약`해야 새 값이 적용됩니다.

## 문제 해결

| 증상 | 조치 |
|------|------|
| `Redis에 연결할 수 없습니다` | `sudo systemctl start redis-server`, `.env`의 `REDIS_HOST/PORT` 확인 |
| 서비스가 `203/EXEC`로 반복 실패 | 유닛 파일의 두 경로가 실제 클론 위치인지, `korailbot`이 실행 가능한지 확인 |
| 재부팅 후 봇이 안 뜸 | `loginctl show-user $USER \| grep Linger` 가 `Linger=yes`인지 확인, 아니면 5단계 linger 명령 |
| 텔레그램 답이 없음 | `systemctl --user status korailbot-telegram`, `pgrep -af "cli.py telegram"`으로 인스턴스가 **하나만** 도는지 확인 |
| `토큰 확인 실패` | `.env` 토큰 오타, 방화벽이 `api.telegram.org`를 막는지 확인 |
| "등록되지 않은 사용자" | `korailbot pair` 후 10분 안에 `등록 <코드>` 전송 |
| `-8204` / `최신 버전으로 업데이트` | 코레일 앱 규격 변경으로 구버전 pykorail 거부. `git pull` → `.venv/bin/pip install -r requirements.txt` → 봇 재시작 |
| `코레일 이용 제한` / `-8202` / `-2000` | IP 차단. 다른 회선에서 이 문서대로 재구축. `SEARCH_INTERVAL`을 3 미만으로 내리지 말 것 |
| 계정/카드 복호화 실패 | `CARD_ENCRYPTION_KEY`가 예전과 다름. 예전 값을 넣거나 `login`, `card set`으로 재등록 |

#!/usr/bin/env bash
# korailbot one-shot installer for Ubuntu/Debian.
#
#   git clone <repo> && cd korail_ktx_macro_telegrambot && ./scripts/install.sh
#
# Installs system packages (git, redis, python), creates .venv, writes .env, registers the
# Telegram bot as a user systemd service and then offers to store the Korail account and card.
# Safe to run again: existing .env, .venv and saved data are kept.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
DO_APT=1; DO_SERVICE=1; DO_LINK=1; INTERACTIVE=1; ENV_SOURCE=""
SERVICE=korailbot-telegram

usage() {
  cat <<'USAGE'
사용법: scripts/install.sh [옵션]
  --env FILE         이전 장치의 .env 를 복사해 사용 (.env 가 없을 때만)
  --no-apt           시스템 패키지 설치 건너뛰기 (git, redis-server, python 이 이미 있을 때)
  --no-service       텔레그램 봇 systemd 사용자 서비스 등록 건너뛰기
  --no-link          /usr/local/bin/korailbot 링크 만들지 않기
  -y, --yes          질문 없이 진행 (토큰·계정·카드 입력 단계는 건너뜀)
  -h, --help         이 도움말
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_SOURCE="${2:?--env 뒤에 파일 경로가 필요합니다}"; shift ;;
    --no-apt) DO_APT=0 ;;
    --no-service) DO_SERVICE=0 ;;
    --no-link) DO_LINK=0 ;;
    -y|--yes) INTERACTIVE=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[ -t 0 ] || INTERACTIVE=0

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '  ⚠️  %s\n' "$*" >&2; }
die()  { printf '\n❌ %s\n' "$*" >&2; exit 1; }
ask()  { [ "$INTERACTIVE" = 1 ] || return 1; local a; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=""
cd "$ROOT"
[ -f src/cli.py ] && [ -d src/pykorail ] || die "저장소 루트를 찾지 못했습니다: $ROOT"

find_python() {
  local c
  for c in python3.12 python3.13 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------- 1. system packages
if [ "$DO_APT" = 1 ]; then
  say "1/6 시스템 패키지 설치 (git, redis-server, python)"
  command -v apt-get >/dev/null 2>&1 || die "apt-get 이 없습니다. Ubuntu/Debian 이 아니면 docs/INSTALL.md 를 따르거나 --no-apt 로 실행하세요."
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq git redis-server python3 python3-venv openssl
  if ! find_python >/dev/null; then
    $SUDO apt-get install -y -qq python3.12 python3.12-venv || die \
      "Python 3.11 이상이 필요합니다. Ubuntu 22.04 이하: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv"
  fi
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    $SUDO systemctl enable --now redis-server >/dev/null 2>&1 || warn "redis-server 서비스를 켜지 못했습니다."
  fi
else
  say "1/6 시스템 패키지 설치 건너뜀 (--no-apt)"
fi
PYTHON="$(find_python)" || die "Python 3.11 이상을 찾지 못했습니다."
note "Python: $($PYTHON --version 2>&1)"
if command -v redis-cli >/dev/null 2>&1 && [ "$(redis-cli ping 2>/dev/null)" = PONG ]; then
  note "Redis: 응답 확인 (PONG)"
else
  warn "Redis 가 응답하지 않습니다. 'sudo systemctl start redis-server' 후 korailbot status 로 확인하세요."
fi

# ---------------------------------------------------------------- 2. virtualenv + dependencies
say "2/6 가상환경과 의존성 설치"
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv || die "가상환경을 만들지 못했습니다. python3-venv 패키지를 설치하세요."
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
# An older checkout installed pykorail into the venv; the in-tree source (src/pykorail) is used now.
.venv/bin/python -m pip uninstall --quiet -y pykorail >/dev/null 2>&1 || true
chmod +x korailbot
PYKORAIL="$(cd src && ../.venv/bin/python -c 'import pykorail; print(pykorail.__version__)')" \
  || die "pykorail 을 임포트하지 못했습니다."
note "pykorail $PYKORAIL (src/pykorail), 의존성 설치 완료"

# ---------------------------------------------------------------- 3. command link
if [ "$DO_LINK" = 1 ]; then
  say "3/6 korailbot 명령 등록 (/usr/local/bin/korailbot)"
  if $SUDO ln -sf "$ROOT/korailbot" /usr/local/bin/korailbot 2>/dev/null; then
    note "어디서든 'korailbot' 으로 실행할 수 있습니다."
  else
    warn "링크를 만들지 못했습니다. $ROOT/korailbot 으로 직접 실행하세요."
  fi
else
  say "3/6 명령 등록 건너뜀 (--no-link)"
fi
BOT="$ROOT/korailbot"

# ---------------------------------------------------------------- 4. .env
say "4/6 설정 파일 (.env)"
env_value() { .venv/bin/python - "$1" <<'PY'
import re, sys
value = ""
try:
    for line in open(".env", encoding="utf-8"):
        m = re.match(r"\s*" + re.escape(sys.argv[1]) + r"\s*=\s*(.*)", line)
        if m:
            value = m.group(1).strip().strip("'\"")
except FileNotFoundError:
    pass
print(value)
PY
}
env_set() {  # env_set KEY  (value on stdin, never on the command line)
  .venv/bin/python - "$1" <<'PY'
import os, re, sys
key, value = sys.argv[1], os.environ["KORAILBOT_ENV_VALUE"]
lines = open(".env", encoding="utf-8").read().splitlines()
pattern = re.compile(r"\s*#?\s*" + re.escape(key) + r"\s*=")
for i, line in enumerate(lines):
    if pattern.match(line):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
open(".env", "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
}
if [ -f .env ]; then
  note "기존 .env 를 그대로 사용합니다."
elif [ -n "$ENV_SOURCE" ]; then
  [ -f "$ENV_SOURCE" ] || die "--env 파일이 없습니다: $ENV_SOURCE"
  cp "$ENV_SOURCE" .env; note "$ENV_SOURCE 를 .env 로 복사했습니다."
else
  cp .env.example .env; note ".env.example 로 .env 를 만들었습니다."
fi
chmod 600 .env
if [ -z "$(env_value CARD_ENCRYPTION_KEY)" ]; then
  KORAILBOT_ENV_VALUE="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')" env_set CARD_ENCRYPTION_KEY
  note "CARD_ENCRYPTION_KEY 를 새로 만들었습니다. (계정·카드 암호화 키, .env 를 잃으면 재등록 필요)"
fi
if [ -z "$(env_value TELEGRAM_BOT_TOKEN)" ]; then
  if [ "$INTERACTIVE" = 1 ]; then
    note "텔레그램 @BotFather 에서 받은 봇 토큰을 붙여 넣으세요. 화면에 표시되지 않습니다. (없으면 Enter)"
    read -r -s -p "  TELEGRAM_BOT_TOKEN: " TOKEN; echo
    if [ -n "$TOKEN" ]; then KORAILBOT_ENV_VALUE="$TOKEN" env_set TELEGRAM_BOT_TOKEN; note "토큰을 .env 에 저장했습니다."; fi
    unset TOKEN
  fi
fi
HAS_TOKEN=0; [ -n "$(env_value TELEGRAM_BOT_TOKEN)" ] && HAS_TOKEN=1
[ "$HAS_TOKEN" = 1 ] || warn "TELEGRAM_BOT_TOKEN 이 비어 있습니다. .env 에 넣은 뒤 이 스크립트를 다시 실행하면 봇 서비스가 등록됩니다."

# ---------------------------------------------------------------- 5. telegram bot service
SERVICE_OK=0
if [ "$DO_SERVICE" = 0 ]; then
  say "5/6 텔레그램 봇 서비스 등록 건너뜀 (--no-service)"
elif [ "$(id -u)" = 0 ]; then
  say "5/6 텔레그램 봇 서비스"
  warn "root 로 실행 중이라 사용자 서비스를 만들지 않습니다. 일반 사용자로 다시 실행하거나 '$BOT telegram' 을 직접 실행하세요."
elif ! systemctl --user show-environment >/dev/null 2>&1; then
  say "5/6 텔레그램 봇 서비스"
  warn "systemd 사용자 세션을 쓸 수 없습니다. '$BOT telegram' 을 직접 실행하거나 docs/INSTALL.md 를 참고하세요."
else
  say "5/6 텔레그램 봇 서비스 등록 ($SERVICE, 사용자 systemd)"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/$SERVICE.service" <<UNIT
[Unit]
Description=Korail KTX bot (Telegram)
After=network-online.target

[Service]
WorkingDirectory=$ROOT
ExecStart=$ROOT/korailbot telegram
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  $SUDO loginctl enable-linger "$USER" >/dev/null 2>&1 || warn "linger 를 켜지 못했습니다: sudo loginctl enable-linger $USER"
  if [ "$HAS_TOKEN" = 1 ]; then
    systemctl --user enable --quiet "$SERVICE"
    systemctl --user restart "$SERVICE"
    sleep 2
    if systemctl --user is-active --quiet "$SERVICE"; then
      SERVICE_OK=1; note "봇 서비스 실행 중. 로그: journalctl --user -u $SERVICE -f"
    else
      warn "봇 서비스가 시작되지 않았습니다: journalctl --user -u $SERVICE -n 30"
    fi
  else
    note "서비스 파일만 만들었습니다. 토큰을 넣은 뒤: systemctl --user enable --now $SERVICE"
  fi
fi

# ---------------------------------------------------------------- 6. account, card, pairing
say "6/6 코레일 계정 · 카드 · 텔레그램 채팅 등록"
if [ "$INTERACTIVE" = 1 ]; then
  if ask "코레일 계정을 지금 등록할까요? (로그인 확인 후 암호화 저장)"; then "$BOT" login || warn "계정 등록 실패. 나중에: korailbot login"; fi
  if ask "자동 결제 카드를 지금 등록할까요?"; then "$BOT" card set || warn "카드 등록 실패. 나중에: korailbot card set"; fi
  if [ "$SERVICE_OK" = 1 ] && ask "텔레그램 채팅 등록 코드를 발급할까요?"; then "$BOT" pair || true; fi
else
  note "건너뜀. 나중에: korailbot login → korailbot card set → korailbot pair"
fi

cat <<DONE

✅ 설치 완료: $ROOT

  다음 순서로 확인하세요.
    korailbot status                                   # Redis, 계정, 카드, 텔레그램 상태
    korailbot search --date YYYYMMDD --from 서울 --to 부산 --time 0900   # 코레일 응답 확인 (로그인 불필요)
    korailbot login / card set / pair                  # 아직 안 했다면
  텔레그램에서 봇에게 /start → '등록 <코드>' → '상태'

  조회에서 -8204 가 나오면 코레일이 이 버전을 거부한 것이고, -2000/-8202 는 IP 차단입니다. (docs/RUNBOOK.md)
DONE

# Test Suite

```
tests/
├── unit/                    # No Redis needed
│   ├── test_validators.py            # Input validation (dates, stations, cards ...)
│   ├── test_korail_service.py        # pykorail error mapping (login/search/reserve)
│   ├── test_card_payment.py          # Card validators, pay_reservation, worker payment helper
│   ├── test_worker.py                # Worker flows with fake storage/notifier/Korail
│   ├── test_kakao_service.py         # Kakao OAuth, memo delivery, local redirect catcher
│   ├── test_notifier.py              # Console + KakaoTalk notifier
│   ├── test_crypto.py                # AES-GCM at-rest encryption, key file fallback
│   └── test_settings_dotenv.py       # .env loader
├── integration/             # Redis-backed
│   ├── test_reservation_service.py   # Worker spawn/cancel/status/paid
│   └── test_cli.py                   # korailbot commands end to end (worker spawn mocked)
└── conftest.py              # Redis container (Docker) or local Redis; temp data dir
```

## Redis

`conftest.py` starts a throwaway Redis container through `testcontainers` when Docker
is available. Without Docker it falls back to a local server at `REDIS_HOST:REDIS_PORT`
(default `localhost:6379`):

```bash
redis-server --port 6379 --daemonize yes
python -m pytest tests -q
```

The integration tests call `FLUSHDB` on that database after every test, so point them
at a dedicated instance or database (`REDIS_DB`).

## Running

```bash
pip install pytest pytest-mock freezegun            # or: pipenv install --dev
python -m pytest tests -q                           # everything
python -m pytest tests/unit -q                      # no Redis needed
python -m pytest tests/integration/test_cli.py -q   # one file
```

Worker logs and the generated encryption key are written to a temporary directory
during the tests (`KORAIL_BOT_DATA_DIR`), never to `~/.korail_bot`.

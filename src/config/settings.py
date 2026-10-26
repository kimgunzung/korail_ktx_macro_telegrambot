"""
Application configuration management.

Centralizes all configuration from environment variables (and an optional ``.env``
file next to the project root) and provides typed access throughout the app.
"""
import os
from typing import Optional


def load_dotenv_file(path: Optional[str] = None) -> int:
    """Load ``KEY=VALUE`` lines from a ``.env`` file into ``os.environ``.

    Existing environment variables win, so values exported by the shell or Docker
    Compose are never overridden. ``path`` defaults to ``ENV_FILE`` or the ``.env``
    next to the project root (one level above ``src/``).

    Returns:
        Number of variables set
    """
    path = path or os.environ.get("ENV_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"
    )
    if not os.path.isfile(path):
        return 0
    loaded = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not key or key in os.environ:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            os.environ[key] = value
            loaded += 1
    return loaded


_DOTENV_LOADED = load_dotenv_file()


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ('true', '1', 'yes')


class Settings:
    """Application settings loaded from environment variables."""

    # This is a single-user, self-hosted tool: all state in Redis is keyed by this id.
    LOCAL_USER_ID: int = 1

    # Local data directory (worker logs, device identity)
    DATA_DIR: str = os.environ.get('KORAIL_BOT_DATA_DIR', os.path.expanduser('~/.korail_bot'))
    LOG_DIR: str = os.environ.get('KORAIL_BOT_LOG_DIR', os.path.join(DATA_DIR, 'logs'))

    # Korail Configuration
    KORAIL_SEARCH_INTERVAL: int = int(os.environ.get('SEARCH_INTERVAL', '3'))  # seconds between searches
    # DynaPath device identity. Pin explicitly or let the service persist a generated one to a file
    # so the CLI and every background worker present the same device to Korail.
    KORAIL_DEVICE_PROFILE_ID: Optional[str] = os.environ.get('KORAIL_DEVICE_PROFILE_ID')
    KORAIL_ANDROID_ID: Optional[str] = os.environ.get('KORAIL_ANDROID_ID')
    KORAIL_DEVICE_FILE: str = os.environ.get('KORAIL_DEVICE_FILE', os.path.expanduser('~/.korail_bot_device.json'))
    KORAIL_STATION_LIST_URL: str = "http://www.letskorail.com/ebizprd/stationKtxList.do"
    KORAIL_PAYMENT_URL: str = "https://www.letskorail.com/ebizprd/EbizPrdTicketpr13500W_pr13510.do?"

    # Automatic card payment right after a reservation succeeds.
    AUTO_PAYMENT_ENABLED: bool = _bool('AUTO_PAYMENT_ENABLED', 'True')
    # Secret used to encrypt saved card details and Korail credentials in Redis (AES-256-GCM).
    # Strongly recommended; without it a key is derived from a fixed string + the Redis host.
    CARD_ENCRYPTION_KEY: Optional[str] = os.environ.get('CARD_ENCRYPTION_KEY') or None

    # Payment Reminder Configuration (KakaoTalk notifications while a reservation is unpaid)
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '10'))
    PAYMENT_REMINDER_INTERVAL_SECONDS: int = int(os.environ.get('PAYMENT_REMINDER_INTERVAL', '120'))

    # KakaoTalk integration ("나에게 보내기" via Kakao REST API). Optional: without it the
    # CLI still works and results are only written to the console/log.
    KAKAO_REST_API_KEY: Optional[str] = os.environ.get('KAKAO_REST_API_KEY') or None
    KAKAO_CLIENT_SECRET: Optional[str] = os.environ.get('KAKAO_CLIENT_SECRET') or None
    # `korailbot kakao link` listens on this local port to catch the OAuth redirect.
    KAKAO_LOCAL_PORT: int = int(os.environ.get('KAKAO_LOCAL_PORT', '8080'))
    KAKAO_REDIRECT_URI: str = os.environ.get('KAKAO_REDIRECT_URI', '') or f"http://localhost:{KAKAO_LOCAL_PORT}/kakao/oauth"
    KAKAO_AUTH_BASE_URL: str = "https://kauth.kakao.com"
    KAKAO_API_BASE_URL: str = "https://kapi.kakao.com"
    KAKAO_MEMO_TEXT_LIMIT: int = 200  # Kakao text template hard limit

    # KakaoTalk chatbot (Kakao i Open Builder skill server, `korailbot serve`).
    KAKAO_SKILL_HOST: str = os.environ.get('KAKAO_SKILL_HOST', '127.0.0.1')
    KAKAO_SKILL_PORT: int = int(os.environ.get('KAKAO_SKILL_PORT', '8090'))
    # Optional secret path segment so random scanners cannot reach the skill endpoint:
    # the skill URL becomes https://<host>/kakao/skill/<secret>
    KAKAO_SKILL_SECRET: str = os.environ.get('KAKAO_SKILL_SECRET', '').strip()
    # Open Builder user ids (botUserKey) allowed to control the bot, besides those paired
    # with `korailbot kakao pair`. Comma separated.
    KAKAO_SKILL_ALLOWED_USERS: list[str] = [
        u.strip() for u in os.environ.get('KAKAO_SKILL_ALLOWED_USERS', '').split(',') if u.strip()
    ]

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Process Management
    RECURSION_LIMIT: int = 10**7

    # Redis Configuration
    REDIS_HOST: str = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.environ.get('REDIS_PORT', '6379'))
    REDIS_DB: int = int(os.environ.get('REDIS_DB', '0'))
    REDIS_PASSWORD: Optional[str] = os.environ.get('REDIS_PASSWORD')
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 50

    def validate(self) -> None:
        """Validate settings; raises ValueError with a user-facing message."""
        if self.KORAIL_SEARCH_INTERVAL < 1:
            raise ValueError("SEARCH_INTERVAL must be at least 1 second")
        if self.PAYMENT_TIMEOUT_MINUTES < 1:
            raise ValueError("PAYMENT_TIMEOUT_MINUTES must be at least 1")

    def kakao_enabled(self) -> bool:
        """True when the KakaoTalk integration is configured."""
        return bool(self.KAKAO_REST_API_KEY and self.KAKAO_REDIRECT_URI)

    def kakao_skill_path(self) -> str:
        """URL path of the Open Builder skill endpoint."""
        return f"/kakao/skill/{self.KAKAO_SKILL_SECRET}" if self.KAKAO_SKILL_SECRET else "/kakao/skill"


# Singleton instance
settings = Settings()

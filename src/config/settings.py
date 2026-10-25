"""
Application configuration management.

This module centralizes all configuration variables from environment
and provides type-safe access to settings throughout the application.
"""
import os
from typing import Optional


class Settings:
    """Application settings loaded from environment variables."""

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.environ.get('BOTTOKEN', '')
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org/bot{token}"

    # Korail Configuration
    KORAIL_ADMIN_USER_ID: Optional[str] = os.environ.get('USERID')
    KORAIL_ADMIN_PASSWORD: Optional[str] = os.environ.get('USERPW')
    KORAIL_SEARCH_INTERVAL: int = int(os.environ.get('SEARCH_INTERVAL', '3'))  # seconds between searches
    # DynaPath device identity. Pin explicitly or let the service persist a generated one to a file
    # so the web app and every background process present the same device to Korail.
    KORAIL_DEVICE_PROFILE_ID: Optional[str] = os.environ.get('KORAIL_DEVICE_PROFILE_ID')
    KORAIL_ANDROID_ID: Optional[str] = os.environ.get('KORAIL_ANDROID_ID')
    KORAIL_DEVICE_FILE: str = os.environ.get('KORAIL_DEVICE_FILE', os.path.expanduser('~/.korail_bot_device.json'))
    KORAIL_STATION_LIST_URL: str = "http://www.letskorail.com/ebizprd/stationKtxList.do"
    KORAIL_PAYMENT_URL: str = "https://www.letskorail.com/ebizprd/EbizPrdTicketpr13500W_pr13510.do?"

    # User Access Control
    ALLOW_LIST: list[str] = os.environ.get('ALLOW_LIST', '').split(',') if os.environ.get('ALLOW_LIST') else []

    # Payment Reminder Configuration
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '10'))
    PAYMENT_REMINDER_INTERVAL_SECONDS: int = int(os.environ.get('PAYMENT_REMINDER_INTERVAL', '10'))

    # Flask Configuration
    FLASK_HOST: str = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT: int = int(os.environ.get('FLASK_PORT', '8080'))
    # Debug defaults to off: Flask's debug mode exposes the Werkzeug debugger (remote code
    # execution) on FLASK_HOST and the reloader would start every service twice.
    FLASK_DEBUG: bool = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')

    # Application Callback URLs (internal)
    CALLBACK_BASE_URL: str = f"http://127.0.0.1:{FLASK_PORT}"

    # Public base URL of this server (used for OAuth redirects, e.g. KakaoTalk linking).
    PUBLIC_BASE_URL: str = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

    # KakaoTalk integration ("나에게 보내기" via Kakao REST API). Optional: the bot works
    # without it, and /kakao tells users the feature is not configured.
    KAKAO_REST_API_KEY: Optional[str] = os.environ.get('KAKAO_REST_API_KEY') or None
    KAKAO_CLIENT_SECRET: Optional[str] = os.environ.get('KAKAO_CLIENT_SECRET') or None
    KAKAO_REDIRECT_URI: str = os.environ.get('KAKAO_REDIRECT_URI', '') or (
        f"{PUBLIC_BASE_URL}/kakao/oauth" if PUBLIC_BASE_URL else ''
    )
    KAKAO_AUTH_BASE_URL: str = "https://kauth.kakao.com"
    KAKAO_API_BASE_URL: str = "https://kapi.kakao.com"
    KAKAO_MEMO_TEXT_LIMIT: int = 200  # Kakao text template hard limit

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Magic Admin Login String
    ADMIN_MAGIC_STRING: str = "근삼이최고"

    # Admin Command Authentication
    # Uses USERPW environment variable (same as Korail admin password)
    ADMIN_PASSWORD: Optional[str] = os.environ.get('USERPW')

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
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_MAX_CONNECTIONS: int = 50

    @classmethod
    def validate(cls) -> None:
        """Validate required settings."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("BOTTOKEN environment variable is required")
        if cls.KAKAO_REST_API_KEY and not cls.KAKAO_REDIRECT_URI:
            raise ValueError(
                "KAKAO_REST_API_KEY is set but no redirect URI is known: "
                "set KAKAO_REDIRECT_URI or PUBLIC_BASE_URL"
            )

    def kakao_enabled(self) -> bool:
        """True when the KakaoTalk integration is configured."""
        return bool(self.KAKAO_REST_API_KEY and self.KAKAO_REDIRECT_URI)

    @classmethod
    def is_user_allowed(cls, phone_number: str) -> bool:
        """Check if user phone number is in allow list."""
        if not cls.ALLOW_LIST or cls.ALLOW_LIST == ['']:
            return True  # No restriction if ALLOW_LIST is empty
        return phone_number in cls.ALLOW_LIST


# Singleton instance
settings = Settings()

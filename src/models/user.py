"""User data models."""
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.reservation import TrainSearchParams


@dataclass
class UserCredentials:
    """Korail login credentials."""
    korail_id: str  # Phone number format: 010-xxxx-xxxx
    korail_pw: str


@dataclass
class UserSession:
    """User session data for conversation flow."""
    chat_id: int
    in_progress: bool = False
    last_action: int = 0  # Progress state (0-12)
    credentials: Optional[UserCredentials] = None
    train_info: dict = field(default_factory=dict)
    process_id: int = 9999999  # PID of background reservation process
    search_params: Optional['TrainSearchParams'] = None  # Search parameters for train reservation

    def reset(self) -> None:
        """Reset user session to initial state."""
        self.in_progress = False
        self.last_action = 0
        self.train_info = {}
        self.process_id = 9999999
        self.search_params = None


@dataclass
class UserProgress:
    """Represents user's progress in the reservation flow."""

    # Progress state constants
    INIT = 0
    STARTED = 1
    START_ACCEPTED = 2
    ID_INPUT_SUCCESS = 3
    PW_INPUT_SUCCESS = 4
    DATE_INPUT_SUCCESS = 5
    SRC_LOCATE_INPUT_SUCCESS = 6
    DST_LOCATE_INPUT_SUCCESS = 7
    DEP_TIME_INPUT_SUCCESS = 8
    MAX_DEP_TIME_INPUT_SUCCESS = 9
    TRAIN_TYPE_INPUT_SUCCESS = 10
    SPECIAL_INPUT_SUCCESS = 11
    PASSENGER_COUNT_INPUT_SUCCESS = 12  # New: passenger count selection
    SEAT_STRATEGY_INPUT_SUCCESS = 13  # Final confirmation shown, waiting for Y/N
    FINDING_TICKET = 14  # Background search running

    # Optional automatic card payment (asked between seat strategy and final confirmation).
    # Numbered after FINDING_TICKET so the long-lived state keeps its value.
    CARD_CHOICE_PENDING = 15      # "자동 결제를 사용할까요?" Y/N
    CARD_NUMBER_PENDING = 16
    CARD_EXPIRE_PENDING = 17
    CARD_PASSWORD_PENDING = 18
    CARD_VERIFY_PENDING = 19


@dataclass
class CardInfo:
    """Credit card used for automatic payment right after a reservation succeeds.

    Lives only for the duration of the conversation (short-TTL Redis key) and in
    the memory of the background process; never written to logs.
    """
    number: str          # digits only
    expire: str          # YYMM
    password: str        # first two digits of the card password
    verify_number: str   # YYMMDD birth date (personal) or 10-digit business number (corporate)
    installment: int = 0
    saved_at: float = 0.0  # unix time when the card was saved (0 while being entered)

    @property
    def is_corporate(self) -> bool:
        return len(self.verify_number) == 10

    @property
    def is_complete(self) -> bool:
        return bool(self.number and self.expire and self.password and self.verify_number)

    @property
    def expire_display(self) -> str:
        """``YYMM`` -> ``MM/YY``."""
        return f"{self.expire[2:]}/{self.expire[:2]}" if len(self.expire) == 4 else self.expire

    @property
    def masked(self) -> str:
        """Display form, e.g. ``****1234``."""
        return f"****{self.number[-4:]}" if len(self.number) >= 4 else "****"

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "expire": self.expire,
            "password": self.password,
            "verify_number": self.verify_number,
            "installment": self.installment,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CardInfo":
        return cls(
            number=str(data["number"]),
            expire=str(data["expire"]),
            password=str(data["password"]),
            verify_number=str(data["verify_number"]),
            installment=int(data.get("installment", 0)),
            saved_at=float(data.get("saved_at", 0) or 0),
        )

    def __repr__(self) -> str:
        return f"CardInfo(number={self.masked!r}, installment={self.installment})"


@dataclass
class KakaoTokens:
    """OAuth tokens of the Kakao account linked to a Telegram chat."""
    chat_id: int
    access_token: str
    refresh_token: str
    expires_at: float            # unix time when the access token expires
    refresh_expires_at: float    # unix time when the refresh token expires
    linked_at: float = field(default_factory=time.time)

    @classmethod
    def from_token_response(cls, chat_id: int, payload: dict) -> "KakaoTokens":
        """Build from Kakao's /oauth/token JSON response."""
        now = time.time()
        return cls(
            chat_id=chat_id,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            expires_at=now + float(payload.get("expires_in", 0)),
            refresh_expires_at=now + float(payload.get("refresh_token_expires_in", 0)),
            linked_at=now,
        )

    def access_token_expires_within(self, seconds: float) -> bool:
        return time.time() + seconds >= self.expires_at

    def refresh_token_expired(self) -> bool:
        return not self.refresh_token or time.time() >= self.refresh_expires_at

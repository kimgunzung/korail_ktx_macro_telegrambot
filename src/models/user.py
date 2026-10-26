"""User-related data models (credentials, KakaoTalk link, payment card)."""
import time
from dataclasses import dataclass, field


@dataclass
class KorailCredentials:
    """Korail login credentials (stored encrypted, see storage)."""
    korail_id: str  # phone number 010-xxxx-xxxx, e-mail or membership number
    korail_pw: str

    def to_dict(self) -> dict:
        return {"korail_id": self.korail_id, "korail_pw": self.korail_pw}

    @classmethod
    def from_dict(cls, data: dict) -> "KorailCredentials":
        return cls(korail_id=str(data["korail_id"]), korail_pw=str(data["korail_pw"]))

    def __repr__(self) -> str:
        return f"KorailCredentials(korail_id={self.korail_id!r})"


@dataclass
class KakaoTokens:
    """OAuth tokens of the linked Kakao account."""
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


@dataclass
class CardInfo:
    """Credit card used for automatic payment right after a reservation succeeds.

    Stored encrypted in Redis and handed to the worker through its environment;
    never written to logs (``__repr__`` masks it).
    """
    number: str          # digits only
    expire: str          # YYMM
    password: str        # first two digits of the card password
    verify_number: str   # YYMMDD birth date (personal) or 10-digit business number (corporate)
    installment: int = 0
    saved_at: float = 0.0  # unix time when the card was saved

    @property
    def is_corporate(self) -> bool:
        return len(self.verify_number) == 10

    @property
    def is_complete(self) -> bool:
        return bool(self.number and self.expire and self.password and self.verify_number)

    @property
    def masked(self) -> str:
        """Display form, e.g. ``****1234``."""
        return f"****{self.number[-4:]}" if len(self.number) >= 4 else "****"

    @property
    def expire_display(self) -> str:
        """``YYMM`` -> ``MM/YY``."""
        return f"{self.expire[2:]}/{self.expire[:2]}" if len(self.expire) == 4 else self.expire

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

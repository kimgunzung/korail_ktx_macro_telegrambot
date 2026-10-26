"""Base storage interface for application state management."""
from abc import ABC, abstractmethod
from typing import Optional, List

from models import (
    RunningReservation, PaymentStatus, MultiReservationStatus, ReservationResult,
    KakaoTokens, CardInfo, KorailCredentials,
)


class StorageInterface(ABC):
    """Abstract interface for application state storage.

    All methods take the local user id (``settings.LOCAL_USER_ID``); the tool is
    single-user but the key layout keeps room for more.
    """

    # Korail credentials (encrypted at rest)
    @abstractmethod
    def get_credentials(self, user_id: int) -> Optional[KorailCredentials]: ...

    @abstractmethod
    def save_credentials(self, user_id: int, credentials: KorailCredentials) -> None: ...

    @abstractmethod
    def delete_credentials(self, user_id: int) -> None: ...

    # Running Reservation Management
    @abstractmethod
    def get_running_reservation(self, user_id: int) -> Optional[RunningReservation]: ...

    @abstractmethod
    def save_running_reservation(self, reservation: RunningReservation) -> None: ...

    @abstractmethod
    def delete_running_reservation(self, user_id: int) -> None: ...

    @abstractmethod
    def get_all_running_reservations(self) -> List[RunningReservation]: ...

    # Last worker result
    @abstractmethod
    def get_last_result(self, user_id: int) -> Optional[ReservationResult]: ...

    @abstractmethod
    def save_last_result(self, user_id: int, result: ReservationResult) -> None: ...

    # Payment Status Management
    @abstractmethod
    def get_payment_status(self, user_id: int) -> Optional[PaymentStatus]: ...

    @abstractmethod
    def save_payment_status(self, status: PaymentStatus) -> None: ...

    @abstractmethod
    def delete_payment_status(self, user_id: int) -> None: ...

    # Multi-Reservation Status Management (random seating)
    @abstractmethod
    def get_multi_reservation_status(self, user_id: int) -> Optional[MultiReservationStatus]: ...

    @abstractmethod
    def save_multi_reservation_status(self, status: MultiReservationStatus) -> None: ...

    @abstractmethod
    def delete_multi_reservation_status(self, user_id: int) -> None: ...

    # KakaoTalk Link Management
    @abstractmethod
    def get_kakao_tokens(self, user_id: int) -> Optional[KakaoTokens]: ...

    @abstractmethod
    def save_kakao_tokens(self, tokens: KakaoTokens) -> None: ...

    @abstractmethod
    def delete_kakao_tokens(self, user_id: int) -> None: ...

    # KakaoTalk chatbot: paired Open Builder users, pairing code, wizard state
    @abstractmethod
    def get_paired_users(self) -> List[str]: ...

    @abstractmethod
    def add_paired_user(self, bot_user_key: str) -> None: ...

    @abstractmethod
    def clear_paired_users(self) -> int: ...

    @abstractmethod
    def get_pairing_code(self) -> Optional[str]: ...

    @abstractmethod
    def set_pairing_code(self, code: Optional[str], ttl_seconds: int = 600) -> None: ...

    @abstractmethod
    def get_chat_state(self, bot_user_key: str) -> Optional[dict]: ...

    @abstractmethod
    def save_chat_state(self, bot_user_key: str, state: Optional[dict], ttl_seconds: int = 1800) -> None: ...

    # Saved Card (automatic payment) - encrypted at rest
    @abstractmethod
    def get_saved_card(self, user_id: int) -> Optional[CardInfo]: ...

    @abstractmethod
    def save_saved_card(self, user_id: int, card: CardInfo) -> None: ...

    @abstractmethod
    def delete_saved_card(self, user_id: int) -> None: ...

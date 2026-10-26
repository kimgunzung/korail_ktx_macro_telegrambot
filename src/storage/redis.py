"""Redis storage implementation."""
import json
import time
from datetime import datetime
from typing import Optional, List
import redis

from models import (
    RunningReservation, PaymentStatus, MultiReservationStatus, ReservationResult,
    TrainSearchParams, KakaoTokens, CardInfo, KorailCredentials,
    SingleReservationInfo, ReservationPaymentStatus,
)
from storage.base import StorageInterface
from config.settings import settings
from utils.crypto import encrypt_json, decrypt_json, DecryptionError
from utils.logger import get_logger

logger = get_logger(__name__)


class RedisStorage(StorageInterface):
    """
    Redis-based storage implementation.

    Provides persistent, process-shared state management using Redis so the CLI
    and the background worker see the same state.
    """

    CARD_INFO_TTL_SECONDS = 60 * 60

    def __init__(self):
        """Initialize Redis connection pool."""
        try:
            self.redis = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=settings.REDIS_DECODE_RESPONSES,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                max_connections=settings.REDIS_MAX_CONNECTIONS
            )
            # Test connection
            self.redis.ping()
            logger.debug(f"Redis connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except redis.RedisError as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    # ==================== Encrypted helpers ====================

    def _read_encrypted(self, key: str) -> Optional[dict]:
        data = self.redis.get(key)
        if not data:
            return None
        try:
            payload = decrypt_json(data)
            return payload if isinstance(payload, dict) else None
        except DecryptionError as e:
            logger.error(f"Cannot decrypt {key} (CARD_ENCRYPTION_KEY changed?): {e}")
            return None

    # ==================== Korail credentials ====================

    def get_credentials(self, user_id: int) -> Optional[KorailCredentials]:
        data = self._read_encrypted(f"credentials:{user_id}")
        if not data:
            return None
        try:
            return KorailCredentials.from_dict(data)
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to deserialize credentials: {e}")
            return None

    def save_credentials(self, user_id: int, credentials: KorailCredentials) -> None:
        self.redis.set(f"credentials:{user_id}", encrypt_json(credentials.to_dict()))

    def delete_credentials(self, user_id: int) -> None:
        self.redis.delete(f"credentials:{user_id}")

    # ==================== Running Reservation Management ====================

    def get_running_reservation(self, user_id: int) -> Optional[RunningReservation]:
        data = self.redis.get(f"running_reservation:{user_id}")
        if not data:
            return None
        try:
            return self._deserialize_running_reservation(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize running reservation: {e}")
            return None

    def save_running_reservation(self, reservation: RunningReservation) -> None:
        self.redis.set(
            f"running_reservation:{reservation.chat_id}",
            json.dumps(self._serialize_running_reservation(reservation)),
        )

    def delete_running_reservation(self, user_id: int) -> None:
        self.redis.delete(f"running_reservation:{user_id}")

    def get_all_running_reservations(self) -> List[RunningReservation]:
        reservations = []
        for key in self.redis.keys("running_reservation:*"):
            data = self.redis.get(key)
            if data:
                try:
                    reservations.append(self._deserialize_running_reservation(json.loads(data)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return reservations

    # ==================== Last result ====================

    def get_last_result(self, user_id: int) -> Optional[ReservationResult]:
        data = self.redis.get(f"last_result:{user_id}")
        if not data:
            return None
        try:
            return ReservationResult.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to deserialize last result: {e}")
            return None

    def save_last_result(self, user_id: int, result: ReservationResult) -> None:
        self.redis.set(f"last_result:{user_id}", json.dumps(result.to_dict(), ensure_ascii=False))

    # ==================== Payment Status Management ====================

    def get_payment_status(self, user_id: int) -> Optional[PaymentStatus]:
        data = self.redis.get(f"payment_status:{user_id}")
        if not data:
            return None
        try:
            d = json.loads(data)
            return PaymentStatus(
                chat_id=d["chat_id"],
                reminder_active=d["reminder_active"],
                completed=d["completed"],
                reservation_time=datetime.fromisoformat(d["reservation_time"]) if d.get("reservation_time") else None,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize payment status: {e}")
            return None

    def save_payment_status(self, status: PaymentStatus) -> None:
        data = json.dumps({
            "chat_id": status.chat_id,
            "reminder_active": status.reminder_active,
            "completed": status.completed,
            "reservation_time": status.reservation_time.isoformat() if status.reservation_time else None,
        })
        # Set with TTL (payment timeout + buffer)
        ttl = (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60
        self.redis.setex(f"payment_status:{status.chat_id}", ttl, data)

    def delete_payment_status(self, user_id: int) -> None:
        self.redis.delete(f"payment_status:{user_id}")

    # ==================== Multi-Reservation Status Management ====================

    def get_multi_reservation_status(self, user_id: int) -> Optional[MultiReservationStatus]:
        data = self.redis.get(f"multi_reservation_status:{user_id}")
        if not data:
            return None
        try:
            return self._deserialize_multi_reservation_status(json.loads(data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize multi reservation status: {e}")
            return None

    def save_multi_reservation_status(self, status: MultiReservationStatus) -> None:
        ttl = (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60
        self.redis.setex(
            f"multi_reservation_status:{status.chat_id}", ttl,
            json.dumps(self._serialize_multi_reservation_status(status), ensure_ascii=False),
        )

    def delete_multi_reservation_status(self, user_id: int) -> None:
        """Delete multi-reservation status and related per-seat keys."""
        self.redis.delete(f"multi_reservation_status:{user_id}")
        self.redis.delete(f"current_seat_index:{user_id}")
        payment_keys = self.redis.keys(f"payment_ready:{user_id}:*")
        if payment_keys:
            self.redis.delete(*payment_keys)

    # ==================== Partial Reservation Management (Random Seating) ====================

    def save_partial_reservation(self, user_id: int, seat_index: int, reservation_data: dict) -> None:
        key = f"partial_reservations:{user_id}"
        existing = self.redis.get(key)
        reservations = json.loads(existing) if existing else []
        while len(reservations) <= seat_index:
            reservations.append(None)
        reservations[seat_index] = reservation_data
        self.redis.setex(key, 7200, json.dumps(reservations, ensure_ascii=False))

    def get_partial_reservations(self, user_id: int) -> List[dict]:
        data = self.redis.get(f"partial_reservations:{user_id}")
        if not data:
            return []
        try:
            return [r for r in json.loads(data) if r is not None]
        except json.JSONDecodeError:
            return []

    def delete_partial_reservations(self, user_id: int) -> None:
        self.redis.delete(f"partial_reservations:{user_id}")

    def get_current_seat_index(self, user_id: int) -> Optional[int]:
        value = self.redis.get(f"current_seat_index:{user_id}")
        return int(value) if value is not None else None

    def set_current_seat_index(self, user_id: int, index: Optional[int]) -> None:
        key = f"current_seat_index:{user_id}"
        if index is not None:
            self.redis.setex(key, 7200, str(index))
        else:
            self.redis.delete(key)

    def is_payment_ready(self, user_id: int, seat_index: int) -> bool:
        return bool(self.redis.get(f"payment_ready:{user_id}:{seat_index}"))

    def mark_payment_ready(self, user_id: int, seat_index: int) -> None:
        self.redis.setex(f"payment_ready:{user_id}:{seat_index}", 60, "1")

    def wait_for_payment(self, user_id: int, seat_index: int, timeout: int = 600) -> bool:
        """Poll until ``korailbot paid`` raises the flag for this seat, or ``timeout`` seconds pass."""
        key = f"payment_ready:{user_id}:{seat_index}"
        start_time = time.time()
        logger.info(f"Waiting for payment confirmation (seat {seat_index}, timeout={timeout}s)...")
        while time.time() - start_time < timeout:
            if self.redis.get(key):
                self.redis.delete(key)
                logger.info(f"Payment confirmed after {int(time.time() - start_time)}s")
                return True
            time.sleep(1)
        logger.warning(f"Payment timeout after {timeout}s")
        return False

    # ==================== KakaoTalk Link Management ====================

    def get_kakao_tokens(self, user_id: int) -> Optional[KakaoTokens]:
        data = self.redis.get(f"kakao_tokens:{user_id}")
        if not data:
            return None
        try:
            d = json.loads(data)
            return KakaoTokens(
                chat_id=d["chat_id"],
                access_token=d["access_token"],
                refresh_token=d.get("refresh_token", ""),
                expires_at=float(d.get("expires_at", 0)),
                refresh_expires_at=float(d.get("refresh_expires_at", 0)),
                linked_at=float(d.get("linked_at", 0)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.error(f"Failed to deserialize Kakao tokens: {e}")
            return None

    def save_kakao_tokens(self, tokens: KakaoTokens) -> None:
        key = f"kakao_tokens:{tokens.chat_id}"
        data = json.dumps({
            "chat_id": tokens.chat_id,
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
            "refresh_expires_at": tokens.refresh_expires_at,
            "linked_at": tokens.linked_at,
        })
        ttl = int(tokens.refresh_expires_at - time.time())
        if ttl > 0:
            self.redis.setex(key, ttl, data)
        else:
            self.redis.set(key, data)

    def delete_kakao_tokens(self, user_id: int) -> None:
        self.redis.delete(f"kakao_tokens:{user_id}")

    # ==================== Saved card (automatic payment) ====================

    def get_saved_card(self, user_id: int) -> Optional[CardInfo]:
        data = self._read_encrypted(f"saved_card:{user_id}")
        if not data:
            return None
        try:
            return CardInfo.from_dict(data)
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Failed to deserialize saved card: {e}")
            return None

    def save_saved_card(self, user_id: int, card: CardInfo) -> None:
        self.redis.set(f"saved_card:{user_id}", encrypt_json(card.to_dict()))
        logger.info(f"Saved payment card {card.masked}")

    def delete_saved_card(self, user_id: int) -> None:
        self.redis.delete(f"saved_card:{user_id}")

    # ==================== Serialization Helpers ====================

    @staticmethod
    def _serialize_params(p: TrainSearchParams) -> dict:
        return {
            "dep_date": p.dep_date,
            "src_locate": p.src_locate,
            "dst_locate": p.dst_locate,
            "dep_time": p.dep_time,
            "max_dep_time": p.max_dep_time,
            "train_type": p.train_type,
            "train_type_display": p.train_type_display,
            "special_option": p.special_option,
            "special_option_display": p.special_option_display,
            "passenger_count": p.passenger_count,
            "seat_strategy": p.seat_strategy,
        }

    @staticmethod
    def _deserialize_params(p: dict) -> TrainSearchParams:
        return TrainSearchParams(
            dep_date=p["dep_date"],
            src_locate=p["src_locate"],
            dst_locate=p["dst_locate"],
            dep_time=p["dep_time"],
            max_dep_time=p.get("max_dep_time", "2400"),
            train_type=p.get("train_type", "TrainType.KTX"),
            train_type_display=p.get("train_type_display", "KTX"),
            special_option=p.get("special_option", "ReserveOption.GENERAL_FIRST"),
            special_option_display=p.get("special_option_display", "GENERAL_FIRST"),
            passenger_count=p.get("passenger_count", 1),
            seat_strategy=p.get("seat_strategy", "consecutive"),
        )

    def _serialize_running_reservation(self, reservation: RunningReservation) -> dict:
        return {
            "chat_id": reservation.chat_id,
            "process_id": reservation.process_id,
            "korail_id": reservation.korail_id,
            "started_at": reservation.started_at.isoformat() if reservation.started_at else None,
            "search_params": self._serialize_params(reservation.search_params),
        }

    def _deserialize_running_reservation(self, data: dict) -> RunningReservation:
        reservation = RunningReservation(
            chat_id=data["chat_id"],
            process_id=data["process_id"],
            korail_id=data["korail_id"],
            search_params=self._deserialize_params(data["search_params"]),
        )
        if data.get("started_at"):
            reservation.started_at = datetime.fromisoformat(data["started_at"])
        return reservation

    def _serialize_multi_reservation_status(self, status: MultiReservationStatus) -> dict:
        return {
            "chat_id": status.chat_id,
            "reservations": [
                {
                    "reservation_id": r.reservation_id,
                    "reserved_at": r.reserved_at.isoformat(),
                    "expires_at": r.expires_at.isoformat(),
                    "status": r.status.value if hasattr(r.status, 'value') else r.status,
                    "seat_number": r.seat_number,
                    "train_info": r.train_info,
                } for r in status.reservations
            ],
            "total_seats": status.total_seats,
            "seat_strategy": status.seat_strategy,
            "created_at": status.created_at.isoformat(),
            "manually_stopped": status.manually_stopped,
        }

    def _deserialize_multi_reservation_status(self, data: dict) -> MultiReservationStatus:
        reservations = [
            SingleReservationInfo(
                reservation_id=r["reservation_id"],
                reservation_obj=None,
                reserved_at=datetime.fromisoformat(r["reserved_at"]),
                expires_at=datetime.fromisoformat(r["expires_at"]),
                status=ReservationPaymentStatus(r["status"]) if isinstance(r["status"], str) else r["status"],
                seat_number=r["seat_number"],
                train_info=r["train_info"],
            ) for r in data["reservations"]
        ]
        return MultiReservationStatus(
            chat_id=data["chat_id"],
            reservations=reservations,
            total_seats=data["total_seats"],
            seat_strategy=data["seat_strategy"],
            created_at=datetime.fromisoformat(data["created_at"]),
            manually_stopped=data["manually_stopped"],
        )

    # ==================== Admin Operations ====================

    def flush_all(self) -> int:
        """Delete every key in this Redis database (used by ``korailbot reset --all``)."""
        key_count = self.redis.dbsize()
        self.redis.flushdb()
        logger.warning(f"Redis database flushed. {key_count} keys deleted.")
        return key_count

"""Plain-text rendering of pykorail trains and reservations (CLI and chat share it)."""
from typing import Iterable, List, Optional


def _hhmm(value: Optional[str]) -> str:
    value = value or ""
    return f"{value[:2]}:{value[2:4]}" if len(value) >= 4 and value[:4].isdigit() else (value or "?")


def _ymd(value: Optional[str]) -> str:
    value = value or ""
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) >= 8 and value.isdigit() else (value or "?")


def _seat_state(train) -> str:
    general = "일반실 ○" if train.has_general_seat() else "일반실 매진"
    special = "특실 ○" if train.has_special_seat() else "특실 매진"
    parts = [general, special]
    if getattr(train, "wait_reserve_flag", -1) >= 0:
        parts.append("예약대기 ○" if train.has_general_waiting_list() else "예약대기 매진")
    return " / ".join(parts)


def train_lines(trains: Iterable, header: Optional[str] = None) -> List[str]:
    """One line per train: ``KTX 101  09:00→11:30  서울→부산  일반실 ○ / 특실 매진``."""
    trains = list(trains)
    lines: List[str] = []
    if header:
        lines.append(header)
    if not trains:
        lines.append("조건에 맞는 열차가 없습니다.")
        return lines
    available = 0
    for train in trains:
        name = f"{getattr(train, 'train_type_name', '') or '열차'} {getattr(train, 'train_no', '') or ''}".strip()
        mark = "🟢" if train.has_seat() else ("🟡" if train.has_waiting_list() else "🔴")
        lines.append(
            f"{mark} {name}  {_hhmm(train.dep_time)}→{_hhmm(train.arr_time)}  "
            f"{train.dep_name}→{train.arr_name}  {_seat_state(train)}"
        )
        if train.has_seat():
            available += 1
    lines.append(f"총 {len(trains)}편, 좌석 있음 {available}편 (🟢 좌석 있음 🟡 예약대기 🔴 매진)")
    return lines


def reservation_lines(reservations: Iterable, header: Optional[str] = None) -> List[str]:
    """One block per unpaid reservation held at Korail."""
    reservations = list(reservations)
    lines: List[str] = []
    if header:
        lines.append(header)
    if not reservations:
        lines.append("코레일에 결제 대기 중인 예약이 없습니다.")
        return lines
    for index, r in enumerate(reservations, 1):
        train = r.train
        name = f"{getattr(train, 'train_type_name', '') or '열차'} {getattr(train, 'train_no', '') or ''}".strip()
        lines.append(
            f"{index}. [{r.rsv_id}] {name}  {_ymd(train.run_date)} {_hhmm(train.dep_time)}→{_hhmm(train.arr_time)}  "
            f"{train.dep_name}→{train.arr_name}"
        )
        seats = ", ".join(f"{s.car}호차 {s.seat}" for s in (r.seats or ())) or f"{r.seat_no_count}석"
        if getattr(r, "is_waiting", False):
            lines.append(f"   예약대기 ({seats})")
        else:
            lines.append(
                f"   {seats} · {r.price:,}원 · 결제기한 {_ymd(r.buy_limit_date)} {_hhmm(r.buy_limit_time)}"
            )
    return lines

"""Unit tests for the shared train / reservation text rendering."""
from types import SimpleNamespace

from services.train_view import reservation_lines, train_lines


def _train(dep="090000", arr="113000", general=True, special=False, waiting=-1, no="101", name="KTX"):
    return SimpleNamespace(
        train_type_name=name, train_no=no, dep_time=dep, arr_time=arr, dep_name="서울", arr_name="부산",
        run_date="20261225", wait_reserve_flag=waiting,
        has_general_seat=lambda: general, has_special_seat=lambda: special,
        has_seat=lambda: general or special, has_general_waiting_list=lambda: waiting == 9,
        has_waiting_list=lambda: waiting == 9,
    )


class TestTrainLines:
    def test_renders_seat_state_and_summary(self):
        lines = train_lines([_train(), _train(dep="100000", general=False, waiting=9, no="103"),
                             _train(dep="110000", general=False, waiting=0, no="105")], header="H")
        assert lines[0] == "H"
        assert lines[1].startswith("🟢 KTX 101  09:00→11:30  서울→부산  일반실 ○ / 특실 매진")
        assert "예약대기" not in lines[1]  # flag < 0: waiting list not applicable
        assert lines[2].startswith("🟡 KTX 103") and "예약대기 ○" in lines[2]
        assert lines[3].startswith("🔴 KTX 105") and "일반실 매진" in lines[3] and "예약대기 매진" in lines[3]
        assert lines[-1].startswith("총 3편, 좌석 있음 1편")

    def test_empty(self):
        assert train_lines([]) == ["조건에 맞는 열차가 없습니다."]


class TestReservationLines:
    def _reservation(self, waiting=False, seats=True):
        return SimpleNamespace(
            rsv_id="12345678", train=_train(), price=59800, seat_no_count=2,
            buy_limit_date="20261220", buy_limit_time="233000", is_waiting=waiting,
            seats=(SimpleNamespace(car="3", seat="7A"), SimpleNamespace(car="3", seat="7B")) if seats else (),
        )

    def test_renders_seats_price_and_deadline(self):
        lines = reservation_lines([self._reservation()], header="H")
        assert lines[0] == "H"
        assert lines[1].startswith("1. [12345678] KTX 101  2026-12-25 09:00→11:30  서울→부산")
        assert lines[2] == "   3호차 7A, 3호차 7B · 59,800원 · 결제기한 2026-12-20 23:30"

    def test_waiting_and_seat_count_fallback(self):
        lines = reservation_lines([self._reservation(waiting=True, seats=False)])
        assert lines[1] == "   예약대기 (2석)"

    def test_empty(self):
        assert reservation_lines([]) == ["코레일에 결제 대기 중인 예약이 없습니다."]

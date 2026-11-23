"""Unit tests for KorailService error mapping on top of pykorail (no network)."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from pykorail.exceptions import (
    AccessRestrictedError,
    HttpStatusError,
    KorailError,
    LoginFailedError,
    NeedToLoginError,
    NoResultsError,
    PasswordChangeRequiredError,
    PastDepartureError,
    StationNotFoundError,
    SoldOutError,
)

from services.korail_service import KorailAccessRestrictedError, KorailService, TrainType


def _train(dep_time: str):
    train = MagicMock()
    train.dep_time = dep_time
    train.__str__ = lambda self: f"train@{dep_time}"
    return train


@pytest.fixture
def service(monkeypatch):
    """KorailService wired to a fake pykorail client."""
    svc = KorailService()
    fake = MagicMock()
    svc._korail_instance = fake
    svc._logged_in = True
    svc._username, svc._password = "010-0000-0000", "pw"
    return svc, fake


class TestLogin:
    def test_login_rejected_session_reports_server_notice(self, monkeypatch):
        """SUCC login + pending notice (e.g. WRC000420 password age) -> session unusable -> login False."""
        svc = KorailService()
        fake = MagicMock()
        fake.reservations.all.side_effect = NeedToLoginError(code="P058")
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        svc._last_login_payload = {
            "strResult": "SUCC",
            "h_msg_cd": "WRC000420",
            "h_msg_txt": "6개월 이상 동일한 비밀번호를 사용하고 있습니다.",
            "strRedirectUrl": "/korailtalk-webview/LS/LS0104PG.jsp",
        }
        monkeypatch.setattr(svc, "_login_notice", lambda: "6개월 이상 동일한 비밀번호를 사용하고 있습니다. (WRC000420)")
        assert svc.login("010-0000-0000", "pw") is False
        assert not svc.is_logged_in
        assert "WRC000420" in svc.last_login_error and "코레일+ 앱" in svc.last_login_error

    def test_password_change_notice_is_explained(self, monkeypatch):
        """pykorail >= 0.3 raises PasswordChangeRequiredError itself (WRC000420)."""
        svc = KorailService()
        fake = MagicMock()
        fake.login.side_effect = PasswordChangeRequiredError("6개월 이상 동일한 비밀번호", "WRC000420")
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        assert svc.login("010-0000-0000", "pw") is False
        assert "코레일+ 앱" in svc.last_login_error and "WRC000420" in svc.last_login_error

    def test_app_version_block_gets_upgrade_hint(self, monkeypatch):
        """-8204: Korail changed the app protocol (DynaPath v2, 2026-10-21) -> tell the user to update pykorail."""
        svc = KorailService()
        fake = MagicMock()
        fake.login.side_effect = LoginFailedError("최신 버전으로 업데이트 후 이용해 주세요", "-8204")
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        assert svc.login("010-0000-0000", "pw") is False
        assert "pykorail" in svc.last_login_error and "-8204" not in svc.last_login_error.split("\n")[0][:0]

    def test_login_success(self, monkeypatch):
        svc = KorailService()
        fake = MagicMock()
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        assert svc.login("010-0000-0000", "pw") is True
        fake.login.assert_called_once_with("010-0000-0000", "pw")
        assert svc.is_logged_in and svc.last_login_error is None

    @pytest.mark.parametrize(
        "exc, fragment",
        [
            (LoginFailedError("비밀번호가 틀렸습니다", "WRT200320"), "비밀번호"),
            (AccessRestrictedError(403, "매크로 등 미허가 도구", "-2000"), "이용 제한"),
            (HttpStatusError(403, "VPN 또는 데이터센터", "-8202"), "VPN"),
        ],
    )
    def test_login_failure_keeps_server_reason(self, monkeypatch, exc, fragment):
        svc = KorailService()
        fake = MagicMock()
        fake.login.side_effect = exc
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        assert svc.login("010-0000-0000", "pw") is False
        assert not svc.is_logged_in
        assert fragment in svc.last_login_error


class TestSearchTrains:
    def test_filters_by_max_dep_time_using_train_field(self, service):
        svc, fake = service
        fake.trains.search.return_value = [_train("094400"), _train("125000"), _train("130000"), _train("130100")]
        trains = svc.search_trains("20261001", "서울", "부산", "090000", "1300", TrainType.KTX, 1, verbose=False)
        # "1300까지": a 13:00 departure is still inside the window, 13:01 is not
        assert [t.dep_time for t in trains] == ["094400", "125000", "130000"]
        kwargs = fake.trains.search.call_args.kwargs
        assert kwargs["depart_after"] == datetime(2026, 10, 1, 9, 0, 0)
        assert kwargs["train_type"] == TrainType.KTX

    def test_no_results_is_empty_list(self, service):
        svc, fake = service
        fake.trains.search.side_effect = NoResultsError()
        assert svc.search_trains("20261001", "서울", "부산", verbose=False) == []

    def test_maintenance_error_is_transient(self, service):
        svc, fake = service
        fake.trains.search.side_effect = KorailError("서비스를 일시중지하오니", "S000")
        assert svc.search_trains("20261001", "서울", "부산", verbose=False) == []

    def test_past_departure_is_fatal_value_error(self, service):
        svc, fake = service
        fake.trains.search.side_effect = PastDepartureError(datetime(2020, 1, 1), datetime(2026, 10, 6))
        with pytest.raises(ValueError):
            svc.search_trains("20200101", "서울", "부산", verbose=False)

    @pytest.mark.parametrize(
        "exc",
        [AccessRestrictedError(403, "매크로", "-2000"), HttpStatusError(403, "VPN 또는 데이터센터", "-8202")],
    )
    def test_access_restriction_stops_the_loop(self, service, exc):
        svc, fake = service
        fake.trains.search.side_effect = exc
        with pytest.raises(KorailAccessRestrictedError):
            svc.search_trains("20261001", "서울", "부산", verbose=False)

    def test_session_expiry_triggers_relogin(self, service):
        svc, fake = service
        fake.trains.search.side_effect = NeedToLoginError(code="P058")
        assert svc.search_trains("20261001", "서울", "부산", verbose=False) == []
        fake.login.assert_called_once_with("010-0000-0000", "pw")
        assert svc.is_logged_in

    def test_relogin_failure_is_not_fatal_and_backs_off(self, service):
        svc, fake = service
        fake.trains.search.side_effect = NeedToLoginError(code="P058")
        fake.login.side_effect = LoginFailedError("점검 중", "S000")
        assert svc.search_trains("20261001", "서울", "부산", verbose=False) == []
        assert not svc.is_logged_in
        # second call within the backoff window must not hit the login endpoint again
        assert svc.search_trains("20261001", "서울", "부산", verbose=False) == []
        assert fake.login.call_count == 1


class TestReserveTrain:
    def test_not_logged_in_within_backoff_returns_none_instead_of_raising(self, service):
        svc, fake = service
        svc._logged_in = False
        svc._last_relogin_attempt = __import__("time").time()  # inside backoff window
        assert svc.reserve_train(_train("090000")) is None
        fake.reservations.create.assert_not_called()

    def test_sold_out_returns_none(self, service):
        svc, fake = service
        fake.reservations.create.side_effect = SoldOutError(code="ERR211161")
        assert svc.reserve_train(_train("090000")) is None

    def test_duplicate_reservation_marker(self, service):
        svc, fake = service
        fake.reservations.create.side_effect = KorailError("동일한 예약 내역이 존재합니다", "WRR800029")
        assert svc.reserve_train(_train("090000")) == "DUPLICATE"

    def test_success_returns_reservation(self, service):
        svc, fake = service
        reservation = MagicMock(rsv_id="123")
        fake.reservations.create.return_value = reservation
        assert svc.reserve_train(_train("090000"), passenger_count=2) is reservation
        passengers = fake.reservations.create.call_args.kwargs["passengers"]
        assert len(passengers) == 1 and passengers[0].count == 2

    def test_access_restriction_propagates(self, service):
        svc, fake = service
        fake.reservations.create.side_effect = AccessRestrictedError(403, "매크로", "-2000")
        with pytest.raises(KorailAccessRestrictedError):
            svc.reserve_train(_train("090000"))


class TestTimetable:
    def test_includes_sold_out_and_waiting_without_login(self, monkeypatch):
        svc = KorailService()
        fake = MagicMock()
        monkeypatch.setattr(svc, "_instance", lambda: fake)
        fake.trains.search.return_value = [_train("090000"), _train("130000")]
        trains = svc.search_timetable("20261225", "서울", "부산", dep_time="090000", train_type=TrainType.KTX)
        assert len(trains) == 2
        kwargs = fake.trains.search.call_args.kwargs
        assert kwargs["include_no_seats"] is True and kwargs["include_waiting_list"] is True
        assert kwargs["depart_after"] == datetime(2026, 12, 25, 9, 0, 0)
        fake.login.assert_not_called()
        assert [t.dep_time for t in svc.filter_until(trains, "1200")] == ["090000"]
        assert len(svc.filter_until(trains, "2400")) == 2

    def test_no_results_is_empty(self, service):
        svc, fake = service
        fake.trains.search.side_effect = NoResultsError()
        assert svc.search_timetable("20261225", "서울", "부산") == []

    @pytest.mark.parametrize("exc", [StationNotFoundError("없는역"), PastDepartureError(datetime(2020, 1, 1), datetime(2026, 10, 6))])
    def test_user_errors_are_value_errors(self, service, exc):
        svc, fake = service
        fake.trains.search.side_effect = exc
        with pytest.raises(ValueError):
            svc.search_timetable("20200101", "서울", "부산")

    def test_access_restriction(self, service):
        svc, fake = service
        fake.trains.search.side_effect = AccessRestrictedError(403, "매크로", "-2000")
        with pytest.raises(KorailAccessRestrictedError):
            svc.search_timetable("20261225", "서울", "부산")


class TestListReservations:
    def test_returns_all(self, service):
        svc, fake = service
        fake.reservations.all.return_value = ["r1", "r2"]
        assert svc.list_reservations() == ["r1", "r2"]

    def test_requires_login(self):
        with pytest.raises(ValueError):
            KorailService().list_reservations()

    def test_session_expired(self, service):
        svc, fake = service
        fake.reservations.all.side_effect = NeedToLoginError(code="P058")
        with pytest.raises(ValueError):
            svc.list_reservations()
        assert not svc.is_logged_in


class TestSearchWindows:
    """The reservation loop only queries Korail inside the configured time slots."""

    def test_loop_searches_only_inside_slots_with_varied_pauses(self, service):
        import random
        from datetime import timedelta, timezone
        from utils.pacing import SearchPacer, parse_windows

        kst = timezone(timedelta(hours=9))

        class Clock:
            now = datetime(2026, 12, 1, 14, 0, tzinfo=kst)

            def __call__(self):
                return self.now

            def sleep(self, seconds):
                self.now += timedelta(seconds=seconds)

        clock = Clock()
        svc, fake = service
        svc._last_login_time = __import__("time").time()
        svc.pacer = SearchPacer(windows=parse_windows("00:00-00:30,06:00-06:30,07:00-07:30,18:00-18:30,19:00-19:30"),
                                min_interval=3, humanize=True, rng=random.Random(5), now=clock, sleep=clock.sleep)
        searched_at = []
        seat_appears = datetime(2026, 12, 2, 6, 15, tzinfo=kst)

        def search(*args, **kwargs):
            searched_at.append(clock.now)
            return [_train("090000")] if clock.now >= seat_appears else []

        fake.trains.search.side_effect = search
        fake.reservations.create.return_value = "RESERVED"

        result = svc.search_and_reserve_loop("20261225", "서울", "부산", dep_time="090000", train_type=TrainType.KTX)

        assert result == "RESERVED"
        slot_starts = {0: 0, 6: 0, 7: 0, 18: 0, 19: 0}
        first = searched_at[0]
        for moment in searched_at:
            minutes = moment.hour * 60 + moment.minute + moment.second / 60
            in_initial = moment - first <= timedelta(minutes=2.7)           # short check right after start
            in_slot = moment.hour in slot_starts and moment.minute < 43    # 5 min late start + 37.5 min at most
            assert in_initial or in_slot, f"searched outside the slots at {moment}"
            assert minutes >= 0
        hours = sorted({m.hour for m in searched_at})
        assert hours == [0, 6, 14, 18, 19]                                 # 14h = initial check; then every slot
        gaps = [(b - a).total_seconds() for a, b in zip(searched_at, searched_at[1:]) if (b - a).total_seconds() < 600]
        assert min(gaps) >= 3.0 and len({round(g, 1) for g in gaps}) > 50   # never faster than 3s, no fixed rhythm
        assert searched_at[-1] >= seat_appears

    def test_always_mode_keeps_the_fixed_interval(self, service, monkeypatch):
        svc, fake = service
        slept = []
        monkeypatch.setattr("utils.pacing.time.sleep", slept.append)
        fake.trains.search.side_effect = [NoResultsError(), NoResultsError(), [_train("090000")]]
        fake.reservations.create.return_value = "RESERVED"
        assert svc.search_and_reserve_loop("20991231", "서울", "부산") == "RESERVED"
        assert slept == [svc._search_interval, svc._search_interval]

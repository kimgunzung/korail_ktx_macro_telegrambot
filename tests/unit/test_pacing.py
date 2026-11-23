"""Unit tests for search time slots and varied refresh pauses (injected clock, no real waiting)."""
import random
from datetime import datetime, timedelta

import pytest

from utils.pacing import DEFAULT_WINDOWS, KST, SearchPacer, Window, describe_windows, parse_windows


class Clock:
    """Wall clock that only advances when the pacer sleeps."""

    def __init__(self, start: datetime):
        self.now = start
        self.sleeps = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


def make(clock, windows=DEFAULT_WINDOWS, seed=1, **kwargs) -> SearchPacer:
    states = []
    pacer = SearchPacer(windows=parse_windows(windows), rng=random.Random(seed), now=clock, sleep=clock.sleep,
                        on_state=states.append, **kwargs)
    pacer.states = states
    return pacer


class TestParse:
    def test_default_windows(self):
        windows = parse_windows(DEFAULT_WINDOWS)
        assert [w.label for w in windows] == ["00:00~00:30", "06:00~06:30", "07:00~07:30", "18:00~18:30", "19:00~19:30"]
        assert all(w.length_minutes == 30 for w in windows)

    def test_24_00_and_midnight_crossing(self):
        assert parse_windows("24:00-00:30") == [Window(0, 30)]
        assert parse_windows("23:50-00:20") == [Window(23 * 60 + 50, 30)]

    @pytest.mark.parametrize("spec", [None, "", "always", "ALWAYS", "off", "24h"])
    def test_always(self, spec):
        assert parse_windows(spec) == []
        assert "상시" in describe_windows(parse_windows(spec))

    @pytest.mark.parametrize("spec", ["18:00", "18-19", "25:00-26:00", "18:00-18:00", "abc"])
    def test_invalid(self, spec):
        with pytest.raises(ValueError):
            parse_windows(spec)


class TestWindows:
    def test_always_mode_never_waits(self):
        clock = Clock(kst(2026, 12, 1, 14, 0))
        pacer = make(clock, windows="always")
        pacer.wait_for_window()
        assert clock.sleeps == [] and pacer.states == []

    def test_waits_until_the_next_slot_and_starts_late(self):
        clock = Clock(kst(2026, 12, 1, 14, 0))
        pacer = make(clock, initial_minutes=0)
        pacer.wait_for_window()
        # next slot is 18:00; it starts 0..5 minutes late
        assert kst(2026, 12, 1, 18, 0) <= clock.now <= kst(2026, 12, 1, 18, 5, 30)
        assert [s["state"] for s in pacer.states] == ["waiting", "active"]
        end = datetime.fromisoformat(pacer.states[-1]["end"])
        start = datetime.fromisoformat(pacer.states[-1]["start"])
        assert timedelta(minutes=22.5) <= end - start <= timedelta(minutes=37.5)  # 30 min +-25%

    def test_inside_a_slot_searches_immediately(self):
        clock = Clock(kst(2026, 12, 1, 18, 12))
        pacer = make(clock)
        pacer.wait_for_window()
        assert clock.sleeps == []
        assert pacer.states[-1]["state"] == "active" and pacer.states[-1]["kind"] == "window"

    def test_initial_check_then_idle_until_the_slot(self):
        clock = Clock(kst(2026, 12, 1, 14, 0))
        pacer = make(clock, initial_minutes=2)
        pacer.wait_for_window()
        assert clock.sleeps == [] and pacer.states[-1]["kind"] == "initial"
        clock.now += timedelta(minutes=3)  # the short initial check is over
        pacer.wait_for_window()
        assert clock.now >= kst(2026, 12, 1, 18, 0)
        assert pacer.states[-1]["kind"] == "window"

    def test_each_slot_is_used_once_and_all_five_come_round(self):
        clock = Clock(kst(2026, 12, 1, 23, 0))
        pacer = make(clock, initial_minutes=0)
        starts = []
        for _ in range(6):
            pacer.wait_for_window()
            starts.append(clock.now)
            clock.now = datetime.fromisoformat(pacer.states[-1]["end"]) + timedelta(seconds=1)
        assert [s.hour for s in starts] == [0, 6, 7, 18, 19, 0]
        assert starts[0].day == 2 and starts[-1].day == 3

    def test_start_and_length_differ_from_day_to_day(self):
        clock = Clock(kst(2026, 12, 1, 12, 0))
        pacer = make(clock, windows="18:00-18:30", initial_minutes=0)
        sessions = []
        for _ in range(8):
            pacer.wait_for_window()
            state = pacer.states[-1]
            start, end = datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"])
            sessions.append((start.minute * 60 + start.second, round((end - start).total_seconds())))
            clock.now = end + timedelta(seconds=1)
        assert len({offset for offset, _ in sessions}) == 8
        assert len({length for _, length in sessions}) == 8

    def test_no_slot_before_departure_uses_extra_sessions(self):
        """20:00 today for a 22:00 train: the next slot (00:00) is too late, so search now, rest, search again."""
        clock = Clock(kst(2026, 12, 1, 20, 0))
        pacer = make(clock, initial_minutes=0)
        deadline = datetime(2026, 12, 1, 22, 0)  # naive = KST
        pacer.wait_for_window(deadline)
        assert clock.sleeps == [] and pacer.states[-1]["kind"] == "extra"
        first_end = datetime.fromisoformat(pacer.states[-1]["end"])
        clock.now = first_end + timedelta(seconds=1)
        pacer.wait_for_window(deadline)
        rest = clock.now - first_end
        assert timedelta(minutes=20) <= rest <= timedelta(minutes=45, seconds=40)
        assert clock.now < kst(2026, 12, 1, 22, 0)

    def test_past_deadline_returns_so_the_search_reports_it(self):
        clock = Clock(kst(2026, 12, 1, 23, 0))
        pacer = make(clock, initial_minutes=0)
        pacer.wait_for_window(datetime(2026, 12, 1, 22, 0))
        assert clock.sleeps == []

    def test_listener_errors_are_ignored(self):
        clock = Clock(kst(2026, 12, 1, 18, 12))
        pacer = make(clock)
        pacer.on_state = lambda state: 1 / 0
        pacer.wait_for_window()


class TestPauses:
    def test_fixed_interval_when_not_humanized(self):
        pacer = SearchPacer(min_interval=3, humanize=False)
        assert [pacer.next_delay() for _ in range(5)] == [3.0] * 5
        assert pacer.next_delay(at_least=10) == 10

    def test_varied_but_never_below_the_minimum(self):
        pacer = SearchPacer(min_interval=3, humanize=True, rng=random.Random(7))
        delays = [pacer.next_delay() for _ in range(400)]
        assert min(delays) >= 3.0
        assert len({round(d, 2) for d in delays}) > 250          # no fixed rhythm
        assert 4.0 < sorted(delays)[len(delays) // 2] < 7.0      # typical refresh: a few seconds
        assert any(d > 25 for d in delays)                       # occasional longer breaks
        assert sum(delays) / len(delays) < 12

    def test_pause_sleeps_the_drawn_delay(self):
        slept = []
        pacer = SearchPacer(min_interval=3, humanize=True, rng=random.Random(3), sleep=slept.append)
        delay = pacer.pause(at_least=10)
        assert slept == [delay] and delay >= 10

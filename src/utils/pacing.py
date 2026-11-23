"""When and how often the worker queries Korail.

Instead of polling around the clock at a fixed interval, the worker only searches in a
few daily time slots (``SEARCH_WINDOWS``), the way a person opens the app when sold-out
seats tend to come back. Every slot starts a little late and lasts a little shorter or
longer than configured, and the pause between two refreshes varies as well.

``SearchPacer`` is pure logic over an injectable clock, sleep function and RNG so it can
be tested without waiting.
"""
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Korea has no daylight saving time, so a fixed offset is exact (and needs no tzdata on Windows).
KST = timezone(timedelta(hours=9), "KST")

ALWAYS_WORDS = {"", "always", "off", "none", "24h", "all", "항상"}
DEFAULT_WINDOWS = "00:00-00:30,06:00-06:30,07:00-07:30,18:00-18:30,19:00-19:30"

_WAIT_CHUNK_SECONDS = 30  # re-check the wall clock this often while waiting for a slot


@dataclass(frozen=True)
class Window:
    """A daily time slot in KST: ``start_minute`` minutes after midnight, ``length_minutes`` long."""

    start_minute: int
    length_minutes: int

    @property
    def label(self) -> str:
        end = (self.start_minute + self.length_minutes) % (24 * 60)
        return f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}~{end // 60:02d}:{end % 60:02d}"


@dataclass(frozen=True)
class Session:
    """One concrete stretch of searching."""

    start: datetime
    end: datetime
    kind: str = "window"  # window | initial | extra


def _parse_clock(text: str) -> int:
    hour, sep, minute = text.strip().partition(":")
    if not sep or not hour.isdigit() or not minute.isdigit():
        raise ValueError(f"시각 형식이 올바르지 않습니다: '{text}' (HH:MM)")
    h, m = int(hour), int(minute)
    if h == 24 and m == 0:
        h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"시각 범위가 올바르지 않습니다: '{text}'")
    return h * 60 + m


def parse_windows(spec: Optional[str]) -> List[Window]:
    """Parse ``"00:00-00:30,06:00-06:30"``. An empty list means "search all the time".

    Raises:
        ValueError: with a user-facing message
    """
    if spec is None or spec.strip().lower() in ALWAYS_WORDS:
        return []
    windows: List[Window] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        start_text, sep, end_text = part.partition("-")
        if not sep:
            raise ValueError(f"SEARCH_WINDOWS 항목 형식이 올바르지 않습니다: '{part}' (예: 18:00-18:30)")
        start, end = _parse_clock(start_text), _parse_clock(end_text)
        length = (end - start) % (24 * 60)
        if length == 0:
            raise ValueError(f"SEARCH_WINDOWS 항목의 길이가 0입니다: '{part}'")
        windows.append(Window(start, length))
    return sorted(windows, key=lambda w: w.start_minute)


def describe_windows(windows: List[Window]) -> str:
    """One line for confirmations and ``status``."""
    if not windows:
        return "상시 (시간대 제한 없음)"
    return ", ".join(w.label for w in windows) + " 무렵 (시작·길이는 매번 조금씩 달라짐)"


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    return moment.replace(tzinfo=KST) if moment.tzinfo is None else moment.astimezone(KST)


class SearchPacer:
    """Decides when the worker may search and how long to pause between refreshes."""

    def __init__(
        self,
        windows: Optional[List[Window]] = None,
        min_interval: float = 3.0,
        humanize: bool = True,
        start_jitter_minutes: float = 5.0,
        length_jitter: float = 0.25,
        initial_minutes: float = 2.0,
        rng: Optional[random.Random] = None,
        now: Optional[Callable[[], datetime]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        on_state: Optional[Callable[[dict], None]] = None,
    ):
        self.windows = list(windows or [])
        self.min_interval = max(1.0, float(min_interval))
        self.humanize = humanize
        self.start_jitter_minutes = max(0.0, float(start_jitter_minutes))
        self.length_jitter = min(0.9, max(0.0, float(length_jitter)))
        self.initial_minutes = max(0.0, float(initial_minutes))
        self._rng = rng or random.Random()
        self._now = now or (lambda: datetime.now(KST))
        # Looked up at call time so tests that patch ``time.sleep`` are honoured.
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))
        self.on_state = on_state

        self._session: Optional[Session] = None
        self._last_window_start: Optional[datetime] = None  # nominal start of the last planned slot
        self._started = False
        self._announced: Optional[tuple] = None
        self._refreshes_until_break = self._draw_break_distance()

    @classmethod
    def from_settings(cls, settings) -> "SearchPacer":
        return cls(
            windows=parse_windows(settings.SEARCH_WINDOWS),
            min_interval=settings.KORAIL_SEARCH_INTERVAL,
            humanize=settings.SEARCH_HUMANIZE,
            start_jitter_minutes=settings.SEARCH_WINDOW_START_JITTER_MINUTES,
            length_jitter=settings.SEARCH_WINDOW_LENGTH_JITTER,
            initial_minutes=settings.SEARCH_INITIAL_MINUTES,
        )

    # ------------------------------------------------------------ sessions

    def _jittered(self, nominal_start: datetime, length_minutes: float, kind: str = "window") -> Session:
        start = nominal_start + timedelta(minutes=self._rng.uniform(0, self.start_jitter_minutes))
        length = length_minutes * self._rng.uniform(1 - self.length_jitter, 1 + self.length_jitter)
        return Session(start, start + timedelta(minutes=length), kind)

    def _next_window_session(self, now: datetime) -> Session:
        """The slot running now, or the next one. Each slot is used at most once."""
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        candidates = sorted(
            (midnight + timedelta(days=day, minutes=w.start_minute), w)
            for day in (-1, 0, 1, 2) for w in self.windows
        )
        for nominal_start, window in candidates:
            if self._last_window_start is not None and nominal_start <= self._last_window_start:
                continue
            session = self._jittered(nominal_start, window.length_minutes)
            if session.end > now + timedelta(minutes=1):
                self._last_window_start = nominal_start
                return session
        raise RuntimeError("no upcoming search window")  # unreachable with a non-empty window list

    def _extra_session(self, now: datetime) -> Session:
        """No slot is left before the train departs: keep going in sessions separated by rests."""
        length = sum(w.length_minutes for w in self.windows) / len(self.windows)
        if self._session is None:
            start = now
        else:
            start = max(now, self._session.end + timedelta(minutes=self._rng.uniform(20, 45)))
        duration = length * self._rng.uniform(1 - self.length_jitter, 1 + self.length_jitter)
        return Session(start, start + timedelta(minutes=duration), "extra")

    def _plan(self, now: datetime, deadline: Optional[datetime]) -> Session:
        if self._session is not None and now < self._session.end:
            return self._session

        first = not self._started
        self._started = True
        previous_window_start = self._last_window_start
        upcoming = self._next_window_session(now)

        if upcoming.start > now:
            if first and self.initial_minutes > 0:
                # A person checks once right after deciding to book, then comes back later.
                self._last_window_start = previous_window_start  # the slot is still unused
                minutes = self.initial_minutes * self._rng.uniform(0.7, 1.3)
                self._session = Session(now, now + timedelta(minutes=minutes), "initial")
                return self._session
            if deadline is not None and upcoming.start >= deadline:
                self._last_window_start = previous_window_start
                self._session = self._extra_session(now)
                return self._session
        self._session = upcoming
        return self._session

    def wait_for_window(self, deadline: Optional[datetime] = None) -> None:
        """Return once searching is allowed, sleeping until the next session if necessary.

        Args:
            deadline: the moment after which searching is pointless (train departure, KST when
                naive). When no configured slot starts before it, extra sessions are used so
                the search does not sit idle until the train has left.
        """
        if not self.windows:
            return
        deadline = _aware(deadline)
        while True:
            now = self._now()
            if deadline is not None and now >= deadline:
                return  # let the search itself report that the departure has passed
            session = self._plan(now, deadline)
            if session.start <= now:
                self._announce("active", session)
                return
            self._announce("waiting", session)
            self._sleep(min(_WAIT_CHUNK_SECONDS, max(1.0, (session.start - now).total_seconds())))

    def _announce(self, state: str, session: Session) -> None:
        key = (state, session.start, session.end)
        if key == self._announced:
            return
        self._announced = key
        if state == "active":
            logger.info(f"▶ Search session ({session.kind}) until {session.end:%H:%M:%S}")
        else:
            logger.info(f"⏸ Idle until the next search session {session.start:%m/%d %H:%M:%S}~{session.end:%H:%M:%S}")
        if self.on_state:
            try:
                self.on_state({
                    "state": state, "kind": session.kind,
                    "start": session.start.isoformat(), "end": session.end.isoformat(),
                })
            except Exception as e:  # status display must never break the search
                logger.debug(f"Pacing state listener failed: {e}")

    # -------------------------------------------------------------- pauses

    def _draw_break_distance(self) -> int:
        return self._rng.randint(15, 35)

    def next_delay(self, at_least: float = 0.0) -> float:
        """Seconds to wait before the next refresh. Never below ``min_interval``."""
        base = self.min_interval
        if not self.humanize:
            return max(base, at_least)
        # Most refreshes come a few seconds apart, with a long right tail.
        delay = base + min(self._rng.gammavariate(2.0, base * 0.35), base * 3)
        if self._rng.random() < 0.07:
            delay += self._rng.uniform(6, 20)  # looked at the screen for a while
        self._refreshes_until_break -= 1
        if self._refreshes_until_break <= 0:
            delay += self._rng.uniform(25, 75)  # put the phone down for a moment
            self._refreshes_until_break = self._draw_break_distance()
        return max(base, at_least, delay)

    def pause(self, at_least: float = 0.0) -> float:
        """Sleep for :meth:`next_delay` seconds and return the duration."""
        delay = self.next_delay(at_least)
        self._sleep(delay)
        return delay

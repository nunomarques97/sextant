"""Time value objects.

Every timestamp crossing a boundary in this system is timezone-aware and UTC.
A naive datetime is rejected where it is constructed, not where it later causes
an off-by-one-hour bar alignment that looks like alpha.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sextant.domain.errors import DomainError


class NaiveDatetimeError(DomainError):
    """A datetime without a usable timezone was supplied."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"Naive datetime rejected: {value!r}. Every timestamp must be "
            "timezone-aware; construct it with tzinfo=UTC."
        )


@dataclass(frozen=True, slots=True, order=True)
class Timestamp:
    """A timezone-aware instant, normalised to UTC."""

    value: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, datetime):
            raise NaiveDatetimeError(self.value)
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise NaiveDatetimeError(self.value)
        if self.value.utcoffset() != timedelta(0):
            object.__setattr__(self, "value", self.value.astimezone(UTC))

    @classmethod
    def parse(cls, text: str) -> Timestamp:
        """Build a Timestamp from an ISO-8601 string carrying an offset."""
        return cls(datetime.fromisoformat(text))

    @classmethod
    def from_epoch_millis(cls, millis: int) -> Timestamp:
        """Build a Timestamp from Unix epoch milliseconds, the exchange wire format."""
        return cls(datetime.fromtimestamp(millis / 1000, tz=UTC))

    @property
    def epoch_millis(self) -> int:
        """Unix epoch milliseconds for this instant."""
        return int(self.value.timestamp() * 1000)

    def plus(self, delta: timedelta) -> Timestamp:
        """Return this instant shifted by ``delta``."""
        return Timestamp(self.value + delta)

    def isoformat(self) -> str:
        """ISO-8601 representation, always with an explicit UTC offset."""
        return self.value.isoformat()


class Timeframe(StrEnum):
    """Bar aggregation intervals the system understands."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def duration(self) -> timedelta:
        """The wall-clock length of one bar at this timeframe."""
        return _TIMEFRAME_DURATIONS[self]


_TIMEFRAME_DURATIONS: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}

"""Clock adapters.

``datetime.now()`` appears here and nowhere else in the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sextant.domain.time import Timestamp


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Wall-clock time. Used by PAPER and LIVE."""

    def now(self) -> Timestamp:
        """The current instant, timezone-aware UTC."""
        return Timestamp(datetime.now(tz=UTC))


@dataclass(slots=True)
class SimulatedClock:
    """A clock the caller advances explicitly. Used by BACKTEST.

    The engine cannot tell this apart from ``SystemClock``, which is the point:
    a backtest and a live run execute the same code with a different clock.
    """

    current: Timestamp

    def now(self) -> Timestamp:
        """The instant this clock has been advanced to."""
        return self.current

    def advance_to(self, instant: Timestamp) -> None:
        """Move the clock forward. Moving backwards is refused."""
        if instant < self.current:
            raise ValueError(
                f"SimulatedClock cannot move backwards: {instant.isoformat()} "
                f"precedes {self.current.isoformat()}"
            )
        self.current = instant

"""The Clock port.

Backtest, paper and live run the same engine code with a different clock. That
is the whole point: if the engine could read the wall clock directly, a
backtest and a live run would not be the same program, and agreement between
them would prove nothing.

Reading wall-clock time is forbidden outside ``sextant.adapters``, and a test
greps for it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sextant.domain.time import Timestamp


@runtime_checkable
class Clock(Protocol):
    """A source of the current instant."""

    def now(self) -> Timestamp:
        """The current instant according to this clock, always timezone-aware UTC."""
        ...


@runtime_checkable
class AdvanceableClock(Clock, Protocol):
    """A clock whose instant is set by the caller rather than by the world.

    A backtest needs one; paper and live cannot have one. The engine holds a
    plain ``Clock`` and asks structurally whether it can be advanced, which is
    how the same engine code drives a simulation and a live run without a mode
    flag and without importing an adapter to test its type against.
    """

    def advance_to(self, instant: Timestamp) -> None:
        """Move this clock to ``instant``. Moving backwards is refused."""
        ...

"""The Clock port.

Backtest, paper and live run the same engine code with a different clock. That
is the whole point: if the engine could call ``datetime.now()`` directly, a
backtest and a live run would not be the same program, and agreement between
them would prove nothing.

``datetime.now()`` is forbidden outside ``sextant.adapters``.
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

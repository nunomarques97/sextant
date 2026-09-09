"""Run modes.

The ladder is BACKTEST -> PAPER -> LIVE. There is no code path in which an
unset or unrecognised mode resolves to LIVE: the enum's default is BACKTEST and
every default in the configuration layer points at it.
"""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    """How the system is being run."""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"

    @classmethod
    def _missing_(cls, value: object) -> RunMode | None:
        """Accept any casing from configuration. Anything unrecognised stays an error.

        This deliberately does not fall back to a default: an unrecognised mode
        string is a configuration mistake, and guessing which mode was meant is
        exactly the kind of helpfulness that eventually guesses LIVE.
        """
        if isinstance(value, str):
            normalised = value.strip().lower()
            for member in cls:
                if member.value == normalised:
                    return member
        return None

    @classmethod
    def default(cls) -> RunMode:
        """The mode used whenever nothing says otherwise. Always the safest one."""
        return cls.BACKTEST

    @property
    def touches_real_money(self) -> bool:
        """Whether this mode can place an order that settles against real funds."""
        return self is RunMode.LIVE

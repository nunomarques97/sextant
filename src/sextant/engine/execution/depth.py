"""Reducing a day of order-book snapshots to the two numbers capacity is read from.

The input is a sequence of snapshot bands: an instant, a signed distance from mid in
per cent, and the resting notional cumulative *to* that distance. The output is, per
day, the median across the day's minutes of the notional resting within one per cent
of mid and within five per cent, plus the same two restricted to the five minutes
after midnight UTC.

Three decisions worth arguing for
---------------------------------

**Both sides are added, and neither is halved.** A pair trade lifts one side and hits
the other, so the quantity a capacity statement needs is what rests on both. Reporting
one side would understate a cash-and-carry's constraint by construction.

**A minute is the unit, and a minute is the mean of its snapshots.** The publisher
takes two snapshots a minute, roughly thirty seconds apart, so a median taken over
snapshots would weight a minute by how many of them survived publication. The
registered statistic says *the median across the day's minutes*, and this is what that
means when a minute holds more than one observation.

**A window with no snapshot answers None, never zero.** The five-minute window after
midnight is empty on most published symbol-days: the publisher's coverage frequently
starts hours into the day. Zero would read as *no depth rested there*, which is a
claim about the market. None reads as *this dataset does not say*, which is the truth
and is what invariant 9 requires.

No venue name appears here, and nothing here knows what a perpetual is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sextant.domain.errors import DomainError
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp

#: The two distances the registered statistic reports, in per cent from mid.
NEAR_PERCENT = 1
FAR_PERCENT = 5

#: The minutes after midnight UTC the second pair of figures is restricted to. A
#: monthly rebalance decides at 00:00 and would execute inside this window, so depth
#: measured across a whole day is not the depth that trade would have met.
OPENING_MINUTES = 5


class DepthUnreadable(DomainError):
    """A snapshot band carried something no reduction can be defined over."""


@dataclass(frozen=True, slots=True)
class DepthBand:
    """One distance band of one snapshot: the shape the reduction consumes.

    Deliberately not the adapter's row type. The adapter carries publication detail
    the reduction has no use for, and the engine may not import an adapter.
    """

    at: Timestamp
    percentage: int
    notional: Notional


@dataclass(frozen=True, slots=True)
class DepthSummary:
    """One symbol-day, reduced.

    ``minutes`` is how many distinct minutes carried a snapshot, out of the 1,440 a
    complete day has. It travels with every figure because a median over 300 minutes
    and a median over 1,440 are not the same measurement, and a reader cannot tell
    them apart from the median alone.
    """

    day: str
    minutes: int
    near: Notional | None
    far: Notional | None
    opening_minutes: int
    opening_near: Notional | None
    opening_far: Notional | None

    @property
    def is_evaluable(self) -> bool:
        """Whether the day carried any snapshot at all."""
        return self.minutes > 0

    @property
    def opening_is_evaluable(self) -> bool:
        """Whether the opening window carried any snapshot.

        False on most published days. Reported rather than filled.
        """
        return self.opening_minutes > 0

    def as_json(self) -> dict[str, object]:
        return {
            "day": self.day,
            "minutes_with_a_snapshot": self.minutes,
            "minutes_in_a_complete_day": 1440,
            f"median_notional_within_{NEAR_PERCENT}pct": _text(self.near),
            f"median_notional_within_{FAR_PERCENT}pct": _text(self.far),
            "opening_window_minutes": self.opening_minutes,
            f"opening_median_notional_within_{NEAR_PERCENT}pct": _text(self.opening_near),
            f"opening_median_notional_within_{FAR_PERCENT}pct": _text(self.opening_far),
            "opening_window": f"00:00-00:0{OPENING_MINUTES} UTC",
            "note": (
                "Both sides of the book, summed, cumulative to the stated distance from "
                "mid. A null figure means this dataset carried no snapshot in that "
                "window, which is not a depth of zero."
            ),
        }


def _text(value: Notional | None) -> str | None:
    """A figure as exact text, or None where nothing was observed."""
    return None if value is None else str(value.amount)


def summarise_day(day: str, bands: Sequence[DepthBand]) -> DepthSummary:
    """Reduce one symbol-day's snapshot bands to the registered statistic."""
    near_by_minute = _by_minute(bands, NEAR_PERCENT)
    far_by_minute = _by_minute(bands, FAR_PERCENT)
    minutes = sorted(set(near_by_minute) | set(far_by_minute))
    opening = [minute for minute in minutes if minute < OPENING_MINUTES]
    return DepthSummary(
        day=day,
        minutes=len(minutes),
        near=_median([near_by_minute[minute] for minute in sorted(near_by_minute)]),
        far=_median([far_by_minute[minute] for minute in sorted(far_by_minute)]),
        opening_minutes=len(opening),
        opening_near=_median([near_by_minute[m] for m in opening if m in near_by_minute]),
        opening_far=_median([far_by_minute[m] for m in opening if m in far_by_minute]),
    )


def _by_minute(bands: Sequence[DepthBand], within: int) -> Mapping[int, Decimal]:
    """Per minute of the day, both sides' notional within ``within`` per cent.

    Keyed by the minute's index since midnight, 0 to 1,439, so the opening window is a
    comparison on a small integer rather than on a parsed clock time. A minute holding
    two snapshots contributes their mean.
    """
    if within <= 0:
        raise DepthUnreadable(f"a distance from mid must be positive, got {within}")
    per_snapshot: dict[tuple[int, int], Decimal] = {}
    for band in bands:
        if abs(band.percentage) != within:
            continue
        stamp = band.at.value
        second = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
        key = (second // 60, second)
        per_snapshot[key] = per_snapshot.get(key, Decimal(0)) + band.notional.amount
    totals: dict[int, list[Decimal]] = {}
    for (minute, _), value in per_snapshot.items():
        totals.setdefault(minute, []).append(value)
    return {
        minute: sum(values, Decimal(0)) / Decimal(len(values)) for minute, values in totals.items()
    }


def _median(values: Sequence[Decimal]) -> Notional | None:
    """The median, or None over nothing. Even counts average the two middle values."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return Notional(ordered[middle])
    return Notional((ordered[middle - 1] + ordered[middle]) / Decimal(2))


def across_days(summaries: Sequence[DepthSummary]) -> tuple[Notional | None, Notional | None]:
    """The median of the daily medians, near and far, over the days that carried one.

    A day with no snapshot is not counted as a zero and not counted at all. The count
    of days that did carry one is reported beside this by the caller, because a median
    over three days and one over seventeen are different claims.
    """
    near = [item.near.amount for item in summaries if item.near is not None]
    far = [item.far.amount for item in summaries if item.far is not None]
    return _median(near), _median(far)


__all__ = [
    "FAR_PERCENT",
    "NEAR_PERCENT",
    "OPENING_MINUTES",
    "DepthBand",
    "DepthSummary",
    "DepthUnreadable",
    "across_days",
    "summarise_day",
]

"""Walk-forward windows, and the rule that makes them walk-forward.

The only mode this engine has is walk-forward. That is a structural claim rather
than a discipline, and this module is where it is made structural: a fold pairs
an in-sample window with an out-of-sample window that starts where the in-sample
one ends, the constructor refuses any other arrangement, and out-of-sample
windows across folds may not overlap each other.

Everything about a strategy - its parameters, its thresholds, its choice of
lookback - is decided inside the in-sample window. Performance is reported from
the out-of-sample windows and from nowhere else. There is no object in this
package that carries an in-sample equity curve, because if one existed it would
eventually be printed.

**Anchored, not rolling.** Each fold's in-sample window starts at the beginning
of the whole evaluation and grows; it does not slide. With thirty months there
is not enough history to throw the early part away, and an anchored window at
least uses all of it. The cost is that consecutive folds share most of their
fitting data, so their out-of-sample results are less independent than three
truly separate experiments would be. That is a real weakness of a short window
and it is stated rather than designed around.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sextant.domain.errors import DomainError
from sextant.domain.time import Timestamp


class InvalidWindow(DomainError):
    """A window or a walk-forward plan was constructed inconsistently."""


@dataclass(frozen=True, slots=True, order=True)
class Window:
    """A half-open interval of time, ``[start, end)``.

    Half-open so that consecutive windows abut exactly: an instant belongs to
    one window and never to two. A rebalance falling on a boundary belongs to
    the window that is starting, which is the reading that keeps a decision out
    of the sample it was fitted on.
    """

    start: Timestamp
    end: Timestamp

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise InvalidWindow(
                f"A window must run forwards: start {self.start.isoformat()} is not "
                f"before end {self.end.isoformat()}"
            )

    def contains(self, at: Timestamp) -> bool:
        """Whether ``at`` falls inside this window."""
        return self.start <= at < self.end

    def overlaps(self, other: Window) -> bool:
        """Whether two windows share any instant."""
        return self.start < other.end and other.start < self.end

    def as_json(self) -> dict[str, str]:
        """Serialisable form, for the run manifest."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    def __str__(self) -> str:
        return f"[{self.start.isoformat()[:10]}, {self.end.isoformat()[:10]})"


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One fitting window and the untouched window it is judged on."""

    index: int
    in_sample: Window
    out_of_sample: Window

    def __post_init__(self) -> None:
        if self.out_of_sample.start != self.in_sample.end:
            raise InvalidWindow(
                f"Fold {self.index}: the out-of-sample window must begin exactly where "
                f"the in-sample one ends. In-sample ends {self.in_sample.end.isoformat()}, "
                f"out-of-sample starts {self.out_of_sample.start.isoformat()}. A gap "
                "discards data; an overlap is not out-of-sample at all."
            )

    def as_json(self) -> dict[str, object]:
        """Serialisable form, for the run manifest."""
        return {
            "index": self.index,
            "in_sample": self.in_sample.as_json(),
            "out_of_sample": self.out_of_sample.as_json(),
        }


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    """A sequence of folds with non-overlapping out-of-sample windows."""

    folds: tuple[WalkForwardFold, ...]

    def __post_init__(self) -> None:
        if not self.folds:
            raise InvalidWindow("A walk-forward plan needs at least one fold.")
        for earlier, later in zip(self.folds, self.folds[1:], strict=False):
            if earlier.out_of_sample.overlaps(later.out_of_sample):
                raise InvalidWindow(
                    f"Folds {earlier.index} and {later.index} have overlapping "
                    f"out-of-sample windows, {earlier.out_of_sample} and "
                    f"{later.out_of_sample}. An instant scored twice is an instant "
                    "counted twice."
                )

    @property
    def out_of_sample_span(self) -> Window:
        """The whole out-of-sample period, first fold's start to last fold's end."""
        return Window(
            start=self.folds[0].out_of_sample.start,
            end=self.folds[-1].out_of_sample.end,
        )

    @property
    def fold_count(self) -> int:
        """How many folds this plan runs."""
        return len(self.folds)

    def as_json(self) -> dict[str, object]:
        """Serialisable form, for the run manifest."""
        return {
            "fold_count": self.fold_count,
            "folds": [fold.as_json() for fold in self.folds],
            "out_of_sample_span": self.out_of_sample_span.as_json(),
        }


def month_start(at: Timestamp) -> Timestamp:
    """The first instant of the UTC month containing ``at``."""
    return Timestamp(datetime(at.value.year, at.value.month, 1, tzinfo=UTC))


def add_months(at: Timestamp, months: int) -> Timestamp:
    """``at`` shifted by whole months, anchored to the first of the month.

    Calendar arithmetic rather than a fixed number of days, because a monthly
    rebalance that drifts by two days a year eventually rebalances in the wrong
    month and nothing in the output would show it.
    """
    zero_based = (at.value.year * 12 + at.value.month - 1) + months
    return Timestamp(datetime(zero_based // 12, zero_based % 12 + 1, 1, tzinfo=UTC))


def monthly_instants(window: Window) -> tuple[Timestamp, ...]:
    """Every month start in ``[window.start, window.end)``, in order.

    The rebalance schedule. ``window.start`` must itself be a month start; a
    plan whose windows do not fall on month boundaries would rebalance on
    different days of the month in different folds, and the comparison between
    folds would be measuring the calendar.
    """
    if month_start(window.start) != window.start:
        raise InvalidWindow(
            f"{window.start.isoformat()} is not the first instant of a month. Rebalance "
            "windows are month-aligned so that folds are comparable."
        )
    instants: list[Timestamp] = []
    cursor = window.start
    while cursor < window.end:
        instants.append(cursor)
        cursor = add_months(cursor, 1)
    return tuple(instants)


def anchored_plan(
    *,
    first_month: Timestamp,
    total_months: int,
    in_sample_months: int,
    fold_count: int,
) -> WalkForwardPlan:
    """Build an anchored plan covering ``total_months`` from ``first_month``.

    The first ``in_sample_months`` are fitting-only and are never scored. What
    remains is divided into ``fold_count`` equal out-of-sample windows, and each
    fold's in-sample window runs from the very start up to its own out-of-sample
    window. Any months that do not divide evenly are left off the end and
    reported by the caller, rather than being folded into the last window and
    making one fold longer than the others.
    """
    if in_sample_months <= 0:
        raise InvalidWindow(f"in_sample_months must be positive, got {in_sample_months}")
    if fold_count <= 0:
        raise InvalidWindow(f"fold_count must be positive, got {fold_count}")
    if month_start(first_month) != first_month:
        raise InvalidWindow(f"{first_month.isoformat()} is not the first instant of a month.")

    available = total_months - in_sample_months
    fold_months = available // fold_count
    if fold_months <= 0:
        raise InvalidWindow(
            f"{total_months} months with {in_sample_months} held back for fitting leaves "
            f"{available} for {fold_count} out-of-sample windows, which is not enough for "
            "one month each. This window is too short for this plan."
        )

    folds: list[WalkForwardFold] = []
    for index in range(fold_count):
        in_sample_end = add_months(first_month, in_sample_months + index * fold_months)
        out_of_sample_end = add_months(in_sample_end, fold_months)
        folds.append(
            WalkForwardFold(
                index=index,
                in_sample=Window(start=first_month, end=in_sample_end),
                out_of_sample=Window(start=in_sample_end, end=out_of_sample_end),
            )
        )
    return WalkForwardPlan(folds=tuple(folds))


def concatenate(windows: Sequence[Window]) -> Window:
    """The span from the earliest start to the latest end."""
    if not windows:
        raise InvalidWindow("Cannot span an empty sequence of windows.")
    return Window(
        start=min(window.start for window in windows),
        end=max(window.end for window in windows),
    )

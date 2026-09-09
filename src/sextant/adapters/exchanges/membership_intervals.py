"""Turning a sequence of membership snapshots into bracketed listing spells.

Both venues in this project publish the same kind of evidence in different
grains. Kraken ships one archive per quarter; Binance ships one file per symbol
per month. In each case the fact is *presence in a published period*, and the
inference rule on top of it is identical:

    a symbol present in period *N* and absent in *N+1* stopped trading strictly
    after the first boundary and at or before the second, recorded as the
    interval ``(N.ends_at, N+1.ends_at]`` and never narrowed to a date.

Because the rule is the same, it lives here once rather than twice. What differs
between the venues is the period type, and a period is only ever asked for the
instant at which its membership is asserted. That is the whole of
:class:`Period`.

Three things this module refuses to smooth over, carried over verbatim from the
Kraken implementation it was extracted from:

**The archive edges are unbounded, not pinned.** A symbol present in the
earliest held period may have listed at any earlier time, so its listing bracket
is open into the past; present in the latest, its delisting bracket is open into
the future. Pinning either to the edge of the archive would turn a fact about
the download into a fact about the venue.

**A symbol may live more than once.** Present, absent, then present again is two
spells rather than one long one. Collapsing them would report the symbol as
tradable throughout a period the archive says it was gone.

**A gap in the held periods widens every interval that spans it.** Brackets are
drawn against the periods actually *held*, never against the periods that ought
to exist, so a hole in the download shows up as a wider bracket instead of being
papered over.

Pure computation. No I/O, no venue, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sextant.domain.listing import EventInterval, IntervalListingWindow
from sextant.domain.time import Timestamp


@runtime_checkable
class Period(Protocol):
    """A published period, asked only for the instant its membership asserts.

    ``ends_at`` is the *exclusive* boundary - the first instant of the following
    period - so consecutive periods abut exactly and a delisting bracket
    ``(previous.ends_at, this.ends_at]`` has no gap and no overlap.
    """

    @property
    def ends_at(self) -> Timestamp:
        """The instant at which this period's membership snapshot is asserted."""
        ...

    @property
    def label(self) -> str:
        """The label the venue's own file names use, for reporting."""
        ...


def spells_from_presence(
    present: Sequence[bool],
    held: Sequence[Period],
) -> tuple[IntervalListingWindow, ...]:
    """Cut a presence flag per held period into bracketed listing spells.

    ``present[i]`` says whether the symbol appeared in ``held[i]``. The two
    sequences must be the same length and ``held`` must already be in ascending
    order; a caller that sorts its snapshots after reading them gets brackets
    that run forwards, and one that does not gets a backwards bracket rejected
    by the domain type rather than quietly stored.
    """
    if len(present) != len(held):
        raise ValueError(
            f"presence has {len(present)} flags for {len(held)} held periods; "
            "a flag per period is what makes the brackets meaningful"
        )
    spells: list[IntervalListingWindow] = []
    index = 0
    while index < len(present):
        if not present[index]:
            index += 1
            continue
        start = index
        while index + 1 < len(present) and present[index + 1]:
            index += 1
        end = index

        listed = (
            EventInterval.at_or_before(held[start].ends_at)
            if start == 0
            else EventInterval.between(held[start - 1].ends_at, held[start].ends_at)
        )
        delisted = (
            EventInterval.after_only(held[end].ends_at)
            if end == len(held) - 1
            else EventInterval.between(held[end].ends_at, held[end + 1].ends_at)
        )
        spells.append(IntervalListingWindow(listed_during=listed, delisted_during=delisted))
        index += 1
    return tuple(spells)

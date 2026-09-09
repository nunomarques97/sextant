"""Where a historical fact came from.

A backtest is only as trustworthy as its instrument history, and instrument
history is the part of a dataset most easily invented. An exchange will happily
tell you which pairs trade *today*; almost none will tell you which pairs traded
in March 2021, or when a pair that no longer exists stopped trading. The gap
between those two is where survivorship bias lives.

So a listing window is never carried as a bare pair of timestamps. It carries
the source that established it, and the source is a first-class value that
travels with the instrument into every report. "the venue's own API says so"
and "we inferred it from the earliest bar we could find" are both usable, and
they are not the same claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sextant.domain.errors import SextantError
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue


class Provenance(StrEnum):
    """How a listing window was established, strongest evidence first.

    The first three are *directly verified*: the venue itself asserted the fact.
    ``RECONSTRUCTED`` is derived from venue data by a stated methodology, which
    is honest but weaker - a first observed bar is a lower bound on a listing
    date, not the listing date. ``SECONDARY`` and ``UNVERIFIED`` may never be
    presented as fact in a report.
    """

    VENUE_API = "venue_api"
    """The venue's own API returned this metadata."""

    VENUE_ARCHIVE = "venue_archive"
    """The venue's official published data archive contained it."""

    VENUE_ANNOUNCEMENT = "venue_announcement"
    """The venue announced it publicly, with a date."""

    RECONSTRUCTED = "reconstructed"
    """Derived from venue data by a documented methodology. A bound, not a fact."""

    SECONDARY = "secondary"
    """A third party asserted it. Never treated as the venue's own statement."""

    UNVERIFIED = "unverified"
    """Nothing established it. Default, so that silence is never mistaken for evidence."""

    @property
    def is_directly_verified(self) -> bool:
        """Whether the venue itself asserted this, rather than us inferring it."""
        return self in _DIRECTLY_VERIFIED


_DIRECTLY_VERIFIED: frozenset[Provenance] = frozenset(
    {Provenance.VENUE_API, Provenance.VENUE_ARCHIVE, Provenance.VENUE_ANNOUNCEMENT}
)


@dataclass(frozen=True, slots=True)
class ListingWindow:
    """When an instrument was tradable, and who says so."""

    listed_at: Timestamp
    delisted_at: Timestamp | None
    provenance: Provenance
    note: str = ""


class PointInTimeUnavailable(SextantError):
    """A historical question was asked that the available data cannot answer.

    Raised instead of returning a plausible-looking answer. A venue that cannot
    say which instruments it listed in 2022 must say so, because the alternative
    - quietly answering with the instruments that survived to today - is a
    survivorship-biased result that looks exactly like a correct one.

    ``remedy`` names the smallest additional data source that would resolve it,
    so the caller is handed the next step rather than a dead end.
    """

    def __init__(self, venue: Venue, at: Timestamp, missing: str, remedy: str) -> None:
        self.venue = venue
        self.at = at
        self.missing = missing
        self.remedy = remedy
        super().__init__(
            f"{venue.name} cannot answer a point-in-time question as of "
            f"{at.isoformat()}: {missing}. Smallest resolution: {remedy}"
        )

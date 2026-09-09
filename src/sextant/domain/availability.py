"""Runtime availability, kept strictly separate from capability.

A capability answers "may this ever happen?". Availability answers "can it
happen right now?". The first is semi-static configuration; the second is a
transient fact about the world that usually resolves itself within minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sextant.domain.errors import SextantError
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue


class VenueStatus(StrEnum):
    """How well a venue is currently responding."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VenueHealth:
    """A point-in-time observation of a venue's operational state."""

    venue: Venue
    status: VenueStatus
    observed_at: Timestamp
    detail: str = ""

    @property
    def is_usable(self) -> bool:
        """Whether work should be attempted against this venue right now."""
        return self.status in (VenueStatus.OPERATIONAL, VenueStatus.DEGRADED)


class VenueUnavailable(SextantError):
    """The venue is transiently unusable. Retryable.

    Never raise this for a missing capability, and never raise
    ``CapabilityNotAvailable`` for an outage. A caller distinguishes "you are
    not allowed to do this" from "try again shortly" purely by exception type.
    """

    retryable = True

    def __init__(self, venue: Venue, status: VenueStatus, detail: str = "") -> None:
        self.venue = venue
        self.status = status
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"{venue.name} is {status.value} and cannot be used right now{suffix}")

"""Venue identity.

A venue is an opaque, validated identifier. The domain deliberately does not
enumerate which venues exist: an enum of venue names is the first step towards
``if venue == ...`` branching, and towards a domain that has to be edited every
time a venue is added. Concrete venue names are declared by their adapter and
supplied by configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sextant.domain.errors import DomainError

_VENUE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class InvalidVenueName(DomainError):
    """A venue identifier did not match the required shape."""


@dataclass(frozen=True, slots=True, order=True)
class Venue:
    """The identity of a trading venue, as a lowercase slug."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _VENUE_PATTERN.match(self.name):
            raise InvalidVenueName(
                f"Venue name must match {_VENUE_PATTERN.pattern!r}, got {self.name!r}"
            )

    def __str__(self) -> str:
        return self.name

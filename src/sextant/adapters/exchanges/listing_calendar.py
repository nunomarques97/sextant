"""Which symbols a venue listed, when, and on whose authority.

No exchange in this project answers "which pairs did you list in March 2022?".
They answer "which pairs do I list now". A point-in-time universe therefore
needs a separate artifact, built once, audited, and carried alongside the venue
metadata: this one.

Two things it deliberately does *not* do.

It does not guess. An entry without a source is not written; a symbol with no
entry is absent from the answer rather than assumed to have always existed.

It does not hide what it cannot supply. A symbol we know was trading at some
instant, but for which no tick size, lot size or minimum notional survives, is
recorded as *known missing* rather than dropped. Dropping it would silently
shrink a historical universe to its survivors, which is precisely the bias the
calendar exists to expose. A caller that sees a non-empty ``missing_at`` knows
its universe for that date is incomplete, and by how much.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sextant.domain.provenance import ListingWindow, Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One symbol's trading life, and whether we can rebuild its constraints."""

    symbol: str
    window: ListingWindow
    metadata_available: bool
    """False when the venue no longer publishes tick size, lot size or minimum
    notional for this symbol - typically because it has been delisted and
    removed from the instrument endpoint entirely."""

    def was_listed_at(self, at: Timestamp) -> bool:
        """Whether this symbol was trading at ``at``."""
        if at < self.window.listed_at:
            return False
        return self.window.delisted_at is None or at < self.window.delisted_at


@dataclass(frozen=True, slots=True)
class ListingCalendar:
    """Every symbol a venue is known to have listed, with the source for each."""

    venue: Venue
    entries: Mapping[str, CalendarEntry]
    methodology: str
    """Prose stating exactly how these windows were established. Written into
    the report verbatim, so a reader can judge the claim rather than trust it."""

    @classmethod
    def of(
        cls,
        venue: Venue,
        entries: Iterable[CalendarEntry],
        methodology: str,
    ) -> ListingCalendar:
        """Build a calendar from any iterable of entries."""
        return cls(
            venue=venue,
            entries={entry.symbol: entry for entry in entries},
            methodology=methodology,
        )

    def entry_for(self, symbol: str) -> CalendarEntry | None:
        """The entry for ``symbol``, or None when nothing established one."""
        return self.entries.get(symbol)

    def symbols_at(self, at: Timestamp) -> frozenset[str]:
        """Symbols trading at ``at`` whose constraints we can still rebuild."""
        return frozenset(
            entry.symbol
            for entry in self.entries.values()
            if entry.metadata_available and entry.was_listed_at(at)
        )

    def missing_at(self, at: Timestamp) -> frozenset[str]:
        """Symbols known to have traded at ``at`` that we cannot rebuild.

        A non-empty result is a measured lower bound on the survivorship bias in
        any universe computed for ``at``.
        """
        return frozenset(
            entry.symbol
            for entry in self.entries.values()
            if not entry.metadata_available and entry.was_listed_at(at)
        )

    def provenance_counts(self) -> Mapping[Provenance, int]:
        """How many entries rest on each kind of evidence."""
        counts: dict[Provenance, int] = dict.fromkeys(Provenance, 0)
        for entry in self.entries.values():
            counts[entry.window.provenance] += 1
        return counts

    # -- persistence ---------------------------------------------------------

    def write_json(self, path: Path) -> int:
        """Persist the calendar. Returns the number of entries written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "venue": self.venue.name,
            "methodology": self.methodology,
            "entries": [
                {
                    "symbol": entry.symbol,
                    "listed_at": entry.window.listed_at.isoformat(),
                    "delisted_at": (
                        None
                        if entry.window.delisted_at is None
                        else entry.window.delisted_at.isoformat()
                    ),
                    "provenance": entry.window.provenance.value,
                    "note": entry.window.note,
                    "metadata_available": entry.metadata_available,
                }
                for entry in sorted(self.entries.values(), key=lambda item: item.symbol)
            ],
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return len(self.entries)

    @classmethod
    def read_json(cls, path: Path) -> ListingCalendar:
        """Load a calendar previously written by :meth:`write_json`."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.of(
            venue=Venue(str(raw["venue"])),
            entries=[
                CalendarEntry(
                    symbol=str(item["symbol"]),
                    window=ListingWindow(
                        listed_at=Timestamp.parse(str(item["listed_at"])),
                        delisted_at=(
                            None
                            if item["delisted_at"] is None
                            else Timestamp.parse(str(item["delisted_at"]))
                        ),
                        provenance=Provenance(str(item["provenance"])),
                        note=str(item.get("note", "")),
                    ),
                    metadata_available=bool(item["metadata_available"]),
                )
                for item in raw["entries"]
            ],
            methodology=str(raw["methodology"]),
        )

"""A point-in-time listing calendar sourced from monthly archive presence.

Every entry rests on one fact and one inference rule.

**The fact:** the venue publishes one object per symbol per month for the months
that symbol traded. Presence of the object is the evidence. Nothing here reads a
price, and nothing here looks at a gap in a series.

**The rule:** a symbol present in month *M* and absent in *M+1* stopped trading
strictly after the first boundary and at or before the second, recorded as the
interval ``(M.ends_at, M+1.ends_at]`` and never narrowed to a date. Listing is
the same rule with presence reversed. That rule is venue-neutral and is shared
with the other venue in
:mod:`sextant.adapters.exchanges.membership_intervals`; what lives here is which
months were held, and what this venue's grain means for the brackets.

The grain is the improvement. The other venue publishes quarterly, so its
delisting brackets are up to three months wide. This one publishes monthly, so
every bracket here is at most one month, and a strategy rebalancing monthly is
never asked to hold something whose membership is undetermined for a whole
quarter.

Because the evidence is the venue's own published archive, every entry carries
``Provenance.VENUE_ARCHIVE``. Nothing here is ``RECONSTRUCTED``: reconstruction
in this project means inferring an instant from the edge of a price series, and
that is exactly the method this module exists to avoid.

What it refuses to smooth over
-------------------------------

**The archive edges are unbounded, not pinned.** A symbol present in the
earliest held month may have listed earlier; present in the latest, it may
delist later or never. Pinning either would turn a fact about the download into
a fact about the venue - and the last held month is *always* suspect, because a
symbol absent from it cannot be distinguished from one whose file has not been
published yet. That is why the evaluation window stops a month short of the
archive's edge.

**A symbol may live more than once.** Present, absent, then present again is two
spells, not one.

**A hole in the download widens every bracket that spans it.** Brackets are
drawn against the months actually held.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sextant.adapters.exchanges.binance.archive import Month
from sextant.adapters.exchanges.membership_intervals import spells_from_presence
from sextant.domain.listing import EventInterval, IntervalListingWindow, MembershipState
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

METHODOLOGY = (
    "Membership is the set of symbols for which the venue publishes a monthly spot kline "
    "object, which it does for the months a symbol traded. A symbol present in month M and "
    "absent in M+1 was delisted during M+1, recorded as the interval (M.ends_at, M+1.ends_at] "
    "and never as a date. Listing is the same rule with presence reversed. A symbol present, "
    "absent, then present again is recorded as two spells rather than one. Symbols present in "
    "the earliest held month are unbounded before it; symbols present in the latest are "
    "unbounded after it, and the evaluation window therefore stops one month short of the "
    "archive's edge because absence from the final published month is indistinguishable from "
    "a file not yet published. A day on which a listed symbol did not trade appears as a bar "
    "carrying zero volume rather than as a missing file. No instant here is inferred from a "
    "price series."
)


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One symbol's trading life, as one or more spells with bracketed ends."""

    symbol: str
    spells: tuple[IntervalListingWindow, ...]
    provenance: Provenance
    first_seen: Month
    last_seen: Month
    published_months: int

    def __post_init__(self) -> None:
        if not self.spells:
            raise ValueError(f"{self.symbol}: a calendar entry needs at least one spell")

    @property
    def listed_during(self) -> EventInterval:
        """The bracket in which this symbol first started trading."""
        return self.spells[0].listed_during

    @property
    def delisted_during(self) -> EventInterval:
        """The bracket in which it last stopped, open if it has not."""
        return self.spells[-1].delisted_during

    @property
    def was_relisted(self) -> bool:
        """Whether the archive shows this symbol leaving and returning."""
        return len(self.spells) > 1

    def membership_at(self, at: Timestamp) -> MembershipState:
        """Whether this symbol was tradable at ``at``, allowing for ignorance.

        Certainty in any spell wins over ignorance in the others: a symbol
        firmly inside one spell is listed regardless of how fuzzy the edges of
        another are.
        """
        states = [spell.membership_at(at) for spell in self.spells]
        if MembershipState.LISTED in states:
            return MembershipState.LISTED
        if MembershipState.UNDETERMINED in states:
            return MembershipState.UNDETERMINED
        return MembershipState.NOT_LISTED


@dataclass(frozen=True, slots=True)
class BinanceListingCalendar:
    """Every symbol the archive shows, with the bracket around each change."""

    venue: Venue
    entries: Mapping[str, CalendarEntry]
    months: tuple[Month, ...]
    missing_months: tuple[Month, ...]
    methodology: str = METHODOLOGY

    # -- construction --------------------------------------------------------

    @classmethod
    def from_presence(
        cls,
        venue: Venue,
        presence: Mapping[str, Sequence[bool]],
        held: Sequence[Month],
        *,
        missing_months: Iterable[Month] = (),
    ) -> BinanceListingCalendar:
        """Diff each symbol's monthly presence flags into bracketed spells."""
        ordered = tuple(sorted(held))
        entries: list[CalendarEntry] = []
        for symbol in sorted(presence):
            flags = tuple(presence[symbol])
            if not any(flags):
                continue
            appearances = [month for month, seen in zip(ordered, flags, strict=True) if seen]
            entries.append(
                CalendarEntry(
                    symbol=symbol,
                    spells=spells_from_presence(flags, ordered),
                    provenance=Provenance.VENUE_ARCHIVE,
                    first_seen=appearances[0],
                    last_seen=appearances[-1],
                    published_months=len(appearances),
                )
            )
        return cls(
            venue=venue,
            entries={entry.symbol: entry for entry in entries},
            months=ordered,
            missing_months=tuple(sorted(missing_months)),
        )

    # -- queries -------------------------------------------------------------

    def entry_for(self, symbol: str) -> CalendarEntry | None:
        """The entry for ``symbol``, or None when the archive never carried it."""
        return self.entries.get(symbol)

    def membership_at(self, symbol: str, at: Timestamp) -> MembershipState:
        """Whether ``symbol`` was tradable at ``at``.

        A symbol the archive never mentions is ``UNDETERMINED``, not
        ``NOT_LISTED``. Absence from what we hold is a fact about the download
        whenever the download is incomplete, and saying otherwise would let a
        missing month delete symbols from history.
        """
        entry = self.entries.get(symbol)
        if entry is None:
            return MembershipState.UNDETERMINED
        return entry.membership_at(at)

    def symbols_at(self, at: Timestamp) -> frozenset[str]:
        """Symbols certainly listed at ``at``."""
        return self._with_state(at, MembershipState.LISTED)

    def undetermined_at(self, at: Timestamp) -> frozenset[str]:
        """Symbols whose membership at ``at`` falls inside a bracket.

        A non-empty result is the measured width of what this archive cannot
        resolve at that instant. It is reported, never rounded to zero.
        """
        return self._with_state(at, MembershipState.UNDETERMINED)

    def _with_state(self, at: Timestamp, state: MembershipState) -> frozenset[str]:
        return frozenset(
            symbol for symbol, entry in self.entries.items() if entry.membership_at(at) is state
        )

    def delisted_during(self, month: Month) -> frozenset[str]:
        """Symbols whose delisting bracket is exactly ``month``.

        A bracket unbounded before is excluded: it belongs to a symbol we simply
        stopped seeing at the edge of the archive.
        """
        return frozenset(
            symbol
            for symbol, entry in self.entries.items()
            for spell in entry.spells
            if spell.delisted_during.until == month.ends_at
            and spell.delisted_during.after is not None
        )

    def relisted_symbols(self) -> frozenset[str]:
        """Every symbol the archive shows leaving and returning."""
        return frozenset(symbol for symbol, entry in self.entries.items() if entry.was_relisted)

    def provenance_counts(self) -> Mapping[Provenance, int]:
        """How many entries rest on each kind of evidence."""
        counts: dict[Provenance, int] = dict.fromkeys(Provenance, 0)
        for entry in self.entries.values():
            counts[entry.provenance] += 1
        return counts

    # -- persistence ---------------------------------------------------------

    def write_json(self, path: Path) -> int:
        """Persist the calendar. Returns the number of entries written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "venue": self.venue.name,
            "methodology": self.methodology,
            "months": [month.label for month in self.months],
            "missing_months": [month.label for month in self.missing_months],
            "entries": [
                {
                    "symbol": entry.symbol,
                    "provenance": entry.provenance.value,
                    "first_seen": entry.first_seen.label,
                    "last_seen": entry.last_seen.label,
                    "published_months": entry.published_months,
                    "spells": [
                        {
                            "listed_after": _iso(spell.listed_during.after),
                            "listed_until": _iso(spell.listed_during.until),
                            "delisted_after": _iso(spell.delisted_during.after),
                            "delisted_until": _iso(spell.delisted_during.until),
                        }
                        for spell in entry.spells
                    ],
                }
                for entry in sorted(self.entries.values(), key=_by_symbol)
            ],
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return len(self.entries)

    @classmethod
    def read_json(cls, path: Path) -> BinanceListingCalendar:
        """Load a calendar previously written by :meth:`write_json`."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        entries = [
            CalendarEntry(
                symbol=str(item["symbol"]),
                spells=tuple(
                    IntervalListingWindow(
                        listed_during=EventInterval(
                            after=_instant(spell["listed_after"]),
                            until=_instant(spell["listed_until"]),
                        ),
                        delisted_during=EventInterval(
                            after=_instant(spell["delisted_after"]),
                            until=_instant(spell["delisted_until"]),
                        ),
                    )
                    for spell in item["spells"]
                ),
                provenance=Provenance(str(item["provenance"])),
                first_seen=Month.parse(str(item["first_seen"])),
                last_seen=Month.parse(str(item["last_seen"])),
                published_months=int(item["published_months"]),
            )
            for item in raw["entries"]
        ]
        return cls(
            venue=Venue(str(raw["venue"])),
            entries={entry.symbol: entry for entry in sorted(entries, key=_by_symbol)},
            months=tuple(Month.parse(str(label)) for label in raw["months"]),
            missing_months=tuple(Month.parse(str(label)) for label in raw["missing_months"]),
            methodology=str(raw["methodology"]),
        )


def _by_symbol(entry: CalendarEntry) -> str:
    return entry.symbol


def _iso(instant: Timestamp | None) -> str | None:
    return None if instant is None else instant.isoformat()


def _instant(raw: object) -> Timestamp | None:
    return None if raw is None else Timestamp.parse(str(raw))

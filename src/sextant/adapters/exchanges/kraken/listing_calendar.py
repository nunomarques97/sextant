"""A point-in-time listing calendar sourced from quarterly archive membership.

Every entry here rests on one fact and one inference rule.

**The fact:** each quarterly archive contains the pairs that were listed at the
end of that quarter. Presence of a file is the evidence. Nothing in this module
reads a price, and nothing in it looks at a gap in a series.

**The rule:** a pair present at the end of quarter *N* and absent at the end of
quarter *N+1* stopped trading strictly after the first boundary and at or before
the second. That is recorded as the interval ``(N.ends_at, N+1.ends_at]``, and
it is never narrowed to a date. Listing is the same rule with the presence
reversed.

Because the evidence is the venue's own published archive, every entry carries
``Provenance.VENUE_ARCHIVE``. Nothing here is ``RECONSTRUCTED``: reconstruction
in this project means inferring an instant from the edge of a price series, and
that is exactly the method this module exists to avoid.

Four things the calendar refuses to smooth over
-----------------------------------------------

**The archive edges are unbounded, not pinned.** A pair present in the earliest
quarter we hold may have listed at any earlier time, so its listing bracket is
open into the past. A pair present in the latest quarter may delist later or
never, so its delisting bracket is open into the future. Pinning either to the
edge of the archive would turn a fact about our download into a fact about the
venue.

**A pair may live more than once.** A pair present, then absent, then present
again was delisted and relisted, and that is two spells rather than one long
one. Collapsing it into a single window would report the pair as tradable
throughout a period the archive says it was gone.

**A gap in the held quarters widens every interval that spans it.** The
manifest's missing list travels into the calendar, so a caller can see which
brackets are wide because of the download rather than because of the venue.

**Listed-and-untraded is its own state.** A pair present with zero rows was
listed and did not trade. It stays a member, and it stays distinguishable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sextant.adapters.exchanges.kraken.archive import PairPresence, Quarter
from sextant.domain.listing import EventInterval, IntervalListingWindow, MembershipState
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

METHODOLOGY = (
    "Membership is the set of pairs present in each quarterly OHLCVT archive, which "
    "the venue populates with the pairs listed at the end of that quarter. A pair "
    "present in quarter N and absent in N+1 was delisted during N+1, recorded as the "
    "interval (N.ends_at, N+1.ends_at] and never as a date. Listing is the same rule "
    "with presence reversed. A pair that is present, absent, then present again is "
    "recorded as two spells rather than one. Pairs present in the earliest held "
    "quarter are unbounded before it; pairs present in the latest are unbounded after "
    "it. A pair present with zero rows at every granularity was listed and untraded, "
    "which is a distinct state from absent. No instant here is inferred from a price "
    "series."
)


@dataclass(frozen=True, slots=True)
class QuarterlyMembership:
    """One quarter's membership snapshot, as read from file presence."""

    quarter: Quarter
    presence: Mapping[str, PairPresence]

    @property
    def symbols(self) -> frozenset[str]:
        """Every pair listed at the end of this quarter, traded or not."""
        return frozenset(symbol for symbol, state in self.presence.items() if state.is_listed)

    @property
    def untraded(self) -> frozenset[str]:
        """Pairs listed at the end of this quarter that recorded no trades in it."""
        return frozenset(
            symbol
            for symbol, state in self.presence.items()
            if state is PairPresence.LISTED_UNTRADED
        )


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One pair's trading life, as one or more spells with bracketed ends."""

    symbol: str
    spells: tuple[IntervalListingWindow, ...]
    provenance: Provenance
    first_seen: Quarter
    last_seen: Quarter
    untraded_quarters: tuple[str, ...]
    """Quarters in which the pair was listed and recorded no trades at all.

    Not a data gap. The venue shipped the files and they hold zero rows, which
    is the venue saying the pair was listed and nothing changed hands."""

    def __post_init__(self) -> None:
        if not self.spells:
            raise ValueError(f"{self.symbol}: a calendar entry needs at least one spell")

    @property
    def listed_during(self) -> EventInterval:
        """The bracket in which this pair first started trading."""
        return self.spells[0].listed_during

    @property
    def delisted_during(self) -> EventInterval:
        """The bracket in which this pair last stopped trading, open if it has not."""
        return self.spells[-1].delisted_during

    @property
    def was_relisted(self) -> bool:
        """Whether the archive shows this pair leaving and returning."""
        return len(self.spells) > 1

    @property
    def was_ever_untraded(self) -> bool:
        """Whether any held quarter shows this pair listed with no trades."""
        return bool(self.untraded_quarters)

    def membership_at(self, at: Timestamp) -> MembershipState:
        """Whether this pair was tradable at ``at``, allowing for ignorance.

        Certainty in any spell wins over ignorance in the others: a pair firmly
        inside one spell is listed regardless of how fuzzy the edges of another
        are.
        """
        states = [spell.membership_at(at) for spell in self.spells]
        if MembershipState.LISTED in states:
            return MembershipState.LISTED
        if MembershipState.UNDETERMINED in states:
            return MembershipState.UNDETERMINED
        return MembershipState.NOT_LISTED


@dataclass(frozen=True, slots=True)
class KrakenListingCalendar:
    """Every pair the archives show, with the bracket around each state change."""

    venue: Venue
    entries: Mapping[str, CalendarEntry]
    quarters: tuple[Quarter, ...]
    missing_quarters: tuple[Quarter, ...]
    methodology: str = METHODOLOGY

    # -- construction --------------------------------------------------------

    @classmethod
    def from_membership(
        cls,
        venue: Venue,
        snapshots: Sequence[QuarterlyMembership],
        *,
        missing_quarters: Iterable[Quarter] = (),
    ) -> KrakenListingCalendar:
        """Diff consecutive membership snapshots into intervals.

        Snapshots are sorted rather than trusted in the order given: archives
        read out of order would otherwise produce brackets that run backwards,
        and a backwards bracket is rejected by the domain type rather than
        quietly stored.
        """
        ordered = sorted(snapshots, key=lambda snapshot: snapshot.quarter)
        held = tuple(snapshot.quarter for snapshot in ordered)
        entries = [
            _entry_for(symbol, ordered, held)
            for symbol in sorted({symbol for snapshot in ordered for symbol in snapshot.symbols})
        ]
        return cls(
            venue=venue,
            entries={entry.symbol: entry for entry in entries},
            quarters=held,
            missing_quarters=tuple(sorted(missing_quarters)),
        )

    # -- queries -------------------------------------------------------------

    def entry_for(self, symbol: str) -> CalendarEntry | None:
        """The entry for ``symbol``, or None when the archives never carried it."""
        return self.entries.get(symbol)

    def membership_at(self, symbol: str, at: Timestamp) -> MembershipState:
        """Whether ``symbol`` was tradable at ``at``.

        A symbol the archives never mention is ``UNDETERMINED``, not
        ``NOT_LISTED``. Absence from what we hold is a fact about the download
        whenever the download is incomplete, and saying otherwise would let a
        missing quarter delete pairs from history.
        """
        entry = self.entries.get(symbol)
        if entry is None:
            return MembershipState.UNDETERMINED
        return entry.membership_at(at)

    def symbols_at(self, at: Timestamp) -> frozenset[str]:
        """Pairs certainly listed at ``at``."""
        return self._with_state(at, MembershipState.LISTED)

    def undetermined_at(self, at: Timestamp) -> frozenset[str]:
        """Pairs whose membership at ``at`` falls inside a bracket.

        A non-empty result is the measured width of what this archive cannot
        resolve at that instant. It is reported, never rounded to zero.
        """
        return self._with_state(at, MembershipState.UNDETERMINED)

    def _with_state(self, at: Timestamp, state: MembershipState) -> frozenset[str]:
        return frozenset(
            symbol for symbol, entry in self.entries.items() if entry.membership_at(at) is state
        )

    def delisted_during(self, quarter: Quarter) -> frozenset[str]:
        """Pairs whose delisting bracket is exactly ``quarter``.

        A bracket unbounded before is excluded: it belongs to a pair we simply
        stopped seeing at the edge of the archive.
        """
        return frozenset(
            symbol
            for symbol, entry in self.entries.items()
            for spell in entry.spells
            if spell.delisted_during.until == quarter.ends_at
            and spell.delisted_during.after is not None
        )

    def listed_during(self, quarter: Quarter) -> frozenset[str]:
        """Pairs whose listing bracket is exactly ``quarter``.

        Excludes pairs present in the earliest held quarter, whose bracket is
        open into the past and therefore says nothing about when they listed.
        """
        return frozenset(
            symbol
            for symbol, entry in self.entries.items()
            for spell in entry.spells
            if spell.listed_during.until == quarter.ends_at
            and spell.listed_during.after is not None
        )

    def untraded_symbols(self) -> frozenset[str]:
        """Every pair seen listed with no trades in at least one held quarter."""
        return frozenset(
            symbol for symbol, entry in self.entries.items() if entry.was_ever_untraded
        )

    def relisted_symbols(self) -> frozenset[str]:
        """Every pair the archive shows leaving and returning."""
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
            "quarters": [quarter.label for quarter in self.quarters],
            "missing_quarters": [quarter.label for quarter in self.missing_quarters],
            "entries": [
                {
                    "symbol": entry.symbol,
                    "provenance": entry.provenance.value,
                    "first_seen": entry.first_seen.label,
                    "last_seen": entry.last_seen.label,
                    "untraded_quarters": list(entry.untraded_quarters),
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
    def read_json(cls, path: Path) -> KrakenListingCalendar:
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
                first_seen=Quarter.parse(str(item["first_seen"])),
                last_seen=Quarter.parse(str(item["last_seen"])),
                untraded_quarters=tuple(str(label) for label in item["untraded_quarters"]),
            )
            for item in raw["entries"]
        ]
        return cls(
            venue=Venue(str(raw["venue"])),
            entries={entry.symbol: entry for entry in sorted(entries, key=_by_symbol)},
            quarters=tuple(Quarter.parse(str(label)) for label in raw["quarters"]),
            missing_quarters=tuple(Quarter.parse(str(label)) for label in raw["missing_quarters"]),
            methodology=str(raw["methodology"]),
        )


def _entry_for(
    symbol: str,
    ordered: Sequence[QuarterlyMembership],
    held: Sequence[Quarter],
) -> CalendarEntry:
    """Build one entry by walking the held quarters and cutting spells at gaps.

    Every bracket is drawn against the *held* quarters, so a hole in the
    download widens the bracket rather than being papered over. Where a pair is
    present in the earliest or latest held quarter, that side of the bracket is
    unbounded instead of being pinned to the archive's edge.
    """
    present = [symbol in snapshot.symbols for snapshot in ordered]
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

    appearances = [snapshot for snapshot in ordered if symbol in snapshot.symbols]
    return CalendarEntry(
        symbol=symbol,
        spells=tuple(spells),
        provenance=Provenance.VENUE_ARCHIVE,
        first_seen=appearances[0].quarter,
        last_seen=appearances[-1].quarter,
        untraded_quarters=tuple(
            snapshot.quarter.label for snapshot in appearances if symbol in snapshot.untraded
        ),
    )


def _by_symbol(entry: CalendarEntry) -> str:
    return entry.symbol


def _iso(instant: Timestamp | None) -> str | None:
    return None if instant is None else instant.isoformat()


def _instant(raw: object) -> Timestamp | None:
    return None if raw is None else Timestamp.parse(str(raw))

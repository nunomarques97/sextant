"""Our own record of which pairs a venue listed, on the days we looked.

This is the permanent fix for the problem SEXTANT-003 exists to work around.
The venue's quarterly archive ends at Q1 2026 and it is now September 2026, so
the intervening months have no delisting record anywhere and no remedy: a pair
that listed and died inside that window left no trace we can obtain. That window
carries survivorship bias, it cannot be repaired, and every result covering it
has to say so.

From the first run of ``sextant snapshot-universe`` onwards it stops getting
worse. Each snapshot is a dated membership record appended to the store, and a
diff between two of them is a listing calendar of exactly the same grade as the
one built from the archives - a bracket, sourced from presence, never a date
inferred from a price.

Idempotent per day on purpose. Running it three times on a Tuesday must leave
one Tuesday, or the record of how often we looked becomes a record of how often
someone ran a command.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow
import pyarrow.parquet as parquet

from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

#: The on-disk schema for one day's membership.
SNAPSHOT_SCHEMA = pyarrow.schema(
    [
        pyarrow.field("symbol", pyarrow.string(), nullable=False),
        pyarrow.field("base", pyarrow.string(), nullable=False),
        pyarrow.field("quote", pyarrow.string(), nullable=False),
        pyarrow.field("status", pyarrow.string(), nullable=False),
        pyarrow.field("jurisdiction_visible", pyarrow.bool_(), nullable=False),
    ]
)

_COMPRESSION = "zstd"


@dataclass(frozen=True, slots=True)
class MembershipRow:
    """One pair as the venue described it on one day.

    ``jurisdiction_visible`` is the venue's own answer to whether the account's
    country may trade this pair, taken from the same endpoint filtered by
    country code. That is the jurisdiction layer sourced from the venue rather
    than guessed at in our configuration, and the difference between the two
    counts is worth keeping.
    """

    symbol: str
    base: str
    quote: str
    status: str
    jurisdiction_visible: bool


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """Everything one venue listed on one day."""

    venue: Venue
    observed_on: date
    rows: tuple[MembershipRow, ...]

    @classmethod
    def of(
        cls, venue: Venue, observed_at: Timestamp, rows: Iterable[MembershipRow]
    ) -> UniverseSnapshot:
        """Build a snapshot dated by the UTC day of ``observed_at``."""
        return cls(
            venue=venue,
            observed_on=observed_at.value.date(),
            rows=tuple(sorted(rows, key=lambda row: row.symbol)),
        )

    @property
    def symbols(self) -> frozenset[str]:
        """Every pair listed on this day."""
        return frozenset(row.symbol for row in self.rows)

    @property
    def jurisdiction_visible(self) -> frozenset[str]:
        """The pairs the venue says the account's country may trade."""
        return frozenset(row.symbol for row in self.rows if row.jurisdiction_visible)


class MembershipStore:
    """Dated membership snapshots, one parquet file per venue per day."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, venue: Venue, observed_on: date) -> Path:
        """Where one day's snapshot lives."""
        return (
            self.root / "membership" / f"venue={venue.name}" / f"{observed_on.isoformat()}.parquet"
        )

    def has_snapshot(self, venue: Venue, observed_on: date) -> bool:
        """Whether this day has already been recorded."""
        return self.path_for(venue, observed_on).is_file()

    def days(self, venue: Venue) -> tuple[date, ...]:
        """Every day recorded for one venue, in order."""
        directory = self.root / "membership" / f"venue={venue.name}"
        if not directory.is_dir():
            return ()
        return tuple(sorted(date.fromisoformat(path.stem) for path in directory.glob("*.parquet")))

    def append(self, snapshot: UniverseSnapshot, *, overwrite: bool = False) -> int:
        """Record one day. Returns rows written, or -1 when the day already exists.

        Idempotent per day: a second run on the same UTC day is a no-op unless
        ``overwrite`` is asked for explicitly. Silently rewriting would let a
        re-run at 23:59 replace what we saw at 09:00 with no record that the
        membership had changed in between.
        """
        target = self.path_for(snapshot.venue, snapshot.observed_on)
        if target.is_file() and not overwrite:
            return -1
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pyarrow.table(
            {
                "symbol": [row.symbol for row in snapshot.rows],
                "base": [row.base for row in snapshot.rows],
                "quote": [row.quote for row in snapshot.rows],
                "status": [row.status for row in snapshot.rows],
                "jurisdiction_visible": [row.jurisdiction_visible for row in snapshot.rows],
            },
            schema=SNAPSHOT_SCHEMA,
        )
        parquet.write_table(table, target, compression=_COMPRESSION)
        return len(snapshot.rows)

    def read(self, venue: Venue, observed_on: date) -> UniverseSnapshot:
        """Read one day's snapshot back."""
        target = self.path_for(venue, observed_on)
        if not target.is_file():
            raise FileNotFoundError(f"No membership snapshot for {venue.name} on {observed_on}")
        columns = parquet.read_table(target, schema=SNAPSHOT_SCHEMA).to_pydict()
        return UniverseSnapshot(
            venue=venue,
            observed_on=observed_on,
            rows=tuple(
                MembershipRow(
                    symbol=str(symbol),
                    base=str(base),
                    quote=str(quote),
                    status=str(status),
                    jurisdiction_visible=bool(visible),
                )
                for symbol, base, quote, status, visible in zip(
                    columns["symbol"],
                    columns["base"],
                    columns["quote"],
                    columns["status"],
                    columns["jurisdiction_visible"],
                    strict=True,
                )
            ),
        )

    def diff(self, venue: Venue, earlier: date, later: date) -> MembershipDiff:
        """What changed between two recorded days.

        The same evidence grade as the archive calendar: presence and absence in
        two dated observations, bracketing each state change between them. No
        price is consulted and no date is invented.
        """
        before = self.read(venue, earlier)
        after = self.read(venue, later)
        return MembershipDiff(
            venue=venue,
            earlier=earlier,
            later=later,
            delisted=tuple(sorted(before.symbols - after.symbols)),
            listed=tuple(sorted(after.symbols - before.symbols)),
        )

    def write_index(self, venue: Venue, path: Path) -> int:
        """Write a human-readable index of what has been recorded so far."""
        recorded = self.days(venue)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "venue": venue.name,
            "days": [day.isoformat() for day in recorded],
            "first": recorded[0].isoformat() if recorded else None,
            "last": recorded[-1].isoformat() if recorded else None,
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return len(recorded)


@dataclass(frozen=True, slots=True)
class MembershipDiff:
    """The state changes bracketed by two dated observations."""

    venue: Venue
    earlier: date
    later: date
    delisted: Sequence[str]
    listed: Sequence[str]

    def as_json(self) -> Mapping[str, object]:
        """Serialisable form."""
        return {
            "venue": self.venue.name,
            "earlier": self.earlier.isoformat(),
            "later": self.later.isoformat(),
            "delisted_between": list(self.delisted),
            "listed_between": list(self.listed),
        }

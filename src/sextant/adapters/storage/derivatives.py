"""Storage for the two derivative series a perpetual has and spot does not.

**Funding settlements.** One row per payment: the instant it was made, the
interval it covered, and the rate, as exact decimal text. Not bars. A funding
payment is an event at an instant, not an aggregation over a period, and forcing
it into the bar schema would invent an open, a high, a low and a volume that do
not exist.

**The premium index.** One row per day: the perpetual expressed against the
venue's own index, which is the basis. Stored apart from bars for one specific
reason - it goes negative, routinely, and ``StoredBar.close_price`` returns a
:class:`~sextant.domain.money.Price`, which refuses a negative amount. A
perpetual trading below its index is the ordinary state of a falling market, not
an error, so it gets a type that can express it.

Text in, text out, exactly as for bars: a rate is stored as the characters the
venue published and parsed into ``Decimal`` by the layer that is allowed to hold
money. ADR 0005 is the reason, and it applies with more force here than to
prices - a funding rate is five significant figures of a very small number, and
a float would lose the last of them on the way in.

Writes are idempotent. A series is de-duplicated on its instant, sorted, and
written whole to a deterministic path, so ingesting twice produces the same
store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pyarrow
import pyarrow.parquet as parquet

from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

#: One funding payment. Declared rather than inferred; a shape change is a diff.
FUNDING_SCHEMA = pyarrow.schema(
    [
        pyarrow.field("settled_at_ms", pyarrow.int64(), nullable=False),
        pyarrow.field("interval_hours", pyarrow.int32(), nullable=False),
        pyarrow.field("rate", pyarrow.string(), nullable=False),
    ]
)

#: One daily premium-index close. Signed.
PREMIUM_SCHEMA = pyarrow.schema(
    [
        pyarrow.field("open_time_ms", pyarrow.int64(), nullable=False),
        pyarrow.field("close", pyarrow.string(), nullable=False),
    ]
)

_COMPRESSION = "zstd"


@dataclass(frozen=True, slots=True)
class StoredFunding:
    """One funding settlement as the store holds it."""

    settled_at: Timestamp
    interval_hours: int
    rate: str

    @property
    def as_decimal(self) -> Decimal:
        """The rate, exact from its text."""
        return Decimal(self.rate)


@dataclass(frozen=True, slots=True)
class StoredPremium:
    """One daily premium-index close as the store holds it. Signed."""

    open_time: Timestamp
    close: str

    @property
    def as_decimal(self) -> Decimal:
        """The premium, exact from its text. Negative is ordinary."""
        return Decimal(self.close)


class _SeriesStore:
    """Shared addressing and idempotence for the two series families."""

    family: str = ""
    schema: pyarrow.Schema = FUNDING_SCHEMA

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, venue: Venue, symbol: str) -> Path:
        """Where one series lives. Deterministic, so a rewrite is an overwrite."""
        return self.root / self.family / f"venue={venue.name}" / f"{symbol}.parquet"

    def has_series(self, venue: Venue, symbol: str) -> bool:
        """Whether this series has been written."""
        return self.path_for(venue, symbol).is_file()

    def symbols(self, venue: Venue) -> frozenset[str]:
        """Every symbol stored for one venue in this family."""
        directory = self.root / self.family / f"venue={venue.name}"
        if not directory.is_dir():
            return frozenset()
        return frozenset(path.stem for path in directory.glob("*.parquet"))

    def _write(self, venue: Venue, symbol: str, columns: Mapping[str, list[object]]) -> int:
        target = self.path_for(venue, symbol)
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pyarrow.table(dict(columns), schema=self.schema)
        parquet.write_table(table, target, compression=_COMPRESSION)
        return int(table.num_rows)


class FundingStore(_SeriesStore):
    """Funding settlements on disk, one file per venue and symbol."""

    family = "funding"
    schema = FUNDING_SCHEMA

    def write_series(self, venue: Venue, symbol: str, rows: Iterable[StoredFunding]) -> int:
        """Write one whole series, replacing whatever was there.

        De-duplicated on the settlement instant. The venue stamps some
        settlements a millisecond late and republishes a boundary settlement in
        two monthly objects, so without this an account could be charged twice
        for one payment.
        """
        by_instant: dict[int, StoredFunding] = {}
        for row in rows:
            by_instant[row.settled_at.epoch_millis] = row
        ordered = [by_instant[key] for key in sorted(by_instant)]
        return self._write(
            venue,
            symbol,
            {
                "settled_at_ms": [row.settled_at.epoch_millis for row in ordered],
                "interval_hours": [row.interval_hours for row in ordered],
                "rate": [row.rate for row in ordered],
            },
        )

    def read_series(self, venue: Venue, symbol: str) -> tuple[StoredFunding, ...]:
        """Every settlement for one symbol, ascending.

        An absent file raises. An empty file is a symbol whose funding history
        the archive holds and which contains no payment, which is a different
        fact and is returned as an empty tuple.
        """
        target = self.path_for(venue, symbol)
        if not target.is_file():
            raise FileNotFoundError(
                f"No funding series for {venue.name}:{symbol}. An absent series and an "
                "empty one are different facts; check has_series() before reading."
            )
        columns = parquet.read_table(target, schema=FUNDING_SCHEMA).to_pydict()
        return tuple(
            StoredFunding(
                settled_at=Timestamp.from_epoch_millis(int(settled)),
                interval_hours=int(interval),
                rate=str(rate),
            )
            for settled, interval, rate in zip(
                columns["settled_at_ms"],
                columns["interval_hours"],
                columns["rate"],
                strict=True,
            )
        )


class PremiumStore(_SeriesStore):
    """Daily premium-index closes on disk, one file per venue and symbol."""

    family = "premium"
    schema = PREMIUM_SCHEMA

    def write_series(self, venue: Venue, symbol: str, rows: Iterable[StoredPremium]) -> int:
        """Write one whole series, replacing whatever was there."""
        by_instant: dict[int, StoredPremium] = {}
        for row in rows:
            by_instant[row.open_time.epoch_millis] = row
        ordered = [by_instant[key] for key in sorted(by_instant)]
        return self._write(
            venue,
            symbol,
            {
                "open_time_ms": [row.open_time.epoch_millis for row in ordered],
                "close": [row.close for row in ordered],
            },
        )

    def read_series(self, venue: Venue, symbol: str) -> tuple[StoredPremium, ...]:
        """Every daily premium close for one symbol, ascending."""
        target = self.path_for(venue, symbol)
        if not target.is_file():
            raise FileNotFoundError(
                f"No premium series for {venue.name}:{symbol}. An absent series and an "
                "empty one are different facts; check has_series() before reading."
            )
        columns = parquet.read_table(target, schema=PREMIUM_SCHEMA).to_pydict()
        return tuple(
            StoredPremium(
                open_time=Timestamp.from_epoch_millis(int(open_ms)),
                close=str(close),
            )
            for open_ms, close in zip(columns["open_time_ms"], columns["close"], strict=True)
        )

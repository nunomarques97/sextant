"""The bar store: parquet on disk, duckdb over the top.

One file per ``(venue, symbol, timeframe)``, which is both the key the store is
addressed by and the unit every reader actually wants. A universe measurement
reads whole series; nothing in this system reads one bar.

**Prices are stored as text.** Parquet's ``DECIMAL`` carries a fixed precision
and scale, and the Kraken archive mixes scales freely - ``1E+1`` appears
verbatim in ``ANTEUR_60.csv``. ``Decimal(str(...))`` round-trips exactly for
every value a venue can emit, whereas a precision chosen today silently
truncates a value we have not met yet. Floats are not an option at all: invariant
4 makes a float price a defect. See docs/adr/0005.

**Writes are idempotent.** A series is sorted by open time, de-duplicated on it,
and written whole to a deterministic path. Running an ingest twice produces the
same store, and a test asserts it rather than the docstring promising it.

**Empty is stored, not skipped.** A pair the venue listed and nobody traded has
a real, zero-row series. Writing no file would make it indistinguishable from a
pair the venue never listed, and those two facts are the whole reason the
listing calendar exists.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow
import pyarrow.parquet as parquet

from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

#: The on-disk schema. Declared rather than inferred, so a shape change is a
#: diff in this file instead of a surprise three modules downstream.
BAR_SCHEMA = pyarrow.schema(
    [
        pyarrow.field("open_time_ms", pyarrow.int64(), nullable=False),
        pyarrow.field("open", pyarrow.string(), nullable=False),
        pyarrow.field("high", pyarrow.string(), nullable=False),
        pyarrow.field("low", pyarrow.string(), nullable=False),
        pyarrow.field("close", pyarrow.string(), nullable=False),
        pyarrow.field("volume", pyarrow.string(), nullable=False),
        pyarrow.field("trades", pyarrow.int64(), nullable=False),
        pyarrow.field("is_closed", pyarrow.bool_(), nullable=False),
    ]
)

_COMPRESSION = "zstd"


@dataclass(frozen=True, slots=True)
class StoredBar:
    """One bar as the store holds it, with exact decimal text and its closed flag.

    Deliberately not ``domain.market_data.Bar``: that type carries a whole
    ``Instrument``, which would mean resolving a listing window before a row
    could be written. Storage should not need to know when a pair listed.
    """

    open_time: Timestamp
    open: str
    high: str
    low: str
    close: str
    volume: str
    trades: int
    is_closed: bool

    @property
    def close_price(self) -> Price:
        """The close as an exact monetary value."""
        return Price(Decimal(self.close))

    @property
    def base_volume(self) -> Quantity:
        """Turnover in the base asset."""
        return Quantity(Decimal(self.volume))

    @property
    def quote_volume(self) -> Notional:
        """Turnover in the quote asset, as close times base volume.

        An approximation, and named as one wherever it is reported. The archive
        publishes no vwap, so the exact turnover the venue's own OHLC endpoint
        can supply is not recoverable from these files.
        """
        return Notional(Decimal(self.close) * Decimal(self.volume))


@dataclass(frozen=True, slots=True)
class SeriesKey:
    """What one stored series is addressed by."""

    venue: Venue
    symbol: str
    timeframe: Timeframe

    def __str__(self) -> str:
        return f"{self.venue.name}:{self.symbol}:{self.timeframe.value}"


class ParquetBarStore:
    """Bars on disk, keyed by venue, symbol and timeframe."""

    def __init__(self, root: Path) -> None:
        self.root = root

    # -- addressing ----------------------------------------------------------

    def path_for(self, key: SeriesKey) -> Path:
        """Where one series lives. Deterministic, so a rewrite is an overwrite."""
        return (
            self.root
            / "bars"
            / f"venue={key.venue.name}"
            / f"timeframe={key.timeframe.value}"
            / f"{key.symbol}.parquet"
        )

    def has_series(self, key: SeriesKey) -> bool:
        """Whether this series has been written."""
        return self.path_for(key).is_file()

    def symbols(self, venue: Venue, timeframe: Timeframe) -> frozenset[str]:
        """Every symbol stored for one venue and timeframe."""
        directory = self.root / "bars" / f"venue={venue.name}" / f"timeframe={timeframe.value}"
        if not directory.is_dir():
            return frozenset()
        return frozenset(path.stem for path in directory.glob("*.parquet"))

    # -- writing -------------------------------------------------------------

    def write_series(self, key: SeriesKey, bars: Iterable[StoredBar]) -> int:
        """Write one whole series, replacing whatever was there. Returns row count.

        Sorting and de-duplication happen here rather than being required of
        every caller, because idempotence that depends on callers behaving is
        not idempotence.
        """
        by_open: dict[int, StoredBar] = {}
        for bar in bars:
            by_open[bar.open_time.epoch_millis] = bar
        ordered = [by_open[millis] for millis in sorted(by_open)]

        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pyarrow.table(
            {
                "open_time_ms": [bar.open_time.epoch_millis for bar in ordered],
                "open": [bar.open for bar in ordered],
                "high": [bar.high for bar in ordered],
                "low": [bar.low for bar in ordered],
                "close": [bar.close for bar in ordered],
                "volume": [bar.volume for bar in ordered],
                "trades": [bar.trades for bar in ordered],
                "is_closed": [bar.is_closed for bar in ordered],
            },
            schema=BAR_SCHEMA,
        )
        parquet.write_table(table, target, compression=_COMPRESSION)
        return len(ordered)

    def extend_series(self, key: SeriesKey, bars: Iterable[StoredBar]) -> int:
        """Merge new bars into an existing series. Returns the resulting row count.

        Later bars win on a collision, which is what an archive quarter arriving
        after a partial one should do. The result is still sorted, still
        de-duplicated, and still byte-identical to writing the union in one go.
        """
        existing = self.read_series(key) if self.has_series(key) else ()
        return self.write_series(key, [*existing, *bars])

    # -- reading -------------------------------------------------------------

    def read_series(self, key: SeriesKey) -> tuple[StoredBar, ...]:
        """Every stored bar for one series, in ascending time order.

        An empty tuple from a series that exists means listed and untraded. Ask
        :meth:`has_series` to tell that apart from never having been written.
        """
        target = self.path_for(key)
        if not target.is_file():
            raise FileNotFoundError(
                f"No stored series for {key}. An absent series and an empty one are "
                "different facts; check has_series() before reading."
            )
        table = parquet.read_table(target, schema=BAR_SCHEMA)
        columns = table.to_pydict()
        return tuple(
            StoredBar(
                open_time=Timestamp.from_epoch_millis(int(open_ms)),
                open=str(open_),
                high=str(high),
                low=str(low),
                close=str(close),
                volume=str(volume),
                trades=int(trades),
                is_closed=bool(is_closed),
            )
            for open_ms, open_, high, low, close, volume, trades, is_closed in zip(
                columns["open_time_ms"],
                columns["open"],
                columns["high"],
                columns["low"],
                columns["close"],
                columns["volume"],
                columns["trades"],
                columns["is_closed"],
                strict=True,
            )
        )

    # -- querying ------------------------------------------------------------

    def row_counts(self, venue: Venue, timeframe: Timeframe) -> Mapping[str, int]:
        """Rows held per symbol, answered by one scan rather than by opening files.

        This is what duckdb is here for. The alternative is opening several
        thousand parquet files in Python to count rows, which turns a report
        into an overnight job.

        A stored series with no rows reports ``0`` rather than being absent from
        the result. A ``GROUP BY`` cannot produce a group for a file with
        nothing in it, so the symbols found on disk seed the tally first. Losing
        the zero here would erase exactly the listed-and-untraded pairs.
        """
        stored = self.symbols(venue, timeframe)
        if not stored:
            return {}
        directory = self.root / "bars" / f"venue={venue.name}" / f"timeframe={timeframe.value}"
        pattern = (directory / "*.parquet").as_posix()
        counts: dict[str, int] = dict.fromkeys(sorted(stored), 0)
        with duckdb.connect() as connection:
            rows: Sequence[tuple[str, int]] = connection.execute(
                "SELECT regexp_extract(filename, '([^/\\\\]+)\\.parquet$', 1) AS symbol, "
                "count(*) AS row_count "
                "FROM read_parquet(?, filename = true, union_by_name = true) "
                "GROUP BY symbol",
                [pattern],
            ).fetchall()
        for symbol, count in rows:
            counts[str(symbol)] = int(count)
        return counts

    def stored_series(self, venue: Venue) -> Mapping[Timeframe, frozenset[str]]:
        """Which symbols are stored at each timeframe for one venue."""
        return {
            timeframe: symbols
            for timeframe in Timeframe
            if (symbols := self.symbols(venue, timeframe))
        }

"""The ``BarRepository`` port, implemented over the parquet store.

The store below this speaks in ``StoredBar`` - exact decimal text, no listing
window, no instrument. The port above speaks in ``Bar`` - a domain object
carrying its whole ``Instrument``. This is the translation, and it is the only
place it happens.

**Series are materialised once and sliced after that.** The engine reads the
same thirty instants ten thousand times during the null experiment, and reading
a parquet file each time would make that experiment cost hours. A series is
read once, converted once, and every later read is a binary search into the
tuple. Correctness is unaffected: the slice is computed from ``close_time``
against the requested bounds, so a cached series cannot return a bar the port
would not have returned.

**Closed-only is honoured twice.** The stored ``is_closed`` flag is respected,
and the caller's window is applied to ``close_time`` rather than ``open_time``.
A bar that opens inside the window and closes after it had not finished forming
when the window ended, and returning it would be look-ahead of exactly one bar -
the kind that is invisible in a report and worth several points of Sharpe.

**An absent series is empty, not an error.** ``FileNotFoundError`` from the
store means the venue never listed this pair, which for a read is an empty
answer rather than a failure. That distinction is invariant 10's, and it is
resolved here rather than left for the engine: a pair the store has never heard
of contributes no bars, and a store that is broken raises from the layer below.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey, StoredBar
from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.market_data import Bar
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.ports.repository import BarRepository


class UnroutedVenue(DomainError):
    """A read was asked for a venue no repository is wired for."""


@dataclass(slots=True)
class ParquetBarRepository:
    """Point-in-time bar reads for the engine, backed by the parquet store."""

    store: ParquetBarStore
    _series: dict[tuple[InstrumentKey, Timeframe], tuple[Bar, ...]] = field(
        default_factory=dict, repr=False
    )
    _close_times: dict[tuple[InstrumentKey, Timeframe], tuple[int, ...]] = field(
        default_factory=dict, repr=False
    )

    def read(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        closed_only: bool = True,
    ) -> Sequence[Bar]:
        """Bars closing in ``[start, end)``, for each instrument, in time order."""
        collected: list[Bar] = []
        for instrument in instruments:
            series = self._load(instrument, timeframe)
            closes = self._close_times[(instrument.key, timeframe)]
            first = bisect_left(closes, start.epoch_millis)
            last = bisect_right(closes, end.epoch_millis - 1)
            window = series[first:last]
            collected.extend(window if not closed_only else [b for b in window if b.is_closed])
        return collected

    def write(self, bars: Sequence[Bar]) -> int:
        """Persist bars through the store, grouped by series.

        Present so that this adapter satisfies the whole port rather than the
        half of it the backtester happens to use. A repository that raises on
        half its own interface is not the port; it is a subset with the same
        name.
        """
        written = 0
        grouped: dict[tuple[InstrumentKey, Timeframe], list[Bar]] = {}
        for bar in bars:
            grouped.setdefault((bar.instrument.key, bar.timeframe), []).append(bar)
        for (key, timeframe), series in sorted(grouped.items(), key=lambda item: str(item[0])):
            written += self.store.extend_series(
                SeriesKey(key.venue, key.symbol, timeframe),
                [_to_stored(bar) for bar in series],
            )
            self._series.pop((key, timeframe), None)
            self._close_times.pop((key, timeframe), None)
        return written

    def latest_close_time(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Timestamp | None:
        """The close time of the most recent stored closed bar, or None."""
        series = self._load(instrument, timeframe)
        for bar in reversed(series):
            if bar.is_closed:
                return bar.close_time
        return None

    def _load(self, instrument: Instrument, timeframe: Timeframe) -> tuple[Bar, ...]:
        """Materialise one whole series, once."""
        cache_key = (instrument.key, timeframe)
        cached = self._series.get(cache_key)
        if cached is not None:
            return cached
        series_key = SeriesKey(instrument.venue, instrument.symbol, timeframe)
        rows: tuple[StoredBar, ...] = ()
        if self.store.has_series(series_key):
            rows = self.store.read_series(series_key)
        bars = tuple(_to_bar(row, instrument, timeframe) for row in rows)
        self._series[cache_key] = bars
        self._close_times[cache_key] = tuple(bar.close_time.epoch_millis for bar in bars)
        return bars


def _to_bar(row: StoredBar, instrument: Instrument, timeframe: Timeframe) -> Bar:
    """One stored row as a domain bar.

    ``quote_volume`` is carried as ``close * volume``, which is the same
    approximation ``StoredBar`` documents and names. The archive publishes no
    vwap, so the exact turnover is not recoverable, and inventing a more
    precise-looking number would be worse than carrying an approximation that
    says so.
    """
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        open_time=row.open_time,
        open=Price(Decimal(row.open)),
        high=Price(Decimal(row.high)),
        low=Price(Decimal(row.low)),
        close=Price(Decimal(row.close)),
        volume=Quantity(Decimal(row.volume)),
        is_closed=row.is_closed,
        quote_volume=Notional(Decimal(row.close) * Decimal(row.volume)),
    )


def _to_stored(bar: Bar) -> StoredBar:
    """One domain bar as a stored row, keeping decimal text exact."""
    return StoredBar(
        open_time=bar.open_time,
        open=str(bar.open.amount),
        high=str(bar.high.amount),
        low=str(bar.low.amount),
        close=str(bar.close.amount),
        volume=str(bar.volume.amount),
        trades=0,
        is_closed=bar.is_closed,
    )


@dataclass(frozen=True, slots=True)
class VenueRoutedBarRepository:
    """One repository per venue, because two archives live in two roots.

    A cash-and-carry strategy holds a spot leg and a perpetual leg. They are two
    instruments on two venues, acquired by two pipelines into two directories, and
    the engine must read both through one port. This routes on the instrument's own
    venue and does nothing else.

    **Not venue branching.** No venue name appears here; the mapping is built by
    the wiring layer from configuration, which is the form invariant 2 requires.

    **A venue nobody wired raises.** It is a configuration failure, not a market
    with no bars in it, and collapsing the two is how a leg silently priced at
    nothing would reach a return. Invariant 10 applies to reads as much as to
    network calls.
    """

    by_venue: Mapping[Venue, BarRepository]

    def _for(self, instrument: Instrument) -> BarRepository:
        """The repository holding this instrument's venue, or a refusal."""
        found = self.by_venue.get(instrument.venue)
        if found is None:
            wired = ", ".join(sorted(venue.name for venue in self.by_venue))
            raise UnroutedVenue(
                f"No bar repository is wired for venue {instrument.venue.name!r}, asked for "
                f"{instrument.symbol}. Wired venues: {wired}. An unwired venue is a wiring "
                "defect, not a market with no bars in it."
            )
        return found

    def read(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        closed_only: bool = True,
    ) -> Sequence[Bar]:
        """Bars for instruments on any wired venue, in no particular order.

        The caller groups and sorts - :class:`PointInTimeView` already does, per
        instrument - so concatenating two venues' answers is safe. Sorting here
        would be work nobody uses.
        """
        grouped: dict[Venue, list[Instrument]] = {}
        for instrument in instruments:
            self._for(instrument)
            grouped.setdefault(instrument.venue, []).append(instrument)
        bars: list[Bar] = []
        for venue, group in sorted(grouped.items()):
            bars.extend(
                self.by_venue[venue].read(group, timeframe, start, end, closed_only=closed_only)
            )
        return bars

    def write(self, bars: Sequence[Bar]) -> int:
        """Persist through whichever repository owns each bar's venue."""
        grouped: dict[Venue, list[Bar]] = {}
        for bar in bars:
            self._for(bar.instrument)
            grouped.setdefault(bar.instrument.venue, []).append(bar)
        return sum(self.by_venue[venue].write(group) for venue, group in sorted(grouped.items()))

    def latest_close_time(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Timestamp | None:
        """The close time of the most recent stored closed bar, or None."""
        return self._for(instrument).latest_close_time(instrument, timeframe)

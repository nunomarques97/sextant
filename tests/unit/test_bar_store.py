"""The parquet bar store.

Three properties carry the weight. Writing twice produces identical bytes, so an
ingest can be re-run whenever a quarter arrives. Prices survive as exact
decimals, including the scientific notation the venue emits. And a stored series
with no rows stays distinguishable from a series that was never stored, because
that difference is the whole of listed-and-untraded.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.storage.bars import (
    BAR_SCHEMA,
    ParquetBarStore,
    SeriesKey,
    StoredBar,
)
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("kraken")
DAY_MILLIS = 86_400_000


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def bar(day: int, close: str = "8.1883", volume: str = "1823.26632851") -> StoredBar:
    """One stored bar, offset ``day`` days from 2024-04-01."""
    return StoredBar(
        open_time=Timestamp.from_epoch_millis(at("2024-04-01").epoch_millis + day * DAY_MILLIS),
        open="8.466",
        high="8.5063",
        low="7.936",
        close=close,
        volume=volume,
        trades=101,
        is_closed=True,
    )


def key(symbol: str = "ANTEUR", timeframe: Timeframe = Timeframe.D1) -> SeriesKey:
    """A series address."""
    return SeriesKey(VENUE, symbol, timeframe)


def fingerprint(path: Path) -> str:
    """A content hash of one stored file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_series_is_addressed_by_venue_timeframe_and_symbol(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)

    path = store.path_for(key())

    assert path.parent.name == "timeframe=1d"
    assert path.parent.parent.name == "venue=kraken"
    assert path.name == "ANTEUR.parquet"


def test_a_series_round_trips_unchanged(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)
    bars = [bar(index) for index in range(5)]

    written = store.write_series(key(), bars)

    assert written == 5
    assert store.read_series(key()) == tuple(bars)


def test_writing_twice_produces_identical_bytes(tmp_path: Path) -> None:
    """Idempotence, asserted rather than promised. An ingest is re-run every quarter."""
    store = ParquetBarStore(tmp_path)
    bars = [bar(index) for index in range(5)]

    store.write_series(key(), bars)
    first = fingerprint(store.path_for(key()))
    store.write_series(key(), bars)

    assert fingerprint(store.path_for(key())) == first


def test_input_order_and_duplicates_do_not_change_the_stored_file(tmp_path: Path) -> None:
    """Idempotence that depends on callers behaving is not idempotence."""
    store = ParquetBarStore(tmp_path)
    bars = [bar(index) for index in range(5)]

    store.write_series(key(), bars)
    ordered = fingerprint(store.path_for(key()))
    store.write_series(key(), [*reversed(bars), *bars])

    assert fingerprint(store.path_for(key())) == ordered
    assert store.read_series(key()) == tuple(bars)


def test_a_later_bar_wins_on_a_collision(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)

    store.write_series(key(), [bar(0, close="1"), bar(0, close="2")])

    assert store.read_series(key())[0].close == "2"


def test_extending_a_series_merges_rather_than_replaces(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)
    store.write_series(key(), [bar(0), bar(1)])

    total = store.extend_series(key(), [bar(1), bar(2)])

    assert total == 3
    assert [row.open_time for row in store.read_series(key())] == [
        at("2024-04-01"),
        at("2024-04-02"),
        at("2024-04-03"),
    ]


def test_extending_an_absent_series_writes_it(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)

    assert store.extend_series(key(), [bar(0)]) == 1


def test_an_empty_series_is_stored_rather_than_skipped(tmp_path: Path) -> None:
    """A pair the venue listed and nobody traded is a fact, not an absence of one."""
    store = ParquetBarStore(tmp_path)

    written = store.write_series(key("WAVESEUR"), [])

    assert written == 0
    assert store.has_series(key("WAVESEUR"))
    assert store.read_series(key("WAVESEUR")) == ()
    assert not store.has_series(key("NEVERLISTED"))


def test_reading_a_series_that_was_never_written_raises(tmp_path: Path) -> None:
    """Because returning () would make it indistinguishable from untraded."""
    store = ParquetBarStore(tmp_path)

    with pytest.raises(FileNotFoundError, match="different facts"):
        store.read_series(key("NEVERLISTED"))


def test_prices_survive_as_exact_decimals_including_scientific_notation(
    tmp_path: Path,
) -> None:
    """``1E+1`` appears verbatim in the venue's own ANTEUR hourly file."""
    store = ParquetBarStore(tmp_path)
    store.write_series(key(), [bar(0, close="8.1883", volume="1E+1")])

    stored = store.read_series(key())[0]

    assert stored.volume == "1E+1"
    assert stored.close_price == Price(Decimal("8.1883"))
    assert stored.base_volume == Quantity(Decimal("1E+1"))
    assert stored.quote_volume == Notional(Decimal("81.883"))


def test_the_closed_flag_is_preserved(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)
    open_bar = StoredBar(
        open_time=at("2024-04-01"),
        open="1",
        high="1",
        low="1",
        close="1",
        volume="1",
        trades=1,
        is_closed=False,
    )

    store.write_series(key(), [open_bar, bar(1)])
    stored = store.read_series(key())

    assert [row.is_closed for row in stored] == [False, True]


def test_the_schema_is_declared_rather_than_inferred() -> None:
    """A shape change should be a diff in one file, not a surprise downstream."""
    assert BAR_SCHEMA.names == [
        "open_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trades",
        "is_closed",
    ]
    assert str(BAR_SCHEMA.field("close").type) == "string"


def test_symbols_and_row_counts_are_answered_across_the_store(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)
    store.write_series(key("ANTEUR"), [bar(0), bar(1)])
    store.write_series(key("WAVESEUR"), [])
    store.write_series(key("ANTEUR", Timeframe.H1), [bar(0)])

    assert store.symbols(VENUE, Timeframe.D1) == frozenset({"ANTEUR", "WAVESEUR"})
    assert store.row_counts(VENUE, Timeframe.D1) == {"ANTEUR": 2, "WAVESEUR": 0}
    assert store.stored_series(VENUE) == {
        Timeframe.D1: frozenset({"ANTEUR", "WAVESEUR"}),
        Timeframe.H1: frozenset({"ANTEUR"}),
    }


def test_an_untraded_pair_reports_zero_rows_rather_than_disappearing(
    tmp_path: Path,
) -> None:
    """A GROUP BY cannot produce a group for an empty file. Losing the zero here
    would erase exactly the listed-and-untraded pairs the calendar works to keep."""
    store = ParquetBarStore(tmp_path)
    store.write_series(key("WAVESEUR"), [])

    assert store.row_counts(VENUE, Timeframe.D1) == {"WAVESEUR": 0}


def test_an_empty_store_answers_emptily_rather_than_failing(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path)

    assert store.symbols(VENUE, Timeframe.D1) == frozenset()
    assert store.row_counts(VENUE, Timeframe.D1) == {}
    assert store.stored_series(VENUE) == {}

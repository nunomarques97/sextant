"""The four archive stages, end to end, over miniature archives.

Built rather than downloaded, so the shape of the pipeline is what is under test
and not the size of the files. The equivalent assertions against the real Q2 and
Q3 2024 archives live in ``test_kraken_archive_facts.py``.

The property this module exists for is idempotence. These stages get re-run
every time a quarter arrives by hand, and an ingest that is only *usually*
idempotent produces a store nobody can reason about.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.adapters.exchanges.kraken.archive import (
    ARCHIVE_INTERVAL_MINUTES,
    Quarter,
    quarters_between,
)
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey
from sextant.app import archive_ingest, archive_measure
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("kraken")
DAY_SECONDS = 86_400
HOUR_SECONDS = 3_600
NOW = Timestamp(datetime(2026, 9, 9, tzinfo=UTC))


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def body(start: str, count: int, step: int, close: str = "100", volume: str = "20000") -> str:
    """A CSV body in the venue's exact wire shape, with no header."""
    first = int(at(start).epoch_millis // 1000)
    return "".join(
        f"{first + index * step},99,101,98,{close},{volume},7\n" for index in range(count)
    )


def daily(start: str, count: int, **kwargs: str) -> str:
    """A daily CSV body."""
    return body(start, count, DAY_SECONDS, **kwargs)


def write_quarter(root: Path, quarter: Quarter, pairs: dict[str, str]) -> None:
    """Write a miniature quarterly ZIP holding all eight granularities."""
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / quarter.archive_name, "w") as archive:
        for symbol, csv in pairs.items():
            for minutes in ARCHIVE_INTERVAL_MINUTES:
                archive.writestr(f"{symbol}_{minutes}.csv", csv)


@dataclass(frozen=True, slots=True)
class Fixture:
    """A built archive root and the store root beside it."""

    archives: Path
    store_root: Path


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Two quarters: a survivor, a pair that dies, one that lists, one untraded."""
    archives = tmp_path / "data"
    write_quarter(
        archives,
        Quarter(2024, 2),
        {
            "XBTEUR": daily("2024-04-01", 91),
            "ANTEUR": daily("2024-04-01", 91),
            "WAVESEUR": "",
        },
    )
    write_quarter(
        archives,
        Quarter(2024, 3),
        {
            "XBTEUR": daily("2024-07-01", 92),
            "EIGENEUR": daily("2024-07-01", 92),
        },
    )
    monkeypatch.setattr(archive_ingest, "FIRST_QUARTER", Quarter(2024, 2))
    monkeypatch.setattr(archive_ingest, "LAST_QUARTER", Quarter(2024, 4))
    return Fixture(archives=archives, store_root=tmp_path / "store")


def store_fingerprint(root: Path) -> str:
    """A content hash of the whole parquet tree."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.parquet")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# -- scan --------------------------------------------------------------------


def test_the_scan_records_a_checksum_per_file_and_names_the_rest(
    built: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)

    assert [quarter.label for quarter in manifest.quarters] == ["Q2_2024", "Q3_2024"]
    assert [quarter.label for quarter in manifest.missing] == ["Q4_2024"]
    recorded = json.loads((built.store_root / "manifest.json").read_text(encoding="utf-8"))
    assert len(recorded["files"][0]["sha256"]) == 64
    captured = capsys.readouterr().out
    assert "held 2/3" in captured
    assert "MISSING 1: Q4_2024" in captured


def test_the_scan_reports_holding_nothing_rather_than_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(archive_ingest, "FIRST_QUARTER", Quarter(2024, 2))
    monkeypatch.setattr(archive_ingest, "LAST_QUARTER", Quarter(2024, 2))

    manifest = archive_ingest.scan(tmp_path / "empty", tmp_path / "store")

    assert manifest.quarters == ()
    assert "held 0/1: none" in capsys.readouterr().out


def test_a_manifest_is_reused_rather_than_rehashed(built: Fixture) -> None:
    """Hashing is 300 MB per file. It happens once and every later read is verified."""
    archive_ingest.scan(built.archives, built.store_root)

    loaded = archive_ingest.load_manifest(built.store_root, built.archives)

    assert [quarter.label for quarter in loaded.quarters] == ["Q2_2024", "Q3_2024"]
    loaded.verify_all()


def test_a_manifest_is_built_on_demand_when_absent(built: Fixture) -> None:
    loaded = archive_ingest.load_manifest(built.store_root, built.archives)

    assert len(loaded.files) == 2


# -- calendar ----------------------------------------------------------------


def test_the_calendar_stage_writes_intervals_and_the_missing_quarters(
    built: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)

    calendar = archive_ingest.build_calendar(manifest, built.store_root)

    assert calendar.delisted_during(Quarter(2024, 3)) == frozenset({"ANTEUR", "WAVESEUR"})
    assert calendar.listed_during(Quarter(2024, 3)) == frozenset({"EIGENEUR"})
    assert calendar.untraded_symbols() == frozenset({"WAVESEUR"})
    assert [quarter.label for quarter in calendar.missing_quarters] == ["Q4_2024"]
    assert "listed-and-untraded" in capsys.readouterr().out
    reloaded = archive_ingest.load_calendar(built.store_root)
    assert reloaded.entries == calendar.entries


def test_no_point_delisting_date_is_written_anywhere(built: Fixture) -> None:
    """The invariant the whole task exists to honour, checked on the artifact.

    Every delisting in the persisted calendar carries two bounds or an open end.
    A single instant would mean somebody narrowed a bracket to a date.
    """
    manifest = archive_ingest.scan(built.archives, built.store_root)
    archive_ingest.build_calendar(manifest, built.store_root)

    written = json.loads((built.store_root / "listing_calendar.json").read_text(encoding="utf-8"))

    for entry in written["entries"]:
        for spell in entry["spells"]:
            assert {"delisted_after", "delisted_until"} <= set(spell)
            assert "delisted_at" not in spell
            assert spell["delisted_after"] != spell["delisted_until"]


# -- ingest ------------------------------------------------------------------


def test_the_ingest_writes_daily_and_hourly_for_every_pair(built: Fixture) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)
    store = ParquetBarStore(built.store_root)

    report = archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))

    assert report.quarters == ("Q2_2024", "Q3_2024")
    assert report.series_written == {"1d": 4, "1h": 4}
    assert store.symbols(VENUE, Timeframe.D1) == frozenset(
        {"XBTEUR", "ANTEUR", "WAVESEUR", "EIGENEUR"}
    )
    assert store.symbols(VENUE, Timeframe.H1) == store.symbols(VENUE, Timeframe.D1)


def test_a_pair_present_in_both_quarters_gets_one_continuous_series(
    built: Fixture,
) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)
    store = ParquetBarStore(built.store_root)
    archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))

    bars = store.read_series(SeriesKey(VENUE, "XBTEUR", Timeframe.D1))

    assert len(bars) == 91 + 92
    assert bars[0].open_time == at("2024-04-01")
    assert bars[-1].open_time == at("2024-09-30")


def test_an_untraded_pair_is_stored_empty_rather_than_skipped(built: Fixture) -> None:
    """Listed-and-untraded has to survive the storage layer too."""
    manifest = archive_ingest.scan(built.archives, built.store_root)
    store = ParquetBarStore(built.store_root)

    report = archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))

    assert report.empty_series == {"1d": 1, "1h": 1}
    assert store.has_series(SeriesKey(VENUE, "WAVESEUR", Timeframe.D1))
    assert store.read_series(SeriesKey(VENUE, "WAVESEUR", Timeframe.D1)) == ()
    assert store.row_counts(VENUE, Timeframe.D1)["WAVESEUR"] == 0


def test_running_the_ingest_twice_produces_the_same_store(built: Fixture) -> None:
    """The acceptance criterion, asserted on every byte of the tree."""
    manifest = archive_ingest.scan(built.archives, built.store_root)
    store = ParquetBarStore(built.store_root)

    first = archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))
    before = store_fingerprint(built.store_root)
    second = archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))

    assert store_fingerprint(built.store_root) == before
    assert first.as_json() == second.as_json()


def test_the_closed_flag_comes_from_the_clock_not_the_wall(built: Fixture) -> None:
    """A backtest clock therefore produces a deterministic store."""
    manifest = archive_ingest.scan(built.archives, built.store_root)
    store = ParquetBarStore(built.store_root)

    archive_ingest.ingest(manifest, store, clock=SimulatedClock(at("2024-05-01")))
    bars = store.read_series(SeriesKey(VENUE, "XBTEUR", Timeframe.D1))

    assert bars[0].is_closed
    assert not bars[-1].is_closed


# -- measure -----------------------------------------------------------------


def test_the_measure_stage_writes_tables_and_the_haircut_sensitivity(
    built: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)
    calendar = archive_ingest.build_calendar(manifest, built.store_root)
    store = ParquetBarStore(built.store_root)
    archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))
    tables = tmp_path / "tables.md"

    summary = archive_measure.measure_all(
        calendar,
        store,
        manifest,
        metadata={},
        tables_path=tables,
        store_root=built.store_root,
    )

    rendered = tables.read_text(encoding="utf-8")
    assert "quote policy `EUR`" in rendered
    assert "quote policy `EUR+USD`" in rendered
    assert "delisting haircut sensitivity" in rendered
    assert "survivorship-biased with no available remedy" in rendered
    assert "Quarters missing: Q4_2024" in rendered
    assert summary["candidates"] == 4
    assert summary["listed_and_untraded"] == ["WAVESEUR"]
    assert len(summary["haircut_sensitivity"]) == 3
    assert "peak research universe" in capsys.readouterr().out


def test_the_haircut_rows_span_zero_twenty_and_fifty_percent(built: Fixture) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)
    calendar = archive_ingest.build_calendar(manifest, built.store_root)
    store = ParquetBarStore(built.store_root)
    archive_ingest.ingest(manifest, store, clock=SimulatedClock(NOW))

    rows = archive_ingest.haircut_report(calendar, store)

    assert [row.fraction for row in rows] == ["0.00", "0.20", "0.50"]
    # ANTEUR died in Q3 and is priced; WAVESEUR died too but has no rows to price.
    assert {row.haircut_applied_to for row in rows} == {1}
    assert Decimal(rows[0].total_cost) == Decimal(0)
    assert Decimal(rows[2].total_cost) > Decimal(rows[1].total_cost) > Decimal(0)


def test_the_measured_window_spans_the_held_quarters(built: Fixture) -> None:
    manifest = archive_ingest.scan(built.archives, built.store_root)
    calendar = archive_ingest.build_calendar(manifest, built.store_root)

    months = archive_measure.month_starts(
        calendar.quarters[0].starts_at, calendar.quarters[-1].ends_at
    )

    assert [instant.isoformat()[:7] for instant in months] == [
        "2024-04",
        "2024-05",
        "2024-06",
        "2024-07",
        "2024-08",
        "2024-09",
        "2024-10",
    ]


def test_an_empty_calendar_refuses_to_be_measured(built: Fixture) -> None:
    """Rather than reporting an empty universe, which looks like a finding."""
    from sextant.adapters.exchanges.kraken.listing_calendar import KrakenListingCalendar

    manifest = archive_ingest.scan(built.archives, built.store_root)

    with pytest.raises(ValueError, match="empty calendar"):
        archive_measure.measure_all(
            KrakenListingCalendar.from_membership(VENUE, []),
            ParquetBarStore(built.store_root),
            manifest,
            metadata={},
            store_root=built.store_root,
        )


def test_the_thirteen_quarter_window_is_the_one_the_venue_publishes() -> None:
    assert len(quarters_between(Quarter(2023, 1), Quarter(2026, 1))) == 13
    assert Quarter(2023, 1) == archive_ingest.FIRST_QUARTER
    assert Quarter(2026, 1) == archive_ingest.LAST_QUARTER

"""Turning the Kraken quarterly archives into a store and a listing calendar.

Four stages, each separately runnable and each idempotent, because the archives
arrive by hand through a browser and the pipeline will be re-run every time one
lands:

* **scan** - hash every quarterly ZIP present, name the ones that are not, and
  write a manifest. Nothing downstream reads an archive without a checksum;
* **calendar** - diff the membership snapshots into a listing calendar of
  intervals;
* **ingest** - write daily and hourly bars into the parquet store;
* **measure** - recompute the Phase 0 universe rules over the whole archive
  window, plus the delisting-haircut sensitivity.

The one thing this module is careful never to do is fill a gap. Where quarters
are missing the manifest says so, the calendar's intervals widen accordingly,
and the tables carry the missing list. A pipeline that quietly interpolated
across a hole in the download would produce a universe that looks complete and
is not, which is the failure this whole task exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.kraken.archive import (
    INGESTED_TIMEFRAMES,
    ArchiveManifest,
    Quarter,
    QuarterlyArchive,
    quarters_between,
    scan_archives,
)
from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.listing_calendar import (
    KrakenListingCalendar,
    QuarterlyMembership,
)
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey, StoredBar
from sextant.domain.instrument import InstrumentKey
from sextant.domain.listing import MembershipState
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.execution.markout import (
    DelistingHaircut,
    Position,
    SeriesEnd,
    haircut_sensitivity,
)
from sextant.engine.universe.rules import AccountParameters
from sextant.ports.clock import Clock

#: Where the hand-downloaded quarterly ZIPs live. Git-ignored, never committed.
ARCHIVE_ROOT = Path("data")

#: Where the parquet store and the derived artifacts live. Also git-ignored.
STORE_ROOT = Path("data") / "kraken-archive"

#: The window the venue publishes quarterly increments for.
FIRST_QUARTER = Quarter(year=2023, quarter=1)
LAST_QUARTER = Quarter(year=2026, quarter=1)

MANIFEST_NAME = "manifest.json"
CALENDAR_NAME = "listing_calendar.json"

NEWLINE = chr(10)
"""Explicit, so generated markdown is LF on every host."""

_UNDETERMINED_LABEL = "undetermined"
"""Reported separately, because a large undetermined population means the
haircut sensitivity below it is understated rather than reassuring."""


def wanted_quarters() -> tuple[Quarter, ...]:
    """Every quarter the venue publishes an increment for."""
    return quarters_between(FIRST_QUARTER, LAST_QUARTER)


# ----------------------------------------------------------------------------
# Stage 1: scan and checksum
# ----------------------------------------------------------------------------


def scan(
    archive_root: Path = ARCHIVE_ROOT,
    store_root: Path = STORE_ROOT,
) -> ArchiveManifest:
    """Hash every held quarter, name every missing one, and record both."""
    manifest = scan_archives(archive_root, wanted_quarters())
    manifest.write_json(store_root / MANIFEST_NAME)
    held = ", ".join(quarter.label for quarter in manifest.quarters) or "none"
    print(f"[archive] held {len(manifest.files)}/{len(wanted_quarters())}: {held}")
    if manifest.missing:
        missing = ", ".join(quarter.label for quarter in manifest.missing)
        print(f"[archive] MISSING {len(manifest.missing)}: {missing}")
        print(
            "[archive] these must be downloaded by hand from the Drive folder linked "
            "in Kraken's 'Downloadable historical OHLCVT data' article and dropped "
            f"into {archive_root}/"
        )
    return manifest


def load_manifest(
    store_root: Path = STORE_ROOT, archive_root: Path = ARCHIVE_ROOT
) -> ArchiveManifest:
    """Read the manifest written by :func:`scan`, or build it if absent."""
    path = store_root / MANIFEST_NAME
    if not path.is_file():
        return scan(archive_root, store_root)
    return ArchiveManifest.read_json(path, archive_root)


# ----------------------------------------------------------------------------
# Stage 2: the listing calendar
# ----------------------------------------------------------------------------


def build_calendar(
    manifest: ArchiveManifest,
    store_root: Path = STORE_ROOT,
    *,
    verify: bool = True,
) -> KrakenListingCalendar:
    """Read each quarter's membership from file presence and diff into intervals."""
    snapshots: list[QuarterlyMembership] = []
    for quarter in manifest.quarters:
        with QuarterlyArchive(manifest.files[quarter.label], verify=verify) as archive:
            presence = archive.presence()
        snapshots.append(QuarterlyMembership(quarter=quarter, presence=presence))
        print(f"[calendar] {quarter.label}: {len(presence)} pairs listed at quarter end")

    calendar = KrakenListingCalendar.from_membership(
        VENUE, snapshots, missing_quarters=manifest.missing
    )
    calendar.write_json(store_root / CALENDAR_NAME)
    print(
        f"[calendar] {len(calendar.entries)} pairs, "
        f"{len(calendar.untraded_symbols())} seen listed-and-untraded, "
        f"{len(calendar.relisted_symbols())} relisted"
    )
    return calendar


def load_calendar(store_root: Path = STORE_ROOT) -> KrakenListingCalendar:
    """Read the calendar written by :func:`build_calendar`."""
    return KrakenListingCalendar.read_json(store_root / CALENDAR_NAME)


# ----------------------------------------------------------------------------
# Stage 3: ingestion into the parquet store
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one ingest run wrote, per timeframe."""

    quarters: tuple[str, ...]
    series_written: Mapping[str, int]
    rows_written: Mapping[str, int]
    empty_series: Mapping[str, int]

    def as_json(self) -> dict[str, object]:
        """Serialisable form, sorted so two runs diff cleanly."""
        return {
            "quarters": list(self.quarters),
            "series_written": dict(sorted(self.series_written.items())),
            "rows_written": dict(sorted(self.rows_written.items())),
            "empty_series": dict(sorted(self.empty_series.items())),
        }


def ingest(
    manifest: ArchiveManifest,
    store: ParquetBarStore,
    *,
    timeframes: Sequence[Timeframe] = tuple(INGESTED_TIMEFRAMES),
    clock: Clock,
    verify: bool = True,
) -> IngestReport:
    """Write every pair's daily and hourly bars into the store.

    Every quarter is opened at once and each series is written in a single call,
    rather than each quarter appending to what the previous one left. That is
    what makes the run idempotent without any bookkeeping: the store is a pure
    function of the manifest, so a re-run overwrites each file with identical
    bytes and a removed quarter cannot leave rows behind.

    The closed flag is decided against ``clock``, which is a port rather than a
    wall-clock read: ``datetime.now`` lives in ``adapters`` and nowhere else, and
    a test greps for it. A backtest clock therefore produces a deterministic
    store, which is what makes the idempotence assertion meaningful.
    """
    boundary = clock.now()
    archives = [
        QuarterlyArchive(manifest.files[quarter.label], verify=verify)
        for quarter in manifest.quarters
    ]
    try:
        symbols = sorted({symbol for archive in archives for symbol in archive.symbols()})
        series_written: dict[str, int] = {}
        rows_written: dict[str, int] = {}
        empty_series: dict[str, int] = {}

        for timeframe in timeframes:
            written = 0
            rows = 0
            empty = 0
            for index, symbol in enumerate(symbols, start=1):
                bars = _bars_for(archives, symbol, timeframe, boundary)
                if bars is None:
                    continue
                count = store.write_series(SeriesKey(VENUE, symbol, timeframe), bars)
                written += 1
                rows += count
                if count == 0:
                    empty += 1
                if index % 200 == 0:
                    print(f"[ingest] {timeframe.value}: {index}/{len(symbols)} pairs")
            series_written[timeframe.value] = written
            rows_written[timeframe.value] = rows
            empty_series[timeframe.value] = empty
            print(
                f"[ingest] {timeframe.value}: {written} series, {rows} rows, "
                f"{empty} listed-and-untraded"
            )
    finally:
        for archive in archives:
            archive.close()

    report = IngestReport(
        quarters=tuple(quarter.label for quarter in manifest.quarters),
        series_written=series_written,
        rows_written=rows_written,
        empty_series=empty_series,
    )
    _write_json(store.root / "ingest_report.json", report.as_json())
    return report


def _bars_for(
    archives: Sequence[QuarterlyArchive],
    symbol: str,
    timeframe: Timeframe,
    boundary: Timestamp,
) -> list[StoredBar] | None:
    """Every bar for one pair at one timeframe, across every held quarter.

    ``None`` means no held quarter carries this pair at this timeframe, which is
    different from an empty list. An empty list means the venue shipped the
    files and they hold no rows: listed and untraded.
    """
    present = False
    bars: list[StoredBar] = []
    for archive in archives:
        if not archive.has_series(symbol, timeframe):
            continue
        present = True
        for row in archive.read_series(symbol, timeframe):
            bars.append(
                StoredBar(
                    open_time=row.open_time,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    trades=row.trades,
                    is_closed=row.open_time.plus(timeframe.duration) <= boundary,
                )
            )
    return bars if present else None


# ----------------------------------------------------------------------------
# Stage 4: the haircut sensitivity over the delisted population
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HaircutRow:
    """One haircut setting, and what it costs across every delisted pair."""

    fraction: str
    marked_out: int
    haircut_applied_to: int
    gross_proceeds: str
    proceeds: str
    total_cost: str
    cost_fraction_of_gross: str


def haircut_report(
    calendar: KrakenListingCalendar,
    store: ParquetBarStore,
    *,
    at: Timestamp | None = None,
    account: AccountParameters | None = None,
) -> tuple[HaircutRow, ...]:
    """Price an equally weighted position in every pair, at each haircut.

    The population is every pair the calendar carries that has a priced series,
    each held at the account's target position size and marked out at its last
    stored close. That is not a backtest and does not pretend to be one: it is
    the assumption's exposure surface, which is the thing the brief asks to be
    readable.

    Equal *notional* rather than equal units, and the distinction matters. One
    unit of each pair weights the population by price, so a BTC pair at 60,000
    swamps three hundred small ones and the haircut reads as negligible for a
    reason that has nothing to do with delistings. Equal notional is also what
    the D2 account actually does: 1,500 EUR over at most 8 positions.

    Pairs whose delisting bracket has closed take the haircut. Pairs still
    listed at the end of the archive, and pairs whose membership at that instant
    falls inside a bracket, do not - and the counts of each are reported,
    because a large undetermined population means the sensitivity itself is
    understated.
    """
    boundary = at or _archive_end(calendar)
    target = (account or default_account()).target_position
    positions: list[Position] = []
    for symbol, entry in sorted(calendar.entries.items()):
        key = SeriesKey(VENUE, symbol, Timeframe.D1)
        if not store.has_series(key):
            continue
        bars = store.read_series(key)
        if not bars:
            continue
        last_close = Price(Decimal(bars[-1].close))
        if last_close.amount <= 0:
            continue
        state = entry.membership_at(boundary)
        reason = {
            MembershipState.LISTED: SeriesEnd.STILL_LISTED,
            MembershipState.NOT_LISTED: SeriesEnd.DELISTED,
            MembershipState.UNDETERMINED: SeriesEnd.UNDETERMINED,
        }[state]
        positions.append(
            Position(
                instrument=InstrumentKey(VENUE, symbol),
                quantity=Quantity(target.amount / last_close.amount),
                last_close=last_close,
                reason=reason,
            )
        )

    return tuple(
        HaircutRow(
            fraction=str(effect.fraction),
            marked_out=effect.marked_out,
            haircut_applied_to=effect.haircut_applied_to,
            gross_proceeds=str(effect.gross_proceeds.amount),
            proceeds=str(effect.proceeds.amount),
            total_cost=str(effect.total_cost.amount),
            cost_fraction_of_gross=str(effect.cost_fraction_of_gross),
        )
        for effect in haircut_sensitivity(positions)
    )


def _archive_end(calendar: KrakenListingCalendar) -> Timestamp:
    """The instant the archive's evidence stops."""
    if not calendar.quarters:
        raise ValueError("an empty calendar has no archive end")
    return calendar.quarters[-1].ends_at


def default_account() -> AccountParameters:
    """PO decision D2: 1,500 EUR of equity, at most 8 concurrent positions."""
    return AccountParameters(equity_quote=Notional(Decimal(1500)), max_positions=8)


def default_haircut() -> DelistingHaircut:
    """The mark-out assumption applied unless a run says otherwise."""
    return DelistingHaircut()


def _write_json(path: Path, payload: object) -> None:
    """Write UTF-8 JSON with stable ordering, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
        handle.write("\n")

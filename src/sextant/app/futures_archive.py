"""Acquiring the venue's USD-margined futures archive, whole.

**Nothing is selected.** SEXTANT-005 fetched only the symbols its two
pre-registered quote policies named, because the spot tree holds 3,710 symbol
directories and fetching all of them would have been a download rather than an
acquisition. This tree holds 952, and the three object families this task reads
come to 83 MB across 67,496 objects. So the whole tree is taken: every symbol,
every published month, no quote filter, no window trim, no liquidity screen.

That is a deliberate removal of a degree of freedom. An acquisition that selects
is an acquisition that could have selected differently once a result was known,
and no argument about the universe can be settled by pointing at what was
downloaded. Every admission decision therefore happens later, at the strategy's
own point-in-time universe rules, on a dataset that contains everything.

Four stages, each writing what the next one reads
--------------------------------------------------

``index``
    list the bucket. One request per symbol per tree, no bar downloaded. The
    listing is the membership evidence for the perpetual universe exactly as it
    was for spot, and a perpetual that stopped trading in 2022 is in it.

``fetch``
    download every indexed object, verifying the ZIP's own CRC and, where the
    publisher ships a sibling ``.CHECKSUM``, its SHA-256 as well. Resumable.

``ingest``
    parse into the three stores: perpetual bars, funding settlements, premium
    index. Idempotent.

``calendar``
    turn month presence into listing intervals through the same venue-neutral
    rule both spot calendars use, so a perpetual's listing and delisting
    brackets are built by the same code that built the spot ones.

The store root is a **parameter with no default pointing anywhere real** in
every function that writes, per invariant 11. The module-level constants below
are what the CLI passes; a test passes ``tmp_path`` and cannot touch the archive.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sextant.adapters.exchanges.binance.archive import Month, months_between, parse_klines
from sextant.adapters.exchanges.binance.futures_archive import (
    BinanceFuturesArchive,
    FuturesObject,
    FuturesTree,
    parse_funding,
    parse_premium_index,
)
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey, StoredBar
from sextant.adapters.storage.derivatives import (
    FundingStore,
    PremiumStore,
    StoredFunding,
    StoredPremium,
)
from sextant.domain.errors import SextantError
from sextant.domain.time import Timeframe
from sextant.domain.venue import Venue

#: Where the futures acquisition lives. Deliberately not the spot archive's
#: root: the SEXTANT-005 dataset is checksummed and published, and nothing this
#: task does may change a byte of it.
STORE_ROOT = Path("data") / "binance-futures"
RAW_ROOT = STORE_ROOT / "raw"

INDEX_NAME = "futures_index.json"
FETCH_NAME = "futures_fetch_report.json"
CALENDAR_NAME = "perpetual_calendar.json"

#: The perpetual book is its own venue as far as the store is concerned. A spot
#: BTCUSDT and a perpetual BTCUSDT are different instruments with different
#: prices and different cash flows, and one store key must not name both.
PERP_VENUE = Venue("binance_perp")

TIMEFRAME = Timeframe.D1
INTERVAL = "1d"

#: The three families this task reads. Mark price is deliberately absent: it is
#: needed only for the liquidation study in stage 3, and only by a family that
#: has already cleared its criteria at 1x.
TREES = (FuturesTree.FUNDING_RATE, FuturesTree.KLINES, FuturesTree.PREMIUM_INDEX)


# ---------------------------------------------------------------------------
# Stage 1: index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FuturesIndex:
    """Which months each symbol published, in each tree, and what they weigh.

    The whole membership record for the perpetual universe. Built from the
    bucket listing alone: no bar is downloaded to establish which months a
    contract traded in.
    """

    objects: Mapping[str, tuple[FuturesObject, ...]]
    """Keyed by ``"<tree>/<symbol>"``."""

    @property
    def symbols(self) -> tuple[str, ...]:
        """Every symbol seen in any tree, ascending."""
        return tuple(sorted({key.split("/", 1)[1] for key in self.objects}))

    def for_tree(self, tree: FuturesTree) -> Mapping[str, tuple[FuturesObject, ...]]:
        """Every symbol's objects in one tree, keyed by symbol."""
        prefix = f"{tree.value}/"
        return {
            key[len(prefix) :]: value
            for key, value in sorted(self.objects.items())
            if key.startswith(prefix)
        }

    def months_for(self, tree: FuturesTree, symbol: str) -> tuple[Month, ...]:
        """The months one symbol published in one tree, ascending."""
        return tuple(item.month for item in self.objects.get(f"{tree.value}/{symbol}", ()))

    @property
    def all_objects(self) -> tuple[FuturesObject, ...]:
        """Every object in every tree, in a deterministic order."""
        return tuple(item for key in sorted(self.objects) for item in self.objects[key])

    def total_bytes(self) -> int:
        """What the whole acquisition weighs, from the listing's own sizes."""
        return sum(item.size_bytes for item in self.all_objects)

    def write_json(self, path: Path) -> None:
        """Persist the index. One entry per object, so it is auditable."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trees": [tree.value for tree in TREES],
            "interval": INTERVAL,
            "objects": {
                key: [
                    {
                        "month": item.month.label,
                        "key": item.key,
                        "size_bytes": item.size_bytes,
                    }
                    for item in value
                ]
                for key, value in sorted(self.objects.items())
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def read_json(cls, path: Path) -> FuturesIndex:
        """Read an index back, reconstructing the objects it names."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        objects: dict[str, tuple[FuturesObject, ...]] = {}
        for key, rows in payload["objects"].items():
            tree_name, symbol = key.split("/", 1)
            tree = FuturesTree(tree_name)
            objects[key] = tuple(
                FuturesObject(
                    tree=tree,
                    symbol=symbol,
                    interval=INTERVAL if tree.has_interval else "",
                    month=Month.parse(str(row["month"])),
                    key=str(row["key"]),
                    size_bytes=int(row["size_bytes"]),
                )
                for row in rows
            )
        return cls(objects=objects)


def build_index(symbols: Sequence[str], *, workers: int = 12) -> FuturesIndex:
    """List every published month for every symbol in every tree.

    One listing request per symbol per tree. Fanned out because 2,856 sequential
    round trips is twenty minutes of waiting for an answer that takes two.
    """
    local = threading.local()

    def archive_for_thread() -> BinanceFuturesArchive:
        existing = getattr(local, "archive", None)
        if existing is None:
            existing = BinanceFuturesArchive()
            local.archive = existing
        return existing

    def one(task: tuple[FuturesTree, str]) -> tuple[str, tuple[FuturesObject, ...]]:
        tree, symbol = task
        found = archive_for_thread().monthly_objects(
            tree, symbol, interval=INTERVAL if tree.has_interval else ""
        )
        return f"{tree.value}/{symbol}", found

    tasks = [(tree, symbol) for tree in TREES for symbol in symbols]
    objects: dict[str, tuple[FuturesObject, ...]] = {}
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (key, found) in enumerate(pool.map(one, tasks), start=1):
            if found:
                objects[key] = found
            if done % 500 == 0:
                print(f"[index] {done}/{len(tasks)} symbol-trees listed")
    return FuturesIndex(objects=objects)


# ---------------------------------------------------------------------------
# Stage 2: fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FuturesFetchReport:
    """What the download produced, including what it could not.

    ``failures`` is a mapping and not a count. A failure that is only counted is
    a failure nobody can look up, and invariant 10 exists because an unexamined
    failure becomes an empty result three stages later.
    """

    digests: Mapping[str, str]
    publisher_verified: int
    publisher_absent: int
    bytes_written: int
    seconds: float
    failures: Mapping[str, str]

    def write_json(self, path: Path) -> None:
        """Persist the report."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "objects": len(self.digests),
            "publisher_verified": self.publisher_verified,
            "publisher_absent": self.publisher_absent,
            "bytes_written": self.bytes_written,
            "seconds": round(self.seconds, 1),
            "failures": dict(sorted(self.failures.items())),
            "digests": dict(sorted(self.digests.items())),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def read_json(cls, path: Path) -> FuturesFetchReport:
        """Read a report back."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            digests={str(k): str(v) for k, v in payload["digests"].items()},
            publisher_verified=int(payload["publisher_verified"]),
            publisher_absent=int(payload["publisher_absent"]),
            bytes_written=int(payload["bytes_written"]),
            seconds=float(payload["seconds"]),
            failures={str(k): str(v) for k, v in payload["failures"].items()},
        )


def raw_path(item: FuturesObject, raw_root: Path) -> Path:
    """Where one downloaded object lives on disk."""
    return raw_root / item.tree.value / item.symbol / item.name


def fetch(
    index: FuturesIndex,
    *,
    raw_root: Path,
    workers: int = 16,
    progress_every: int = 5000,
) -> FuturesFetchReport:
    """Download every indexed object. Resumable, and it fetches nothing else."""
    from concurrent.futures import ThreadPoolExecutor

    started = time.monotonic()
    digests: dict[str, str] = {}
    failures: dict[str, str] = {}
    verified = 0
    absent = 0
    written = 0

    local = threading.local()

    def archive_for_thread() -> BinanceFuturesArchive:
        existing = getattr(local, "archive", None)
        if existing is None:
            existing = BinanceFuturesArchive()
            local.archive = existing
        return existing

    def one(item: FuturesObject) -> tuple[FuturesObject, object | None, str | None]:
        destination = raw_path(item, raw_root)
        try:
            return item, archive_for_thread().download(item, destination), None
        except Exception as error:  # recorded in the report, never swallowed
            return item, None, f"{type(error).__name__}: {error}"

    planned = index.all_objects
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (item, outcome, error) in enumerate(pool.map(one, planned), start=1):
            if outcome is None:
                failures[item.key] = error or "unknown"
            else:
                digests[item.key] = getattr(outcome, "digest", "")
                if getattr(outcome, "verified", False):
                    verified += 1
                elif getattr(outcome, "publisher_digest", None) is None:
                    absent += 1
                written += item.size_bytes
            if done % progress_every == 0:
                print(
                    f"[fetch] {done}/{len(planned)} objects, {written / 1e6:.0f} MB, "
                    f"{len(failures)} failures"
                )

    return FuturesFetchReport(
        digests=digests,
        publisher_verified=verified,
        publisher_absent=absent,
        bytes_written=written,
        seconds=time.monotonic() - started,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Stage 3: ingest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FuturesIngestReport:
    """How many series and rows each family produced, and what would not parse."""

    bar_series: int
    bars: int
    funding_series: int
    funding_rows: int
    premium_series: int
    premium_rows: int
    unreadable: Mapping[str, str]
    seconds: float

    def as_json(self) -> dict[str, object]:
        """Serialisable form."""
        return {
            "bar_series": self.bar_series,
            "bars": self.bars,
            "funding_series": self.funding_series,
            "funding_rows": self.funding_rows,
            "premium_series": self.premium_series,
            "premium_rows": self.premium_rows,
            "unreadable": dict(sorted(self.unreadable.items())),
            "seconds": round(self.seconds, 1),
        }


def ingest(index: FuturesIndex, *, store_root: Path, raw_root: Path) -> FuturesIngestReport:
    """Parse every downloaded object into its store. Idempotent.

    An object that will not parse is recorded by key with the reason and its
    symbol's series is still written from the months that did parse, with the
    gap left as a gap. That is invariant 9: a month whose bytes are unreadable
    is not a month with no data, and the two must not be collapsed.
    """
    started = time.monotonic()
    bars_store = ParquetBarStore(store_root)
    funding_store = FundingStore(store_root)
    premium_store = PremiumStore(store_root)
    unreadable: dict[str, str] = {}

    def payload_of(item: FuturesObject) -> bytes | None:
        path = raw_path(item, raw_root)
        if not path.is_file():
            unreadable[item.key] = "not downloaded"
            return None
        return path.read_bytes()

    bar_series = bars = 0
    for symbol, objects in sorted(index.for_tree(FuturesTree.KLINES).items()):
        rows: list[StoredBar] = []
        for item in objects:
            payload = payload_of(item)
            if payload is None:
                continue
            try:
                parsed = parse_klines(payload, source=item.name)
            except Exception as error:
                unreadable[item.key] = f"{type(error).__name__}: {error}"
                continue
            rows.extend(
                StoredBar(
                    open_time=row.open_time,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    trades=row.trades,
                    is_closed=True,
                )
                for row in parsed
            )
        written = bars_store.write_series(
            SeriesKey(venue=PERP_VENUE, symbol=symbol, timeframe=TIMEFRAME), rows
        )
        bar_series += 1
        bars += written

    funding_series = funding_rows = 0
    for symbol, objects in sorted(index.for_tree(FuturesTree.FUNDING_RATE).items()):
        settlements: list[StoredFunding] = []
        for item in objects:
            payload = payload_of(item)
            if payload is None:
                continue
            try:
                parsed_funding = parse_funding(payload, source=item.name)
            except Exception as error:
                unreadable[item.key] = f"{type(error).__name__}: {error}"
                continue
            settlements.extend(
                StoredFunding(
                    settled_at=row.settled_at,
                    interval_hours=row.interval_hours,
                    rate=row.rate,
                )
                for row in parsed_funding
            )
        funding_rows += funding_store.write_series(PERP_VENUE, symbol, settlements)
        funding_series += 1

    premium_series = premium_rows = 0
    for symbol, objects in sorted(index.for_tree(FuturesTree.PREMIUM_INDEX).items()):
        premiums: list[StoredPremium] = []
        for item in objects:
            payload = payload_of(item)
            if payload is None:
                continue
            try:
                parsed_premium = parse_premium_index(payload, source=item.name)
            except Exception as error:
                unreadable[item.key] = f"{type(error).__name__}: {error}"
                continue
            premiums.extend(
                StoredPremium(open_time=row.open_time, close=row.close) for row in parsed_premium
            )
        premium_rows += premium_store.write_series(PERP_VENUE, symbol, premiums)
        premium_series += 1

    return FuturesIngestReport(
        bar_series=bar_series,
        bars=bars,
        funding_series=funding_series,
        funding_rows=funding_rows,
        premium_series=premium_series,
        premium_rows=premium_rows,
        unreadable=unreadable,
        seconds=time.monotonic() - started,
    )


# ---------------------------------------------------------------------------
# Stage 4: the perpetual listing calendar
# ---------------------------------------------------------------------------


def build_calendar(index: FuturesIndex) -> BinanceListingCalendar:
    """Turn month presence into listing intervals, through the shared rule.

    Presence is taken from the **kline** tree rather than the funding one. A
    contract that trades has bars; funding is a property of a contract that is
    live, and this archive holds kline-months with no funding object beside
    them. Using funding presence as the membership evidence would delist a
    contract on the strength of a missing cash-flow file.
    """
    per_symbol = index.for_tree(FuturesTree.KLINES)
    if not per_symbol:
        raise SextantError("The index holds no perpetual kline months to build a calendar from.")
    every_month = sorted({item.month for objects in per_symbol.values() for item in objects})
    held = months_between(every_month[0], every_month[-1])
    presence = {
        symbol: tuple(month in {item.month for item in objects} for month in held)
        for symbol, objects in sorted(per_symbol.items())
    }
    return BinanceListingCalendar.from_presence(PERP_VENUE, presence, held)


def not_evaluable_months(index: FuturesIndex) -> Mapping[str, tuple[str, ...]]:
    """Kline months with no funding object beside them, by symbol.

    These are the months a carry construction cannot evaluate. A missing funding
    file is not zero funding, and treating it as zero would fabricate exactly
    the quantity the family is about.
    """
    klines = index.for_tree(FuturesTree.KLINES)
    funding = index.for_tree(FuturesTree.FUNDING_RATE)
    gaps: dict[str, tuple[str, ...]] = {}
    for symbol, objects in sorted(klines.items()):
        published = {item.month.label for item in funding.get(symbol, ())}
        missing = tuple(item.month.label for item in objects if item.month.label not in published)
        if missing:
            gaps[symbol] = missing
    return gaps


def total_missing(gaps: Mapping[str, Iterable[str]]) -> int:
    """How many symbol-months are not evaluable in total."""
    return sum(len(tuple(value)) for value in gaps.values())

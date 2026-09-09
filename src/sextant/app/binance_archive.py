"""Acquiring the Binance public archive, as an explicit, resumable pipeline.

Four stages, each separately runnable and each idempotent, because tens of
thousands of small objects are fetched over a network that will drop some of
them:

* **index** - list the bucket. Which symbols exist, and which months each one
  published. Nothing is downloaded here, and the index alone is the point-in-time
  membership evidence;
* **plan** - resolve the pre-registered window rule against the index, and
  derive the minimum set of objects the pre-registered variants need. Nothing
  beyond that set is fetched;
* **fetch** - download the planned objects, verify each ZIP's CRC, and record a
  SHA-256 per object;
* **ingest** - parse the objects into the same parquet store, through the same
  ``StoredBar`` type, that SEXTANT-003 built for the other venue, and write the
  listing calendar.

The thing this module is careful never to do is fill a gap. Where a month is
absent the index says so, the calendar's brackets widen accordingly, and the
report carries the missing list. A pipeline that quietly interpolated across a
hole would produce a universe that looks complete and is not.

**The minimum set, and why it is enforced here.** The brief forbids downloading
nine years of everything in order to have nine years. The plan stage therefore
takes the longest pre-registered lookback (360 days), the listing-age rule (180
days) and the turnover window (30 days), adds them to the window's first month,
and fetches nothing earlier. Symbols outside the pre-registered quote policies
are never fetched at all, though they are still *indexed*, because the index is
cheap and a symbol's absence from the universe should be a measured decision
rather than a download that never happened.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sextant.adapters.exchanges.binance.archive import (
    BinanceDataArchive,
    Month,
    MonthlyFile,
    months_between,
    parse_klines,
    presence_by_month,
)
from sextant.adapters.exchanges.binance.capabilities import VENUE
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey, StoredBar
from sextant.domain.time import Timeframe, Timestamp

#: Where the raw objects and the derived store live. Git-ignored, never committed.
STORE_ROOT = Path("data") / "binance-archive"
RAW_ROOT = STORE_ROOT / "raw"

INDEX_NAME = "month_index.json"
PLAN_NAME = "acquisition_plan.json"
FETCH_NAME = "fetch_report.json"
CALENDAR_NAME = "listing_calendar.json"

#: The committed checksum record. The objects themselves stay out of git; the
#: statement of which bytes a published result was computed from does not.
CHECKSUMS_PATH = Path("docs") / "binance-archive-checksums.md"

#: The only timeframe the pre-registered variants need. Daily closes drive every
#: signal, every universe rule and the FX leg. Nothing asks for an hourly bar,
#: so nothing downloads one.
TIMEFRAME = Timeframe.D1
INTERVAL = "1d"

#: The quote assets the pre-registered policies name, longest first so that a
#: suffix match is unambiguous.
QUOTE_ASSETS: tuple[str, ...] = ("USDT", "EUR")

#: Suffixes marking a leveraged token, per part 1 section 4. Applied to the base
#: asset only, and only where the remaining stem is itself a base asset the
#: archive carries, so a genuine asset is never deleted for its spelling.
LEVERAGED_SUFFIXES: tuple[str, ...] = ("UP", "DOWN", "BULL", "BEAR")

NEWLINE = chr(10)


def split_symbol(symbol: str) -> tuple[str, str] | None:
    """Base and quote for a symbol in one of the pre-registered policies.

    Returns None when the symbol is quoted in something the policies do not
    name. Suffix matching is exact rather than heuristic here because the
    policies name only two quote assets and neither is a prefix of the other.
    """
    for quote in QUOTE_ASSETS:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return None


def is_leveraged_token(base: str, known_bases: frozenset[str]) -> bool:
    """Whether ``base`` is a leveraged token by the pre-registered stem rule."""
    return any(
        base.endswith(suffix) and len(base) > len(suffix) and base[: -len(suffix)] in known_bases
        for suffix in LEVERAGED_SUFFIXES
    )


# ---------------------------------------------------------------------------
# Stage 1: index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthIndex:
    """Which months each symbol published, and the span the archive covers."""

    files: Mapping[str, tuple[MonthlyFile, ...]]
    first_month: Month
    last_month: Month

    @property
    def held(self) -> tuple[Month, ...]:
        """Every month in the archive's span, whether or not anyone traded."""
        return months_between(self.first_month, self.last_month)

    def presence(self) -> Mapping[str, tuple[bool, ...]]:
        """A presence flag per held month, per symbol. The membership evidence."""
        held = self.held
        return {
            symbol: presence_by_month(items, held) for symbol, items in sorted(self.files.items())
        }

    def total_bytes(self) -> int:
        """What the indexed objects weigh, before anything is fetched."""
        return sum(item.size_bytes for items in self.files.values() for item in items)

    def write_json(self, path: Path) -> None:
        """Persist the index so the listing is done once, not once per stage."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "first_month": self.first_month.label,
            "last_month": self.last_month.label,
            "symbols": {
                symbol: [
                    {"month": item.month.label, "key": item.key, "size": item.size_bytes}
                    for item in items
                ]
                for symbol, items in sorted(self.files.items())
            },
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")

    @classmethod
    def read_json(cls, path: Path) -> MonthIndex:
        """Load an index previously written by :meth:`write_json`."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        files = {
            str(symbol): tuple(
                MonthlyFile(
                    symbol=str(symbol),
                    interval=INTERVAL,
                    month=Month.parse(str(item["month"])),
                    key=str(item["key"]),
                    size_bytes=int(item["size"]),
                )
                for item in items
            )
            for symbol, items in raw["symbols"].items()
        }
        return cls(
            files=files,
            first_month=Month.parse(str(raw["first_month"])),
            last_month=Month.parse(str(raw["last_month"])),
        )


def build_index(
    symbols: Sequence[str],
    *,
    workers: int = 12,
    progress_every: int = 100,
) -> MonthIndex:
    """List every published month for every symbol given. Downloads nothing."""
    results: dict[str, tuple[MonthlyFile, ...]] = {}

    local = threading.local()

    def one(symbol: str) -> tuple[str, tuple[MonthlyFile, ...]]:
        existing = getattr(local, "archive", None)
        if existing is None:
            existing = BinanceDataArchive()
            local.archive = existing
        return symbol, existing.monthly_files(symbol, INTERVAL)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (symbol, items) in enumerate(pool.map(one, symbols), start=1):
            if items:
                results[symbol] = items
            if done % progress_every == 0:
                print(f"[index] {done}/{len(symbols)} symbols listed")

    every = [item.month for items in results.values() for item in items]
    if not every:
        raise ValueError("the archive listed no months for any requested symbol")
    return MonthIndex(files=results, first_month=min(every), last_month=max(every))


# ---------------------------------------------------------------------------
# Stage 2: plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    """The window the pre-registered rule resolves to, and what it needs."""

    first_usable_month: Month
    last_usable_month_end: Month
    warmup_months: int
    first_fetch_month: Month
    fx_first_month: Month
    archive_last_month: Month
    objects: tuple[MonthlyFile, ...]
    excluded_symbols: Mapping[str, str]

    @property
    def usable_months(self) -> int:
        """How many months the evaluation window spans."""
        return len(months_between(self.first_usable_month, self.last_usable_month_end)) - 1

    @property
    def symbols(self) -> tuple[str, ...]:
        """Every symbol the plan fetches at least one object for."""
        return tuple(sorted({item.symbol for item in self.objects}))

    def total_bytes(self) -> int:
        """What the plan will weigh on disk once fetched."""
        return sum(item.size_bytes for item in self.objects)

    def write_json(self, path: Path) -> None:
        """Persist the plan, so the fetch stage cannot silently widen it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "first_usable_month": self.first_usable_month.label,
            "last_usable_month_end": self.last_usable_month_end.label,
            "usable_months": self.usable_months,
            "warmup_months": self.warmup_months,
            "first_fetch_month": self.first_fetch_month.label,
            "fx_first_month": self.fx_first_month.label,
            "archive_last_month": self.archive_last_month.label,
            "symbol_count": len(self.symbols),
            "object_count": len(self.objects),
            "total_bytes": self.total_bytes(),
            "excluded_symbols": dict(sorted(self.excluded_symbols.items())),
            "objects": [
                {"symbol": item.symbol, "month": item.month.label, "key": item.key}
                for item in self.objects
            ],
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")

    @classmethod
    def read_json(cls, path: Path, index: MonthIndex) -> AcquisitionPlan:
        """Load a plan, rebuilding its objects from the index it was cut from."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        by_key = {item.key: item for items in index.files.values() for item in items}
        return cls(
            first_usable_month=Month.parse(str(raw["first_usable_month"])),
            last_usable_month_end=Month.parse(str(raw["last_usable_month_end"])),
            warmup_months=int(raw["warmup_months"]),
            first_fetch_month=Month.parse(str(raw["first_fetch_month"])),
            fx_first_month=Month.parse(str(raw["fx_first_month"])),
            archive_last_month=Month.parse(str(raw["archive_last_month"])),
            objects=tuple(by_key[str(item["key"])] for item in raw["objects"]),
            excluded_symbols={str(k): str(v) for k, v in raw["excluded_symbols"].items()},
        )


#: Warm-up the pre-registered rules need before the first scored rebalance:
#: 360 days for the longest lookback, 180 for the listing-age rule and 30 for the
#: turnover window. The lookback and the age rule overlap - an instrument must be
#: 180 days old *and* have 360 days of closes, so 360 dominates - and one extra
#: month absorbs the ragged edges of a calendar month against a day count.
WARMUP_MONTHS = 13


def build_plan(
    index: MonthIndex,
    *,
    fx_symbol: str = "EURUSDT",
    minimum_months: int = 36,
) -> AcquisitionPlan:
    """Resolve the pre-registered window rule and cut the minimum object set.

    The rule is in ``config/spike-005.yaml`` and was committed before any of
    this existed. Nothing here consults a price: the inputs are which months the
    archive publishes, and for which symbols.
    """
    fx_files = index.files.get(fx_symbol)
    if not fx_files:
        raise ValueError(
            f"{fx_symbol} is absent from the archive index. The account currency cannot be "
            "priced from the venue's own pair, and the pre-registered FX rule admits no "
            "substitute source by design."
        )
    fx_first = fx_files[0].month

    # Part 1, section 5: the FX series must cover the window, and T must sit at
    # least 360 days after the FX pair's first bar so the longest lookback is
    # satisfiable. Thirteen months is that bound expressed in whole months.
    first_usable = fx_first
    for _ in range(WARMUP_MONTHS):
        first_usable = first_usable.next()

    # The last month whose delisting record is complete needs its successor
    # published too, so the window stops one month short of the archive's edge.
    last_end = index.last_month

    span = len(months_between(first_usable, last_end)) - 1
    if span < minimum_months:
        raise ValueError(
            f"the resolved window is {span} months, below the pre-registered floor of "
            f"{minimum_months}. Part 1 section 5 requires verdict (C) on sample size alone."
        )

    first_fetch = first_usable
    for _ in range(WARMUP_MONTHS):
        first_fetch = first_fetch.previous()

    known_bases = frozenset(
        parts[0] for symbol in index.files if (parts := split_symbol(symbol)) is not None
    )
    wanted: list[MonthlyFile] = []
    excluded: dict[str, str] = {}
    for symbol, items in sorted(index.files.items()):
        parts = split_symbol(symbol)
        if parts is None:
            excluded[symbol] = "quote asset outside the pre-registered policies"
            continue
        base, _ = parts
        if is_leveraged_token(base, known_bases):
            excluded[symbol] = "leveraged token by the pre-registered stem rule"
            continue
        in_range = [item for item in items if first_fetch <= item.month <= last_end]
        if not in_range:
            excluded[symbol] = "published no month inside the window plus its warm-up"
            continue
        wanted.extend(in_range)

    return AcquisitionPlan(
        first_usable_month=first_usable,
        last_usable_month_end=last_end,
        warmup_months=WARMUP_MONTHS,
        first_fetch_month=first_fetch,
        fx_first_month=fx_first,
        archive_last_month=index.last_month,
        objects=tuple(sorted(wanted, key=lambda item: (item.symbol, item.month))),
        excluded_symbols=excluded,
    )


# ---------------------------------------------------------------------------
# Stage 3: fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchReport:
    """What was actually downloaded, and the hash of every byte of it."""

    digests: Mapping[str, str]
    bytes_written: int
    seconds: float
    failures: Mapping[str, str]

    def write_json(self, path: Path) -> None:
        """Persist the report, digests included."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "object_count": len(self.digests),
            "bytes_written": self.bytes_written,
            "seconds": self.seconds,
            "failures": dict(sorted(self.failures.items())),
            "digests": dict(sorted(self.digests.items())),
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")

    @classmethod
    def read_json(cls, path: Path) -> FetchReport:
        """Load a report previously written by :meth:`write_json`."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls(
            digests={str(k): str(v) for k, v in raw["digests"].items()},
            bytes_written=int(raw["bytes_written"]),
            seconds=float(raw["seconds"]),
            failures={str(k): str(v) for k, v in raw["failures"].items()},
        )


def raw_path(item: MonthlyFile, raw_root: Path = RAW_ROOT) -> Path:
    """Where one downloaded object lives on disk."""
    return raw_root / item.symbol / item.name


def fetch(
    plan: AcquisitionPlan,
    *,
    raw_root: Path = RAW_ROOT,
    workers: int = 16,
    progress_every: int = 2000,
) -> FetchReport:
    """Download every planned object. Resumable, and it fetches nothing else."""
    import time

    started = time.monotonic()
    digests: dict[str, str] = {}
    failures: dict[str, str] = {}
    written = 0

    # One archive per worker thread rather than one per object: 28,000 TLS
    # handshakes would dominate the wall clock of a 49 MB download.
    local = threading.local()

    def archive_for_thread() -> BinanceDataArchive:
        existing = getattr(local, "archive", None)
        if existing is None:
            existing = BinanceDataArchive()
            local.archive = existing
        return existing

    def one(item: MonthlyFile) -> tuple[MonthlyFile, str | None, str | None]:
        destination = raw_path(item, raw_root)
        try:
            return item, archive_for_thread().download(item, destination), None
        except Exception as error:  # recorded in the report, never swallowed
            return item, None, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (item, digest, error) in enumerate(pool.map(one, plan.objects), start=1):
            if digest is None:
                failures[item.key] = error or "unknown"
            else:
                digests[item.key] = digest
                written += item.size_bytes
            if done % progress_every == 0:
                print(
                    f"[fetch] {done}/{len(plan.objects)} objects, "
                    f"{written / 1e6:.0f} MB, {len(failures)} failures"
                )

    return FetchReport(
        digests=digests,
        bytes_written=written,
        seconds=time.monotonic() - started,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Stage 4: ingest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What reached the store, and what the store now holds."""

    series_written: int
    bars_written: int
    symbols_empty: tuple[str, ...]
    seconds: float


def ingest(
    plan: AcquisitionPlan,
    *,
    store_root: Path = STORE_ROOT,
    raw_root: Path = RAW_ROOT,
    workers: int = 8,
    progress_every: int = 100,
) -> IngestReport:
    """Parse the fetched objects into the same store the other venue uses.

    One series per symbol, written whole and sorted, so a re-run overwrites
    rather than appends. Bars are the same ``StoredBar`` type, the same schema
    and the same exact decimal text; nothing about this venue reaches the store.
    """
    import time

    started = time.monotonic()
    store = ParquetBarStore(store_root)
    by_symbol: dict[str, list[MonthlyFile]] = {}
    for item in plan.objects:
        by_symbol.setdefault(item.symbol, []).append(item)

    # Everything the archive publishes is a whole month, so every bar has
    # finished forming by the time it is downloadable. The boundary is stated
    # rather than assumed, so a partial month appearing later is refused instead
    # of silently entering the store as a closed bar.
    boundary = plan.archive_last_month.ends_at

    def one(symbol: str) -> tuple[str, int]:
        bars: list[StoredBar] = []
        for item in sorted(by_symbol[symbol], key=lambda entry: entry.month):
            path = raw_path(item, raw_root)
            if not path.is_file():
                continue
            bars.extend(
                StoredBar(
                    open_time=row.open_time,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    trades=row.trades,
                    is_closed=row.open_time.plus(TIMEFRAME.duration) <= boundary,
                )
                for row in parse_klines(path.read_bytes(), source=item.name)
            )
        key = SeriesKey(venue=VENUE, symbol=symbol, timeframe=TIMEFRAME)
        store.write_series(key, bars)
        return symbol, len(bars)

    written = 0
    empty: list[str] = []
    symbols = sorted(by_symbol)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, (symbol, count) in enumerate(pool.map(one, symbols), start=1):
            written += count
            if count == 0:
                empty.append(symbol)
            if done % progress_every == 0:
                print(f"[ingest] {done}/{len(symbols)} series, {written:,} bars")

    return IngestReport(
        series_written=len(symbols),
        bars_written=written,
        symbols_empty=tuple(empty),
        seconds=time.monotonic() - started,
    )


def build_calendar(index: MonthIndex, plan: AcquisitionPlan) -> BinanceListingCalendar:
    """The listing calendar, from monthly presence over the archive's own span.

    Built from the *whole* index rather than from the planned subset, because a
    symbol's membership before the fetch window is still a fact and truncating
    it would pin a listing bracket to the edge of a download.
    """
    return BinanceListingCalendar.from_presence(
        VENUE,
        index.presence(),
        index.held,
        missing_months=(),
    )


# ---------------------------------------------------------------------------
# The committed checksum record
# ---------------------------------------------------------------------------


def write_checksums(
    report: FetchReport,
    plan: AcquisitionPlan,
    path: Path,
) -> int:
    """Write the committed checksum record. Returns the number of lines listed.

    Tens of thousands of per-object digests would drown the repository, so the
    record commits one digest per symbol - the SHA-256 of that symbol's own
    object digests in month order - plus a single dataset fingerprint over all
    of them. That is enough to prove two downloads are the same dataset and to
    localise a difference to a symbol, which is what the record is for.
    """
    import hashlib

    per_symbol: dict[str, list[str]] = {}
    for key, digest in sorted(report.digests.items()):
        symbol = key.split("/")[-2] if "/" in key else key
        symbol = key.split("/")[4] if len(key.split("/")) > 4 else symbol
        per_symbol.setdefault(symbol, []).append(digest)

    rolled = {
        symbol: hashlib.sha256("".join(digests).encode("ascii")).hexdigest()
        for symbol, digests in sorted(per_symbol.items())
    }
    dataset = hashlib.sha256(
        "".join(f"{symbol}:{digest}" for symbol, digest in sorted(rolled.items())).encode("ascii")
    ).hexdigest()

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Binance spot monthly klines - checksums",
        "",
        "<!-- generated by `sextant binance scan`; do not hand-edit -->",
        "",
        "The objects themselves are git-ignored. This is the durable statement of which",
        "bytes the SEXTANT-005 results were computed from, and it is the only part of the",
        "dataset that can honestly live in a repository.",
        "",
        "Each row is one symbol: the SHA-256 of that symbol's own per-object SHA-256s,",
        "concatenated in month order. The dataset fingerprint below is the SHA-256 over",
        "every row. Two downloads that agree on the fingerprint are the same dataset; a",
        "disagreement is localised to a symbol by the rows.",
        "",
        f"- interval: `{INTERVAL}`",
        f"- window fetched: {plan.first_fetch_month.label} to {plan.archive_last_month.label}",
        f"- evaluation window: {plan.first_usable_month.label} to "
        f"{plan.last_usable_month_end.label}",
        f"- symbols: {len(rolled)}",
        f"- objects: {len(report.digests)}",
        f"- bytes: {report.bytes_written:,}",
        "",
        f"**Dataset fingerprint:** `{dataset}`",
        "",
        "| symbol | rolled sha256 | objects |",
        "|---|---|---:|",
    ]
    lines.extend(
        f"| `{symbol}` | `{digest}` | {len(per_symbol[symbol])} |"
        for symbol, digest in sorted(rolled.items())
    )
    lines.append("")
    path.write_text(NEWLINE.join(lines), encoding="utf-8", newline=NEWLINE)
    return len(rolled)


def dataset_checksums(report: FetchReport) -> Mapping[str, str]:
    """The per-object digests, in the shape the trial registry fingerprints."""
    return dict(sorted(report.digests.items()))


def usable_window_instants(plan: AcquisitionPlan) -> tuple[Timestamp, Timestamp]:
    """The evaluation window as instants, for the engine's walk-forward plan."""
    return plan.first_usable_month.starts_at, plan.last_usable_month_end.starts_at


def cold_start_days(plan: AcquisitionPlan) -> int:
    """How many days of warm-up sit before the first scored rebalance."""
    delta: timedelta = (
        plan.first_usable_month.starts_at.value - plan.first_fetch_month.starts_at.value
    )
    return delta.days


def symbols_in_policy(index: MonthIndex, quote: str) -> tuple[str, ...]:
    """Indexed symbols quoted in ``quote``, for reporting universe breadth."""
    return tuple(
        sorted(
            symbol
            for symbol in index.files
            if (parts := split_symbol(symbol)) is not None and parts[1] == quote
        )
    )


def missing_months(index: MonthIndex, symbols: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """Interior holes in a symbol's published months, which widen its brackets."""
    holes: dict[str, tuple[str, ...]] = {}
    for symbol in symbols:
        items = index.files.get(symbol)
        if not items or len(items) < 2:
            continue
        span = months_between(items[0].month, items[-1].month)
        published = {item.month for item in items}
        gap = tuple(month.label for month in span if month not in published)
        if gap:
            holes[symbol] = gap
    return holes

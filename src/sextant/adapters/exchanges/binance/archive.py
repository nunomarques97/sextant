"""This venue's public data archive: what it publishes, and how it is read.

The venue publishes a static, unauthenticated archive at ``data.binance.vision``,
backed by an S3 bucket whose listing endpoint is also public. One object per
``(symbol, interval, month)``, a ZIP holding a single CSV. Nothing here needs a
key, a session or a browser, which is the reason this is the source of record
for the spike rather than the REST endpoint.

Why the archive and not the REST endpoint
------------------------------------------

``/api/v3/klines`` returns a thousand bars per call and would cover a symbol's
whole daily history in two requests. It also answers only for symbols the venue
lists **today**. A delisted symbol is not a thin answer there, it is an error,
and a dataset built from it would be survivorship-biased by construction with no
way to notice after the fact. The archive keeps the files of symbols that are
long gone, which is exactly the property this project needs and the reason
invariant 9 exists.

Presence is the membership evidence
------------------------------------

A monthly file exists for the months a symbol traded, so the set of published
months *is* a membership snapshot sequence in a monthly grain. That is a
considerably tighter bracket than the quarterly one SEXTANT-003 had to work
with, and the inference on top of it is the same rule, shared with the other
venue in :mod:`sextant.adapters.exchanges.membership_intervals`.

One difference from the other venue's archive is worth recording. Kraken ships a
file with zero rows for a pair that was listed and never traded, so
listed-untraded is a state its archive can express. This venue emits a daily
kline for every day a symbol is listed, carrying zero volume when nothing
changed hands, so listed-untraded appears here as a *row* rather than as an
empty file. It survives into the store either way; it is simply carried
differently, and no code should look for an empty file to find it.

Two timestamp units, in one archive
------------------------------------

Files published from 2025 onward carry open times in **microseconds**; earlier
ones use milliseconds. Both appear in a single symbol's history. The unit is
detected per row by magnitude rather than by file date, because a rule keyed on
the publication date would be a rule about when we happened to download.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from sextant.adapters.exchanges.http import HttpTransport, RateLimiter, RequestJournal
from sextant.domain.errors import SextantError
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

#: The public download host. No credential, no session, no browser.
DOWNLOAD_HOST = "https://data.binance.vision"

#: The bucket's public listing endpoint, which is what makes the universe and
#: the month-presence index discoverable without downloading anything.
LISTING_HOST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

#: Where in the bucket the spot monthly klines live.
SPOT_MONTHLY_KLINES = "data/spot/monthly/klines"

#: Open times at or above this are microseconds, below are milliseconds. The cut
#: sits four orders of magnitude above any millisecond epoch this archive can
#: contain and four below any microsecond one, so it cannot misclassify a bar.
_MICROSECOND_FLOOR = 100_000_000_000_000


class ArchiveError(SextantError):
    """The archive could not be read, or answered with something unreadable."""


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    """One kline row, kept as exact decimal text.

    Text rather than ``Decimal`` because this is the storage shape as well as
    the parse shape, and any fixed precision chosen here would silently truncate
    a value we have not met yet. Deliberately not the store's own ``StoredBar``:
    an exchange adapter that imported the storage engines would reach parquet
    and duckdb from inside ``adapters.exchanges``, which the layering contract
    forbids and which would make this venue's parser depend on how bars happen
    to be persisted. The app converts, exactly as it does for the other venue.
    """

    open_time: Timestamp
    open: str
    high: str
    low: str
    close: str
    volume: str
    trades: int


@dataclass(frozen=True, slots=True, order=True)
class Month:
    """One calendar month, and the instant its membership snapshot describes.

    ``ends_at`` is the *exclusive* boundary - midnight UTC on the first day of
    the following month - so consecutive months abut exactly and a delisting
    bracket ``(previous.ends_at, this.ends_at]`` has no gap and no overlap.
    Satisfies :class:`~sextant.adapters.exchanges.membership_intervals.Period`.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ArchiveError(f"Month must be 1-12, got {self.month}")

    @classmethod
    def parse(cls, label: str) -> Month:
        """Build a month from the venue's own ``2024-01`` file-name label."""
        text = label.strip()
        year, _, month = text.partition("-")
        try:
            return cls(year=int(year), month=int(month))
        except ValueError as error:
            raise ArchiveError(f"Unrecognised month label: {label!r}") from error

    @property
    def label(self) -> str:
        """The label the venue's own file names use."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def starts_at(self) -> Timestamp:
        """The first instant of the month."""
        return Timestamp(datetime(self.year, self.month, 1, tzinfo=UTC))

    @property
    def ends_at(self) -> Timestamp:
        """The exclusive boundary at which this month's membership is asserted."""
        return self.next().starts_at

    def next(self) -> Month:
        """The month after this one."""
        if self.month == 12:
            return Month(year=self.year + 1, month=1)
        return Month(year=self.year, month=self.month + 1)

    def previous(self) -> Month:
        """The month before this one."""
        if self.month == 1:
            return Month(year=self.year - 1, month=12)
        return Month(year=self.year, month=self.month - 1)


def months_between(first: Month, last: Month) -> tuple[Month, ...]:
    """Every month from ``first`` to ``last`` inclusive, ascending."""
    if last < first:
        raise ArchiveError(f"{last.label} precedes {first.label}")
    months: list[Month] = [first]
    while months[-1] < last:
        months.append(months[-1].next())
    return tuple(months)


@dataclass(frozen=True, slots=True)
class MonthlyFile:
    """One published object: where it is, and what it weighs."""

    symbol: str
    interval: str
    month: Month
    key: str
    size_bytes: int

    @property
    def url(self) -> str:
        """The public download URL for this object."""
        return f"{DOWNLOAD_HOST}/{self.key}"

    @property
    def name(self) -> str:
        """The file name the venue publishes it under."""
        return self.key.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Reading the bucket
# ---------------------------------------------------------------------------


class BinanceDataArchive:
    """The public archive, read through the one sanctioned HTTP transport.

    It does not open its own connection or set its own retry policy. Everything
    about how this project speaks HTTP - the token bucket, the retry ladder, the
    journal, the distinction between a refusal and a transient failure - lives in
    :mod:`sextant.adapters.exchanges.http` and applies here unchanged. An archive
    fetch is not a special case that gets to be politer or ruder than the rest of
    the system, and a second client would be exactly that.

    Callers that fan out across threads build one archive per thread, because the
    underlying client is not designed to be shared.
    """

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        rate_per_second: float = 24.0,
        timeout: float = 60.0,
    ) -> None:
        self._transport = transport or HttpTransport(
            venue=Venue("binance"),
            base_url=DOWNLOAD_HOST,
            limiter=RateLimiter(rate_per_second=rate_per_second, burst=rate_per_second),
            journal=RequestJournal(),
            timeout_seconds=timeout,
        )

    def close(self) -> None:
        """Release the connection pool."""
        self._transport.close()

    def __enter__(self) -> BinanceDataArchive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- listing -------------------------------------------------------------

    def _list(self, prefix: str, *, delimiter: str | None) -> Iterator[ElementTree.Element]:
        """Page through the bucket listing for one prefix."""
        marker: str | None = None
        while True:
            url = f"{LISTING_HOST}?prefix={quote(prefix, safe='')}&max-keys=1000"
            if delimiter is not None:
                url += f"&delimiter={quote(delimiter, safe='')}"
            if marker is not None:
                url += f"&marker={quote(marker, safe='')}"
            root = ElementTree.fromstring(self._transport.get_bytes(url))
            yield root
            truncated = root.findtext(f"{_S3_NS}IsTruncated")
            if truncated != "true":
                return
            next_marker = root.findtext(f"{_S3_NS}NextMarker")
            if next_marker:
                marker = next_marker
                continue
            keys = [element.text for element in root.iter(f"{_S3_NS}Key") if element.text]
            prefixes = [element.text for element in root.iter(f"{_S3_NS}Prefix") if element.text]
            tail = keys[-1] if keys else (prefixes[-1] if prefixes else None)
            if tail is None or tail == marker:
                return
            marker = tail

    def symbols(self) -> tuple[str, ...]:
        """Every symbol directory the spot monthly kline archive publishes.

        This is the venue's full historical symbol set, delisted names included,
        and it is the only place that set can be had without a private endpoint.
        """
        found: list[str] = []
        for page in self._list(f"{SPOT_MONTHLY_KLINES}/", delimiter="/"):
            for element in page.iter(f"{_S3_NS}CommonPrefixes"):
                text = element.findtext(f"{_S3_NS}Prefix")
                if text:
                    found.append(text.rstrip("/").rsplit("/", 1)[-1])
        return tuple(sorted(set(found)))

    def monthly_files(self, symbol: str, interval: str) -> tuple[MonthlyFile, ...]:
        """Every published month for one symbol and interval, ascending.

        The listing alone is the membership evidence: no bar is downloaded to
        establish which months a symbol traded in.
        """
        prefix = f"{SPOT_MONTHLY_KLINES}/{symbol}/{interval}/"
        suffix = ".zip"
        files: list[MonthlyFile] = []
        for page in self._list(prefix, delimiter=None):
            for element in page.iter(f"{_S3_NS}Contents"):
                key = element.findtext(f"{_S3_NS}Key") or ""
                if not key.endswith(suffix):
                    continue
                stem = key.rsplit("/", 1)[-1][: -len(suffix)]
                label = stem.rsplit("-", 2)
                if len(label) != 3:
                    continue
                files.append(
                    MonthlyFile(
                        symbol=symbol,
                        interval=interval,
                        month=Month.parse(f"{label[1]}-{label[2]}"),
                        key=key,
                        size_bytes=int(element.findtext(f"{_S3_NS}Size") or 0),
                    )
                )
        return tuple(sorted(files, key=lambda item: item.month))

    # -- downloading ---------------------------------------------------------

    def download(self, item: MonthlyFile, destination: Path) -> str:
        """Fetch one object to disk and return its SHA-256.

        Idempotent: an existing file of the right shape is hashed and left
        alone, so a re-run after an interruption resumes rather than restarts.
        The ZIP's own CRC is checked before the bytes are accepted, which is
        what makes a truncated download an error here rather than a short series
        three stages downstream.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == item.size_bytes:
            return sha256_of(destination)
        payload = self._transport.get_bytes(item.url)
        _verify_zip(payload, item)
        destination.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()


def _verify_zip(payload: bytes, item: MonthlyFile) -> None:
    """Reject a body that is not an intact ZIP holding exactly one CSV."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            bad = bundle.testzip()
            if bad is not None:
                raise ArchiveError(f"{item.name}: CRC failure in {bad}")
            names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
    except zipfile.BadZipFile as error:
        raise ArchiveError(f"{item.name}: not a readable ZIP") from error
    if len(names) != 1:
        raise ArchiveError(f"{item.name}: expected one CSV, found {len(names)}")


def sha256_of(path: Path) -> str:
    """SHA-256 of a file on disk, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Reading the rows
# ---------------------------------------------------------------------------

#: Column positions in the venue's kline CSV. Declared rather than inferred, so
#: a shape change is a diff in this file instead of a surprise downstream.
_OPEN_TIME = 0
_OPEN = 1
_HIGH = 2
_LOW = 3
_CLOSE = 4
_VOLUME = 5
_TRADES = 8
_MINIMUM_COLUMNS = 9


def parse_klines(payload: bytes, *, source: str = "") -> tuple[ArchiveRow, ...]:
    """Turn one monthly ZIP into stored bars, exact decimal text preserved.

    Prices arrive as text and leave as text. Nothing here parses a price into a
    number: ``Decimal(str(...))`` round-trips exactly for every value a venue can
    emit, and a float would be a defect under invariant 4. See ADR 0005.

    Whether a row is closed is not decided here. The archive publishes only
    whole months, so in practice every row is, but the boundary that decides it
    belongs to the caller that knows where the archive stops.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        name = next(item for item in bundle.namelist() if item.lower().endswith(".csv"))
        text = bundle.read(name).decode("utf-8")
    rows: list[ArchiveRow] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < _MINIMUM_COLUMNS:
            continue
        raw = row[_OPEN_TIME].strip()
        if not raw or not (raw[0].isdigit()):
            continue  # the header row files published from 2025 carry
        rows.append(
            ArchiveRow(
                open_time=Timestamp.from_epoch_millis(_epoch_millis(raw, source)),
                open=row[_OPEN].strip(),
                high=row[_HIGH].strip(),
                low=row[_LOW].strip(),
                close=row[_CLOSE].strip(),
                volume=row[_VOLUME].strip(),
                trades=int(float(row[_TRADES].strip() or 0)),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.open_time))


def _epoch_millis(raw: str, source: str) -> int:
    """Normalise the archive's two timestamp units to milliseconds.

    Files published from 2025 onward carry microseconds; earlier ones carry
    milliseconds, and both appear inside a single symbol's history. The unit is
    decided by magnitude, per row, because a rule keyed on the file's
    publication date would be a rule about when we happened to download.
    """
    try:
        value = int(raw)
    except ValueError as error:
        raise ArchiveError(f"{source}: unreadable open time {raw!r}") from error
    if value >= _MICROSECOND_FLOOR:
        if value % 1000 != 0:
            raise ArchiveError(f"{source}: microsecond open time {value} is not a whole ms")
        return value // 1000
    return value


def presence_by_month(
    files: Iterable[MonthlyFile],
    held: Sequence[Month],
) -> tuple[bool, ...]:
    """A presence flag per held month, for one symbol's published files."""
    published = {item.month for item in files}
    return tuple(month in published for month in held)

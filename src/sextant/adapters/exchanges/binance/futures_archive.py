"""The venue's USD-margined futures archive: funding, perpetual bars, premium.

The same static, unauthenticated bucket SEXTANT-005 read for spot, a different
tree inside it. Three object families matter here and they are listed in
:class:`FuturesTree`:

``fundingRate``
    ``calc_time``, ``funding_interval_hours``, ``last_funding_rate``: the
    realised funding payment between the long and the short, at the instant it
    was made. Not a forecast. A holder at ``calc_time`` received or paid exactly
    this. Verified against the venue's own ``/fapi/v1/fundingRate`` endpoint
    before any bulk download - ten of ten settlements matched to the last
    decimal - which is what establishes that ``calc_time`` is the settlement
    instant rather than the start of the interval it was computed over.

``klines``
    the perpetual's own OHLCV, in the same twelve-column shape as the spot tree,
    so :func:`sextant.adapters.exchanges.binance.archive.parse_klines` reads it
    unchanged.

``premiumIndexKlines``
    the premium index, which is the perpetual expressed against the venue's own
    index price and is therefore the basis, directly. Its values go **negative**
    routinely, so it is parsed into signed decimal text and never into a
    :class:`~sextant.domain.money.Price`.

Two differences from the spot tree, both load-bearing
------------------------------------------------------

**The publisher supplies a digest.** Every object here has a sibling
``.zip.CHECKSUM`` holding the venue's own SHA-256. The spot acquisition had to
settle for hashing what it received and comparing two of its own downloads
against each other; here a download can be checked against what the publisher
says it published. :meth:`BinanceFuturesArchive.download` does that whenever the
sibling exists, and records whether it did.

**One timestamp unit, not two.** The spot tree switched to microsecond open
times in 2025. This tree did not: every object from 2020-01 to 2026-08 carries
milliseconds, confirmed at 2024-12, 2025-06 and 2026-08. The magnitude check is
applied anyway, because a rule that happens to be unnecessary today is cheaper
to keep than to reinstate.

Funding settlements straddle month boundaries
----------------------------------------------

A settlement stamped one millisecond after midnight on the first of the month
appears in that month's object, and occasionally also in the previous one. The
instant is floored to the second and the series is deduplicated on it, so a
symbol's assembled history has exactly one payment per settlement.

Reading only. Nothing here writes to a store, and nothing here knows what a bar
is persisted as.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from sextant.adapters.exchanges.binance.archive import (
    DOWNLOAD_HOST,
    LISTING_HOST,
    ArchiveError,
    Month,
    sha256_of,
)
from sextant.adapters.exchanges.http import HttpTransport, RateLimiter, RequestJournal
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

#: Open and settlement times at or above this are microseconds, below them are
#: milliseconds. Same cut as the spot reader, kept for the same reason.
_MICROSECOND_FLOOR = 100_000_000_000_000


class FuturesTree(StrEnum):
    """Which family of objects a request is about.

    A string enum rather than a bare prefix so that a caller cannot ask for a
    tree this module has not been told how to parse.
    """

    FUNDING_RATE = "fundingRate"
    KLINES = "klines"
    PREMIUM_INDEX = "premiumIndexKlines"
    MARK_PRICE = "markPriceKlines"

    @property
    def prefix(self) -> str:
        """Where in the bucket this family lives."""
        return f"data/futures/um/monthly/{self.value}"

    @property
    def has_interval(self) -> bool:
        """Whether objects in this tree are keyed by bar interval.

        Funding is an event stream and has no interval; everything else is a
        kline family and does.
        """
        return self is not FuturesTree.FUNDING_RATE


@dataclass(frozen=True, slots=True)
class FuturesObject:
    """One published object: which tree it belongs to, where it is, its size."""

    tree: FuturesTree
    symbol: str
    interval: str
    month: Month
    key: str
    size_bytes: int

    @property
    def url(self) -> str:
        """The public download URL."""
        return f"{DOWNLOAD_HOST}/{self.key}"

    @property
    def checksum_url(self) -> str:
        """The sibling object holding the publisher's own SHA-256."""
        return f"{self.url}.CHECKSUM"

    @property
    def name(self) -> str:
        """The file name the venue publishes it under."""
        return self.key.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class FundingRow:
    """One funding settlement, as the venue recorded it.

    ``rate`` is exact decimal text rather than a parsed number, for the same
    reason prices are: any precision chosen here would silently truncate a value
    the venue has not emitted yet. ``interval_hours`` is carried per row because
    the venue moved some symbols from eight hours to four, and a constant in the
    code would turn that fact into an assumption.
    """

    settled_at: Timestamp
    interval_hours: int
    rate: str

    @property
    def as_decimal(self) -> Decimal:
        """The rate as a Decimal, exact from its text."""
        return Decimal(self.rate)


@dataclass(frozen=True, slots=True)
class PremiumRow:
    """One daily premium-index bar. Signed: the premium goes negative often."""

    open_time: Timestamp
    close: str
    """Exact decimal text, signed. Deliberately not a ``Price``: a ``Price``
    refuses a negative amount, and a negative premium is the ordinary state of a
    perpetual trading below its index."""


@dataclass(frozen=True, slots=True)
class DownloadOutcome:
    """What one download produced, and whether the publisher agreed with it."""

    digest: str
    """SHA-256 of the bytes now on disk."""
    publisher_digest: str | None
    """What the sibling ``.CHECKSUM`` said, or None when there is no sibling."""

    @property
    def verified(self) -> bool:
        """Whether the publisher supplied a digest and it matched."""
        return self.publisher_digest is not None and self.publisher_digest == self.digest


class BinanceFuturesArchive:
    """The futures half of the public archive, read through the one transport.

    Holds no state about what has been fetched. Callers that fan out across
    threads build one archive per thread, exactly as they do for the spot
    reader, because the underlying client is not designed to be shared.
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

    def __enter__(self) -> BinanceFuturesArchive:
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
            _assert_prefix_echoed(root, prefix)
            yield root
            if root.findtext(f"{_S3_NS}IsTruncated") != "true":
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

    def symbols(self, tree: FuturesTree) -> tuple[str, ...]:
        """Every symbol directory this tree publishes, delisted names included.

        The listing is the membership evidence for the perpetual universe, the
        same way monthly file presence is for spot. A perpetual that stopped
        trading in 2022 still has its directory here; the live REST endpoint
        would answer for none of them.
        """
        found: list[str] = []
        for page in self._list(f"{tree.prefix}/", delimiter="/"):
            for element in page.iter(f"{_S3_NS}CommonPrefixes"):
                text = element.findtext(f"{_S3_NS}Prefix")
                if text:
                    found.append(text.rstrip("/").rsplit("/", 1)[-1])
        return tuple(sorted(set(found)))

    def monthly_objects(
        self, tree: FuturesTree, symbol: str, *, interval: str = ""
    ) -> tuple[FuturesObject, ...]:
        """Every published month for one symbol and tree, ascending.

        ``.CHECKSUM`` siblings are filtered out here rather than downstream: they
        are metadata about an object, not an object, and a caller counting
        published months must not count each one twice.
        """
        if tree.has_interval and not interval:
            raise ArchiveError(f"{tree.value} objects are keyed by interval; none was given.")
        if not tree.has_interval and interval:
            raise ArchiveError(f"{tree.value} objects carry no interval; got {interval!r}.")
        prefix = f"{tree.prefix}/{symbol}/" + (f"{interval}/" if interval else "")
        found: list[FuturesObject] = []
        for page in self._list(prefix, delimiter=None):
            for element in page.iter(f"{_S3_NS}Contents"):
                key = element.findtext(f"{_S3_NS}Key") or ""
                if not key.endswith(".zip"):
                    continue
                stem = key.rsplit("/", 1)[-1][: -len(".zip")]
                parts = stem.rsplit("-", 2)
                if len(parts) != 3:
                    continue
                found.append(
                    FuturesObject(
                        tree=tree,
                        symbol=symbol,
                        interval=interval,
                        month=Month.parse(f"{parts[1]}-{parts[2]}"),
                        key=key,
                        size_bytes=int(element.findtext(f"{_S3_NS}Size") or 0),
                    )
                )
        return tuple(sorted(found, key=lambda item: item.month))

    # -- downloading ---------------------------------------------------------

    def download(self, item: FuturesObject, destination: Path) -> DownloadOutcome:
        """Fetch one object to disk, verify it, and report both digests.

        Three checks, in this order: the ZIP's own central directory and CRC,
        which makes a truncated download an error here rather than a short series
        three stages later; the publisher's SHA-256 where a sibling
        ``.CHECKSUM`` exists; and idempotence, so a re-run after an interruption
        resumes rather than restarts.

        A publisher digest that disagrees with the bytes is an error and the
        object is not written. That is the one case where "download it again"
        would be the wrong response, because the bytes arrived intact and were
        simply not the bytes that were promised.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == item.size_bytes:
            digest = sha256_of(destination)
            return DownloadOutcome(digest=digest, publisher_digest=None)
        payload = self._transport.get_bytes(item.url)
        _verify_zip(payload, item.name)
        digest = hashlib.sha256(payload).hexdigest()
        published = self.published_digest(item)
        if published is not None and published != digest:
            raise ArchiveError(
                f"{item.name}: the publisher's checksum says {published} but the bytes "
                f"hash to {digest}. The download is intact and is not what was promised."
            )
        destination.write_bytes(payload)
        return DownloadOutcome(digest=digest, publisher_digest=published)

    def published_digest(self, item: FuturesObject) -> str | None:
        """The publisher's own SHA-256 for this object, when it publishes one.

        Returns None when the sibling is absent or unreadable rather than
        raising: a missing digest is a fact about the archive's coverage, which
        the acquisition reports, and not a reason to abandon an object whose CRC
        already checked out.
        """
        try:
            body = self._transport.get_bytes(item.checksum_url)
        except Exception:
            return None
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        head = text.split()[0].strip().lower()
        if len(head) != 64 or any(character not in "0123456789abcdef" for character in head):
            return None
        return head


def _assert_prefix_echoed(root: ElementTree.Element, prefix: str) -> None:
    """Refuse a listing that answers a question we did not ask.

    S3 echoes the ``Prefix`` it was given. A response echoing a different one is
    an answer to a different request - which is what happens when a query string
    is lost between here and the wire - and its contents would be the top of the
    bucket rather than this symbol's months. Without this check that arrives as
    an empty or wrong result, indistinguishable from a symbol the archive does
    not carry, which is exactly the collapse invariant 10 forbids.
    """
    echoed = root.findtext(f"{_S3_NS}Prefix")
    if echoed is None or echoed != prefix:
        raise ArchiveError(
            f"The bucket was asked for prefix {prefix!r} and answered about {echoed!r}. "
            "A listing that answers a different question is not an empty listing."
        )


def _verify_zip(payload: bytes, name: str) -> None:
    """Reject a body that is not an intact ZIP holding exactly one CSV."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            bad = bundle.testzip()
            if bad is not None:
                raise ArchiveError(f"{name}: CRC failure in {bad}")
            names = [item for item in bundle.namelist() if item.lower().endswith(".csv")]
    except zipfile.BadZipFile as error:
        raise ArchiveError(f"{name}: not a readable ZIP") from error
    if len(names) != 1:
        raise ArchiveError(f"{name}: expected one CSV, found {len(names)}")


# ---------------------------------------------------------------------------
# Reading the rows
# ---------------------------------------------------------------------------

_FUNDING_CALC_TIME = 0
_FUNDING_INTERVAL = 1
_FUNDING_RATE = 2
_FUNDING_COLUMNS = 3

_KLINE_OPEN_TIME = 0
_KLINE_CLOSE = 4
_KLINE_COLUMNS = 5


def _csv_rows(payload: bytes) -> Iterator[Sequence[str]]:
    """Every row of the single CSV inside one object, header rows skipped.

    A row whose first field does not begin with a digit or a sign is a header.
    Objects published from 2025 onward carry one; earlier ones do not, and both
    appear inside a single symbol's history.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        name = next(item for item in bundle.namelist() if item.lower().endswith(".csv"))
        text = bundle.read(name).decode("utf-8")
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        head = row[0].strip()
        if not head or not (head[0].isdigit() or head[0] == "-"):
            continue
        yield row


def parse_funding(payload: bytes, *, source: str = "") -> tuple[FundingRow, ...]:
    """Turn one monthly funding object into settlements, ascending.

    Deduplicated on the settlement instant floored to the second. The venue
    stamps some settlements one millisecond late, and a settlement stamped just
    after midnight on the first appears in both that month's object and
    occasionally the previous one; either would otherwise produce two payments
    where the account made one.
    """
    seen: dict[Timestamp, FundingRow] = {}
    for row in _csv_rows(payload):
        if len(row) < _FUNDING_COLUMNS:
            continue
        settled = _settlement_instant(row[_FUNDING_CALC_TIME].strip(), source)
        rate = row[_FUNDING_RATE].strip()
        if not rate:
            continue
        try:
            interval = int(row[_FUNDING_INTERVAL].strip() or 0)
        except ValueError as error:
            raise ArchiveError(f"{source}: unreadable funding interval") from error
        if interval <= 0:
            raise ArchiveError(f"{source}: funding interval must be positive, got {interval}")
        try:
            Decimal(rate)
        except ArithmeticError as error:
            raise ArchiveError(f"{source}: unreadable funding rate {rate!r}") from error
        seen[settled] = FundingRow(settled_at=settled, interval_hours=interval, rate=rate)
    return tuple(seen[at] for at in sorted(seen))


def parse_premium_index(payload: bytes, *, source: str = "") -> tuple[PremiumRow, ...]:
    """Turn one monthly premium-index object into daily closes, ascending.

    Only the close is kept. The high and low of a premium index over a day are
    intraday facts, and nothing registered in this task reads one.
    """
    rows: list[PremiumRow] = []
    for row in _csv_rows(payload):
        if len(row) < _KLINE_COLUMNS:
            continue
        close = row[_KLINE_CLOSE].strip()
        if not close:
            continue
        try:
            Decimal(close)
        except ArithmeticError as error:
            raise ArchiveError(f"{source}: unreadable premium {close!r}") from error
        rows.append(
            PremiumRow(
                open_time=_epoch_millis_to_timestamp(row[_KLINE_OPEN_TIME].strip(), source),
                close=close,
            )
        )
    return tuple(sorted(rows, key=lambda item: item.open_time))


def _settlement_instant(raw: str, source: str) -> Timestamp:
    """A settlement time, floored to the second.

    The venue stamps a settlement one millisecond late often enough that two
    objects can disagree about the same payment by that millisecond. Flooring
    makes the instant the identity of the payment, which is what a series
    assembled across months needs it to be.
    """
    millis = _epoch_millis(raw, source)
    return Timestamp.from_epoch_millis(millis - millis % 1000)


def _epoch_millis_to_timestamp(raw: str, source: str) -> Timestamp:
    """A bar's open time as a timestamp."""
    return Timestamp.from_epoch_millis(_epoch_millis(raw, source))


def _epoch_millis(raw: str, source: str) -> int:
    """Normalise an epoch to milliseconds, deciding the unit by magnitude.

    This tree has published milliseconds throughout, confirmed at 2020-01,
    2024-12, 2025-06 and 2026-08, where the spot tree switched to microseconds in
    2025. The check is kept anyway: it costs one comparison, and a unit change
    that went unnoticed would move every timestamp by three orders of magnitude.
    """
    try:
        value = int(raw)
    except ValueError as error:
        raise ArchiveError(f"{source}: unreadable epoch {raw!r}") from error
    if value >= _MICROSECOND_FLOOR:
        if value % 1000 != 0:
            raise ArchiveError(f"{source}: microsecond epoch {value} is not a whole ms")
        return value // 1000
    return value

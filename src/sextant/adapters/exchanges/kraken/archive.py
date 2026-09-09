"""Reading Kraken's downloadable quarterly OHLCVT archives.

The archives are the only source that carries this venue's delisted pairs at
all. The public API does not: ``AssetPairs`` returns today's survivors and
``OHLC`` answers ``EQuery:Invalid asset pair`` for anything it has removed. So
everything a point-in-time Kraken universe rests on comes through this module.

What one quarterly ZIP contains
-------------------------------

A flat set of ``<PAIR>_<MINUTES>.csv`` files, eight granularities per pair, no
directories and no header row. Each row is
``time,open,high,low,close,volume,trades`` with ``time`` in whole seconds.

**The rule that makes the archives a listing calendar:** each quarterly file
contains the pairs that were listed at the end of that quarter. Measured on the
two quarters we hold, ``Q2_2024`` carries 700 pairs and ``Q3_2024`` carries 762,
and the 13 pairs that vanish between them are exactly the pairs Kraken's own
announcements say stopped trading inside Q3. Membership is therefore read from
*file presence*, never inferred from a gap in a price series.

Two defects, handled rather than discovered
-------------------------------------------

**The final partial quarter of a delisted pair is lost.** ANT traded from
2024-07-01 to 2024-09-25 and that data is nowhere: dropped from Q3, wrong
quarter for Q2. A delisted pair's series therefore ends at a quarter boundary,
up to three months before it actually stopped trading. The bias is optimistic,
because a delisting announcement usually craters the price, and it is handled by
the mark-out haircut in ``sextant.engine.execution.markout`` rather than by a
comment.

**Presence and tradability are different.** ``WAVESEUR`` is present in Q2 2024
with zero rows at every one of the eight granularities, while the announcement
says trading stopped 2024-07-08. An empty file means *listed but not traded*,
which is information. It is carried as its own state and must not be compacted
into "missing" anywhere downstream.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from types import TracebackType

from sextant.domain.errors import SextantError
from sextant.domain.time import Timeframe, Timestamp

#: Granularities the venue ships, in minutes, as they appear in the file names.
ARCHIVE_INTERVAL_MINUTES: tuple[int, ...] = (1, 5, 15, 30, 60, 240, 720, 1440)

#: The subset we ingest, mapped to the file-name suffix. Daily is the working
#: timeframe; hourly is taken at the same time because re-acquiring the archive
#: later means another 4.5 GB through a quota wall that has already refused us.
INGESTED_TIMEFRAMES: Mapping[Timeframe, int] = {
    Timeframe.D1: 1440,
    Timeframe.H1: 60,
}

#: How many fields each CSV row carries.
_ROW_FIELDS = 7

_ARCHIVE_NAME = "Kraken_OHLCVT_Q{quarter}_{year}.zip"


class ArchiveError(SextantError):
    """The archive on disk is not what the manifest or the reader expects."""


class ChecksumMismatch(ArchiveError):
    """A file's contents changed since it was recorded.

    Raised rather than tolerated. These files arrive by hand through a browser,
    and a truncated download is indistinguishable from a small quarter unless
    something checks.
    """

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{path.name} does not match its recorded checksum: expected {expected}, "
            f"got {actual}. The file was re-downloaded, truncated or corrupted."
        )


class PairPresence(StrEnum):
    """What one quarter says about one pair. Three states, not two.

    ``LISTED_UNTRADED`` is the state the brief insists must survive the whole
    pipeline. A pair with a file and no rows was listed and did not trade, which
    is a measurement. A pair with no file was not listed, which is a different
    measurement. Compacting them into "missing" destroys the difference.
    """

    ABSENT = "absent"
    LISTED_UNTRADED = "listed_untraded"
    LISTED_TRADED = "listed_traded"

    @property
    def is_listed(self) -> bool:
        """Whether the venue listed this pair at the end of the quarter."""
        return self is not PairPresence.ABSENT


@dataclass(frozen=True, slots=True, order=True)
class Quarter:
    """One calendar quarter, and the instant its membership snapshot describes.

    ``ends_at`` is the *exclusive* boundary - midnight UTC on the first day of
    the following quarter - so consecutive quarters abut exactly and a delisting
    bracket ``(previous.ends_at, this.ends_at]`` has no gap and no overlap.
    """

    year: int
    quarter: int

    def __post_init__(self) -> None:
        if not 1 <= self.quarter <= 4:
            raise ArchiveError(f"Quarter must be 1-4, got {self.quarter}")

    @classmethod
    def parse(cls, label: str) -> Quarter:
        """Build a quarter from a ``Q3_2024`` or ``2024Q3`` style label."""
        text = label.strip().upper().replace("-", "_")
        if text.startswith("Q") and "_" in text:
            quarter, _, year = text[1:].partition("_")
            return cls(year=int(year), quarter=int(quarter))
        if "Q" in text:
            year, _, quarter = text.partition("Q")
            return cls(year=int(year), quarter=int(quarter))
        raise ArchiveError(f"Unrecognised quarter label: {label!r}")

    @property
    def label(self) -> str:
        """The label used in the venue's own file names."""
        return f"Q{self.quarter}_{self.year}"

    @property
    def archive_name(self) -> str:
        """The file name this quarter's archive is published under."""
        return _ARCHIVE_NAME.format(quarter=self.quarter, year=self.year)

    @property
    def starts_at(self) -> Timestamp:
        """The first instant of the quarter."""
        return Timestamp(datetime(self.year, 3 * (self.quarter - 1) + 1, 1, tzinfo=UTC))

    @property
    def ends_at(self) -> Timestamp:
        """The exclusive boundary at which this quarter's membership is asserted."""
        return self.next().starts_at

    def next(self) -> Quarter:
        """The quarter immediately following this one."""
        if self.quarter == 4:
            return Quarter(year=self.year + 1, quarter=1)
        return Quarter(year=self.year, quarter=self.quarter + 1)

    def __str__(self) -> str:
        return self.label


def quarters_between(first: Quarter, last: Quarter) -> tuple[Quarter, ...]:
    """Every quarter from ``first`` to ``last`` inclusive, in order."""
    if last < first:
        raise ArchiveError(f"{last} precedes {first}")
    walked: list[Quarter] = []
    cursor = first
    while cursor <= last:
        walked.append(cursor)
        cursor = cursor.next()
    return tuple(walked)


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """One quarterly ZIP on disk, with the checksum it was recorded under."""

    quarter: Quarter
    path: Path
    sha256: str
    size_bytes: int

    def verify(self) -> None:
        """Re-hash the file and raise if it no longer matches."""
        actual = sha256_of(self.path)
        if actual != self.sha256:
            raise ChecksumMismatch(self.path, self.sha256, actual)


def sha256_of(path: Path, *, chunk_bytes: int = 1 << 20) -> str:
    """Stream a file through SHA-256. These are hundreds of megabytes each."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """Which quarters are held, which are missing, and what each one hashes to.

    The missing list is as much a part of the record as the present one. A
    calendar built from a gapped set of quarters is not wrong, but every
    interval spanning a gap is wider than it looks, and nothing downstream can
    account for that unless the gap is written down.
    """

    files: Mapping[str, ArchiveFile]
    missing: tuple[Quarter, ...]

    @classmethod
    def of(cls, files: Iterable[ArchiveFile], missing: Iterable[Quarter]) -> ArchiveManifest:
        """Build a manifest from any iterables."""
        return cls(
            files={item.quarter.label: item for item in files},
            missing=tuple(sorted(missing)),
        )

    @property
    def quarters(self) -> tuple[Quarter, ...]:
        """Every quarter actually held, in order."""
        return tuple(sorted(item.quarter for item in self.files.values()))

    @property
    def is_complete(self) -> bool:
        """Whether every quarter asked for is present."""
        return not self.missing

    def verify_all(self) -> None:
        """Re-hash every held file. Raises on the first mismatch."""
        for item in sorted(self.files.values(), key=lambda file: file.quarter):
            item.verify()

    def write_json(self, path: Path) -> int:
        """Persist the manifest. Returns the number of files recorded."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "files": [
                {
                    "quarter": item.quarter.label,
                    "name": item.path.name,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in sorted(self.files.values(), key=lambda file: file.quarter)
            ],
            "missing": [quarter.label for quarter in self.missing],
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return len(self.files)

    @classmethod
    def read_json(cls, path: Path, root: Path) -> ArchiveManifest:
        """Load a manifest previously written by :meth:`write_json`."""
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls.of(
            files=[
                ArchiveFile(
                    quarter=Quarter.parse(str(item["quarter"])),
                    path=root / str(item["name"]),
                    sha256=str(item["sha256"]),
                    size_bytes=int(item["size_bytes"]),
                )
                for item in raw["files"]
            ],
            missing=[Quarter.parse(str(label)) for label in raw["missing"]],
        )


def scan_archives(root: Path, wanted: Sequence[Quarter]) -> ArchiveManifest:
    """Hash every wanted quarter present under ``root`` and name the rest.

    Hashing is the slow part - roughly 300 MB per file - and it happens once,
    here, so that every subsequent read can be cheap and still verified.
    """
    present: list[ArchiveFile] = []
    absent: list[Quarter] = []
    for quarter in wanted:
        path = root / quarter.archive_name
        if not path.is_file():
            absent.append(quarter)
            continue
        present.append(
            ArchiveFile(
                quarter=quarter,
                path=path,
                sha256=sha256_of(path),
                size_bytes=path.stat().st_size,
            )
        )
    return ArchiveManifest.of(present, absent)


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    """One OHLCVT row, kept as exact decimal text.

    Text rather than ``Decimal`` because this is the storage shape as well as
    the parse shape: the archive mixes scales freely - ``1E+1`` appears verbatim
    in ``ANTEUR_60.csv`` - and any fixed precision chosen here would silently
    truncate a value we have not met yet.
    """

    open_time: Timestamp
    open: str
    high: str
    low: str
    close: str
    volume: str
    trades: int


class QuarterlyArchive:
    """One quarterly ZIP, opened once and read many times.

    The ZIP central directory carries every entry's uncompressed size, so
    presence and emptiness are answered without decompressing anything. That
    matters: a membership snapshot for 762 pairs across eight granularities
    would otherwise mean inflating a third of a gigabyte to learn which files
    have no rows.
    """

    def __init__(self, archive: ArchiveFile, *, verify: bool = True) -> None:
        if verify:
            archive.verify()
        self.archive = archive
        self._zip = zipfile.ZipFile(archive.path)
        self._sizes: dict[str, int] = {
            info.filename: info.file_size for info in self._zip.infolist()
        }

    @property
    def quarter(self) -> Quarter:
        """The quarter this archive describes."""
        return self.archive.quarter

    def __enter__(self) -> QuarterlyArchive:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the ZIP handle."""
        self._zip.close()

    def symbols(self) -> frozenset[str]:
        """Every pair the venue listed at the end of this quarter.

        This is the membership snapshot, and it is read from file presence
        alone. A pair whose files are all empty is still listed and still
        appears here.
        """
        return frozenset(name.rsplit("_", 1)[0] for name in self._sizes if name.endswith(".csv"))

    def presence(self) -> Mapping[str, PairPresence]:
        """Presence and tradability for every pair in this quarter.

        A pair is ``LISTED_UNTRADED`` only when *every* granularity is empty.
        One non-empty file anywhere means it traded, and the daily file being
        empty while the hourly is not is a granularity artefact rather than a
        statement about trading.
        """
        traded: dict[str, bool] = {}
        for name, size in self._sizes.items():
            if not name.endswith(".csv"):
                continue
            symbol = name.rsplit("_", 1)[0]
            traded[symbol] = traded.get(symbol, False) or size > 0
        return {
            symbol: PairPresence.LISTED_TRADED if any_rows else PairPresence.LISTED_UNTRADED
            for symbol, any_rows in traded.items()
        }

    def has_series(self, symbol: str, timeframe: Timeframe) -> bool:
        """Whether this quarter carries a file for ``symbol`` at ``timeframe``."""
        return self._entry_name(symbol, timeframe) in self._sizes

    def read_series(self, symbol: str, timeframe: Timeframe) -> tuple[ArchiveRow, ...]:
        """Every row for one pair at one timeframe, in ascending time order.

        An empty tuple from a file that exists means listed and untraded. A
        missing file raises, because "we never had this" and "this had no
        trades" are the two facts this module exists to keep apart.
        """
        name = self._entry_name(symbol, timeframe)
        if name not in self._sizes:
            raise ArchiveError(
                f"{self.archive.path.name} carries no {timeframe.value} series for "
                f"{symbol}. Absent from the archive is not the same as untraded; "
                "check presence() before reading."
            )
        with self._zip.open(name) as handle:
            body = handle.read().decode("utf-8")
        return tuple(_parse_row(line, symbol, name) for line in body.splitlines() if line.strip())

    def _entry_name(self, symbol: str, timeframe: Timeframe) -> str:
        minutes = INGESTED_TIMEFRAMES.get(timeframe)
        if minutes is None:
            raise ArchiveError(
                f"{timeframe.value} is not ingested from the archive; "
                f"available: {sorted(item.value for item in INGESTED_TIMEFRAMES)}"
            )
        return f"{symbol}_{minutes}.csv"


def open_quarters(manifest: ArchiveManifest, *, verify: bool = True) -> Iterator[QuarterlyArchive]:
    """Open every held quarter in chronological order, closing each after use."""
    for quarter in manifest.quarters:
        archive = QuarterlyArchive(manifest.files[quarter.label], verify=verify)
        try:
            yield archive
        finally:
            archive.close()


def _parse_row(line: str, symbol: str, entry: str) -> ArchiveRow:
    """Turn one CSV line into a row, refusing anything malformed.

    A short or unparseable line raises rather than being skipped. A silently
    dropped row is a hole in a price series, and a hole in a price series is
    precisely what this project refuses to let masquerade as a fact.
    """
    fields = line.split(",")
    if len(fields) != _ROW_FIELDS:
        raise ArchiveError(
            f"{entry}: expected {_ROW_FIELDS} fields for {symbol}, got {len(fields)}: {line!r}"
        )
    try:
        seconds = int(fields[0])
        trades = int(fields[6])
        for value in fields[1:6]:
            Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise ArchiveError(f"{entry}: unparseable row for {symbol}: {line!r}") from exc
    return ArchiveRow(
        open_time=Timestamp.from_epoch_millis(seconds * 1000),
        open=fields[1],
        high=fields[2],
        low=fields[3],
        close=fields[4],
        volume=fields[5],
        trades=trades,
    )

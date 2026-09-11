"""Reading several thousand stored series in one pass.

The universe rules, the liquidity bands and the FX leg all want the same thing:
every symbol's daily closes and turnover across the whole window. Fetched a file
at a time through :class:`~sextant.adapters.storage.bars.ParquetBarStore` that is
roughly 1,600 parquet opens and takes minutes; the null experiment needs it once
per run of the report and the cost is felt every time.

polars reads the whole directory in one scan. That is the entire justification
for the dependency and it is confined to this module. See ADR 0006.

**No monetary type appears here, deliberately.** Prices are stored as text -
ADR 0005 - and they leave here as text. The conversion to ``Decimal`` happens in
the caller, in the layer that is allowed to hold money. That is not fastidiousness:
polars would happily parse a decimal string into a float if anybody asked it to,
and the way to make sure nobody ever does is for this file to have no reason to
mention ``Decimal`` at all. ``tests/unit/test_numeric_boundary.py`` enforces it.

**An absent directory is an empty panel, not a failure.** A store nobody has
ingested into yet is a legitimate state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import polars

from sextant.domain.time import Timeframe
from sextant.domain.venue import Venue


@dataclass(frozen=True, slots=True)
class PanelRow:
    """One daily observation, still in the exact text the store holds.

    ``close`` and ``volume`` are strings because that is what round-trips
    exactly for every value a venue emits. The caller turns them into
    ``Decimal``; nothing here does arithmetic on them.
    """

    open_time_ms: int
    close: str
    volume: str


@dataclass(frozen=True, slots=True)
class RangeRow:
    """One daily observation's high, low and close, still in exact text.

    A second row type rather than three more fields on :class:`PanelRow`, because the
    universe scan reads sixteen hundred series for close and turnover and has no use for
    a range, while the spread estimator of rule E1 reads high, low and close and has no
    use for volume. Two narrow reads are cheaper than one wide one and neither carries a
    column nobody asked for.
    """

    open_time_ms: int
    high: str
    low: str
    close: str


def load_daily_ranges(
    root: Path,
    venue: Venue,
    timeframe: Timeframe = Timeframe.D1,
    *,
    symbols: Sequence[str] | None = None,
) -> Mapping[str, tuple[RangeRow, ...]]:
    """Every stored series' daily high, low and close, keyed by symbol.

    What rule E1's spread estimator reads. The same single polars scan as
    :func:`load_daily_panel`, the same sorting guarantees, and the same rule about
    money: these come out as the exact text the store holds and the caller converts.
    """
    directory = root / "bars" / f"venue={venue.name}" / f"timeframe={timeframe.value}"
    if not directory.is_dir():
        return {}
    wanted = None if symbols is None else set(symbols)
    paths = sorted(
        path for path in directory.glob("*.parquet") if wanted is None or path.stem in wanted
    )
    if not paths:
        return {}

    frame = (
        polars.scan_parquet(paths, include_file_paths="source_path")
        .select(
            polars.col("source_path"),
            polars.col("open_time_ms"),
            polars.col("high"),
            polars.col("low"),
            polars.col("close"),
        )
        .collect()
    )
    panel: dict[str, list[RangeRow]] = {path.stem: [] for path in paths}
    for source, open_time_ms, high, low, close in zip(
        frame.get_column("source_path").to_list(),
        frame.get_column("open_time_ms").to_list(),
        frame.get_column("high").to_list(),
        frame.get_column("low").to_list(),
        frame.get_column("close").to_list(),
        strict=True,
    ):
        panel[Path(str(source)).stem].append(
            RangeRow(
                open_time_ms=int(open_time_ms),
                high=str(high),
                low=str(low),
                close=str(close),
            )
        )
    return {
        symbol: tuple(sorted(rows, key=lambda row: row.open_time_ms))
        for symbol, rows in sorted(panel.items())
    }


def load_daily_panel(
    root: Path,
    venue: Venue,
    timeframe: Timeframe = Timeframe.D1,
    *,
    symbols: Sequence[str] | None = None,
) -> Mapping[str, tuple[PanelRow, ...]]:
    """Every stored series for one venue and timeframe, keyed by symbol.

    ``symbols`` restricts the scan when the caller already knows what it wants.
    ``None`` reads everything, which is what the universe measurement does.

    Ordering is by symbol then by open time, and both are explicit sorts rather
    than whatever order the file system produced. A panel whose row order
    depends on a directory listing is a panel whose downstream results depend on
    a directory listing.
    """
    directory = root / "bars" / f"venue={venue.name}" / f"timeframe={timeframe.value}"
    if not directory.is_dir():
        return {}
    wanted = None if symbols is None else set(symbols)
    paths = sorted(
        path for path in directory.glob("*.parquet") if wanted is None or path.stem in wanted
    )
    if not paths:
        return {}

    frame = (
        polars.scan_parquet(paths, include_file_paths="source_path")
        .select(
            polars.col("source_path"),
            polars.col("open_time_ms"),
            polars.col("close"),
            polars.col("volume"),
        )
        .collect()
    )
    panel: dict[str, list[PanelRow]] = {path.stem: [] for path in paths}
    for source, open_time_ms, close, volume in zip(
        frame.get_column("source_path").to_list(),
        frame.get_column("open_time_ms").to_list(),
        frame.get_column("close").to_list(),
        frame.get_column("volume").to_list(),
        strict=True,
    ):
        panel[Path(str(source)).stem].append(
            PanelRow(open_time_ms=int(open_time_ms), close=str(close), volume=str(volume))
        )
    return {
        symbol: tuple(sorted(rows, key=lambda row: row.open_time_ms))
        for symbol, rows in sorted(panel.items())
    }

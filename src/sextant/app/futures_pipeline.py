"""Driving the futures acquisition stages, and reporting what each one did.

Thin on purpose, exactly as the spot pipeline is: every decision lives in
:mod:`sextant.app.futures_archive` or in the pre-registration it implements.
What lives here is the order the stages run in, what each one writes, and what
it prints, because a stage that runs for forty minutes and prints nothing is a
stage nobody can tell has hung.
"""

from __future__ import annotations

from pathlib import Path

from sextant.adapters.exchanges.binance.futures_archive import FuturesTree
from sextant.app.futures_archive import (
    CALENDAR_NAME,
    FETCH_NAME,
    INDEX_NAME,
    PERP_VENUE,
    RAW_ROOT,
    STORE_ROOT,
    FuturesIndex,
    build_calendar,
    build_index,
    fetch,
    ingest,
    not_evaluable_months,
    total_missing,
)

STAGES = ("index", "fetch", "ingest", "calendar")


def run_stage(stage: str, *, store_root: Path = STORE_ROOT, raw_root: Path = RAW_ROOT) -> None:
    """Run one stage, or every stage in order when given ``all``."""
    if stage == "all":
        for each in STAGES:
            run_stage(each, store_root=store_root, raw_root=raw_root)
        return
    handler = {
        "index": _index,
        "fetch": _fetch,
        "ingest": _ingest,
        "calendar": _calendar,
    }[stage]
    handler(store_root, raw_root)


def _index(store_root: Path, raw_root: Path) -> None:
    """List the bucket. Downloads no bar; the listing IS the membership evidence."""
    del raw_root
    from sextant.adapters.exchanges.binance.futures_archive import BinanceFuturesArchive

    print("[index] listing every perpetual with a funding history")
    with BinanceFuturesArchive() as archive:
        symbols = archive.symbols(FuturesTree.FUNDING_RATE)
    print(f"[index] {len(symbols)} perpetual symbol directories, delisted names included")
    print("[index] nothing is filtered here: the whole tree is indexed and the whole")
    print("[index] tree is fetched, so no universe argument rests on what was downloaded")

    index = build_index(symbols)
    index.write_json(store_root / INDEX_NAME)
    print(
        f"[index] {len(index.all_objects)} objects across {len(TREE_LABELS)} trees, "
        f"{index.total_bytes() / 1e6:.1f} MB indexed"
    )
    for tree, label in TREE_LABELS.items():
        per = index.for_tree(tree)
        print(f"[index]   {label}: {len(per)} symbols, {sum(len(v) for v in per.values())} months")
    gaps = not_evaluable_months(index)
    print(
        f"[index] {total_missing(gaps)} kline-months across {len(gaps)} symbols have no "
        "funding object beside them. Those months are not evaluable for carry and are "
        "never filled with a zero."
    )


def _fetch(store_root: Path, raw_root: Path) -> None:
    """Download every indexed object, verifying against the publisher's digest."""
    index = FuturesIndex.read_json(store_root / INDEX_NAME)
    planned = index.all_objects
    print(f"[fetch] {len(planned)} objects, {index.total_bytes() / 1e6:.1f} MB planned")
    report = fetch(index, raw_root=raw_root)
    report.write_json(store_root / FETCH_NAME)
    print(
        f"[fetch] {len(report.digests)} objects, {report.bytes_written / 1e6:.1f} MB in "
        f"{report.seconds / 60:.1f} minutes, {len(report.failures)} failures"
    )
    print(
        f"[fetch] publisher checksum verified on {report.publisher_verified} objects; "
        f"{report.publisher_absent} objects publish none"
    )
    for key, reason in sorted(report.failures.items()):
        print(f"[fetch]   FAILED {key}: {reason}")


def _ingest(store_root: Path, raw_root: Path) -> None:
    """Parse the downloaded objects into the three stores."""
    index = FuturesIndex.read_json(store_root / INDEX_NAME)
    report = ingest(index, store_root=store_root, raw_root=raw_root)
    print(
        f"[ingest] {report.bar_series} perpetual series, {report.bars} bars; "
        f"{report.funding_series} funding series, {report.funding_rows} settlements; "
        f"{report.premium_series} premium series, {report.premium_rows} rows; "
        f"{report.seconds / 60:.1f} minutes"
    )
    for key, reason in sorted(report.unreadable.items()):
        print(f"[ingest]   UNREADABLE {key}: {reason}")


def _calendar(store_root: Path, raw_root: Path) -> None:
    """Turn month presence into bracketed listing spells for the perpetuals."""
    del raw_root
    index = FuturesIndex.read_json(store_root / INDEX_NAME)
    calendar = build_calendar(index)
    written = calendar.write_json(store_root / CALENDAR_NAME)
    live = sum(1 for entry in calendar.entries.values() if entry.last_seen == calendar.months[-1])
    print(
        f"[calendar] {written} perpetuals, {len(calendar.months)} months "
        f"({calendar.months[0].label} to {calendar.months[-1].label})"
    )
    print(
        f"[calendar] {written - live} contracts stopped publishing before the archive's "
        f"last month; {sum(1 for e in calendar.entries.values() if e.was_relisted)} left "
        "and returned"
    )
    print(f"[calendar] venue recorded as {PERP_VENUE.name}")


#: What each tree is called in the acquisition log, in the order it is reported.
TREE_LABELS: dict[FuturesTree, str] = {
    FuturesTree.FUNDING_RATE: "funding settlements",
    FuturesTree.KLINES: "perpetual daily bars",
    FuturesTree.PREMIUM_INDEX: "premium index",
}

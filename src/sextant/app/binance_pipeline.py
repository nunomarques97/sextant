"""Driving the Binance acquisition stages, and reporting what each one did.

Thin on purpose: every decision lives in :mod:`sextant.app.binance_archive` or
in the pre-registration it implements. What lives here is the order the stages
run in, what each one writes, and what it prints - because a stage that runs for
an hour and prints nothing is a stage nobody can tell has hung.
"""

from __future__ import annotations

from pathlib import Path

from sextant.app.binance_archive import (
    CALENDAR_NAME,
    CHECKSUMS_PATH,
    FETCH_NAME,
    INDEX_NAME,
    INTERVAL,
    PLAN_NAME,
    QUOTE_ASSETS,
    RAW_ROOT,
    STORE_ROOT,
    AcquisitionPlan,
    FetchReport,
    MonthIndex,
    build_calendar,
    build_index,
    build_plan,
    cold_start_days,
    fetch,
    ingest,
    missing_months,
    split_symbol,
    symbols_in_policy,
    write_checksums,
)

STAGES = ("index", "plan", "fetch", "scan", "ingest", "calendar")


def run_stage(stage: str, *, store_root: Path = STORE_ROOT) -> None:
    """Run one stage, or every stage in order when given ``all``."""
    if stage == "all":
        for each in STAGES:
            run_stage(each, store_root=store_root)
        return
    handler = {
        "index": _index,
        "plan": _plan,
        "fetch": _fetch,
        "scan": _scan,
        "ingest": _ingest,
        "calendar": _calendar,
    }[stage]
    handler(store_root)


def _index(store_root: Path) -> None:
    """List the bucket. Downloads no bar; the listing IS the membership evidence."""
    from sextant.adapters.exchanges.binance.archive import BinanceDataArchive

    print("[index] listing every spot symbol directory the archive publishes")
    with BinanceDataArchive() as archive:
        every = archive.symbols()
    print(f"[index] {len(every)} symbol directories in the archive")

    wanted = tuple(symbol for symbol in every if split_symbol(symbol) is not None)
    print(
        f"[index] {len(wanted)} of them are quoted in {', '.join(QUOTE_ASSETS)}, "
        "which is what the pre-registered policies name. The rest are not listed "
        "further and are never fetched."
    )
    index = build_index(wanted)
    index.write_json(store_root / INDEX_NAME)
    print(
        f"[index] {len(index.files)} symbols published at least one {INTERVAL} month; "
        f"span {index.first_month.label} to {index.last_month.label}; "
        f"{index.total_bytes() / 1e6:.0f} MB indexed"
    )
    for quote in QUOTE_ASSETS:
        print(f"[index]   {quote}-quoted: {len(symbols_in_policy(index, quote))}")


def _plan(store_root: Path) -> None:
    """Resolve the pre-registered window rule and cut the minimum object set."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = build_plan(index)
    plan.write_json(store_root / PLAN_NAME)
    print(
        f"[plan] FX pair first published {plan.fx_first_month.label}; "
        f"archive ends {plan.archive_last_month.label}"
    )
    print(
        f"[plan] evaluation window {plan.first_usable_month.label} to "
        f"{plan.last_usable_month_end.label} - {plan.usable_months} months, "
        f"after {cold_start_days(plan)} days of warm-up from "
        f"{plan.first_fetch_month.label}"
    )
    print(
        f"[plan] {len(plan.objects):,} objects across {len(plan.symbols)} symbols, "
        f"{plan.total_bytes() / 1e6:.0f} MB"
    )
    print(f"[plan] {len(plan.excluded_symbols)} symbols excluded before any fetch")


def _fetch(store_root: Path) -> None:
    """Download the planned objects. Resumable; fetches nothing beyond the plan."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = AcquisitionPlan.read_json(store_root / PLAN_NAME, index)
    print(f"[fetch] {len(plan.objects):,} objects planned, {plan.total_bytes() / 1e6:.0f} MB")
    report = fetch(plan, raw_root=RAW_ROOT)
    report.write_json(store_root / FETCH_NAME)
    print(
        f"[fetch] {len(report.digests):,} objects in {report.seconds / 60:.1f} min, "
        f"{report.bytes_written / 1e6:.0f} MB, {len(report.failures)} failures"
    )
    for key, reason in list(report.failures.items())[:10]:
        print(f"[fetch]   FAILED {key}: {reason}")


def _scan(store_root: Path) -> None:
    """Write the committed checksum record over what was actually downloaded."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = AcquisitionPlan.read_json(store_root / PLAN_NAME, index)
    report = FetchReport.read_json(store_root / FETCH_NAME)
    rows = write_checksums(report, plan, CHECKSUMS_PATH)
    print(f"[scan] {rows} symbol rows written to {CHECKSUMS_PATH}")


def _ingest(store_root: Path) -> None:
    """Parse the objects into the same parquet store the other venue uses."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = AcquisitionPlan.read_json(store_root / PLAN_NAME, index)
    report = ingest(plan, store_root=store_root, raw_root=RAW_ROOT)
    print(
        f"[ingest] {report.series_written} series, {report.bars_written:,} bars, "
        f"{report.seconds / 60:.1f} min"
    )
    if report.symbols_empty:
        print(f"[ingest] {len(report.symbols_empty)} series held no rows at all")


def _calendar(store_root: Path) -> None:
    """Build and persist the point-in-time listing calendar."""
    index = MonthIndex.read_json(store_root / INDEX_NAME)
    plan = AcquisitionPlan.read_json(store_root / PLAN_NAME, index)
    calendar = build_calendar(index, plan)
    written = calendar.write_json(store_root / CALENDAR_NAME)
    relisted = calendar.relisted_symbols()
    holes = missing_months(index, index.files)
    print(f"[calendar] {written} entries over {len(calendar.months)} months")
    print(f"[calendar] {len(relisted)} symbols left and returned")
    print(f"[calendar] {len(holes)} symbols have an interior hole widening their brackets")
    delisted_in_window = sum(
        len(calendar.delisted_during(month))
        for month in calendar.months
        if plan.first_usable_month <= month <= plan.last_usable_month_end
    )
    print(f"[calendar] {delisted_in_window} delistings bracketed inside the evaluation window")

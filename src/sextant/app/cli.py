"""Command-line entrypoints.

``sextant status`` resolves configuration and reports what this run would be
allowed to do, without refusing to print when preflight fails.
``sextant run`` performs the full startup sequence and stops, because there is
no engine yet.

This software does not place real orders in its current state.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sextant.adapters.clocks import SystemClock
from sextant.app.config import load_settings, resolve_profile
from sextant.app.credentials import credentials_for, load_dotenv
from sextant.app.preflight import PreflightFailed, PreflightReport, run_preflight
from sextant.app.startup import describe_mode, prepare
from sextant.app.wiring import build_exchange_client
from sextant.domain.errors import SextantError

EXIT_OK = 0
EXIT_STARTUP_REFUSED = 2
#: The pre-registration ordering could not be verified: the results are not
#: committed yet, or the specification's commit is not an ancestor of theirs. A
#: distinct code from a startup refusal, because the run itself did nothing wrong.
EXIT_ORDERING_UNVERIFIED = 3

_LOGGER = logging.getLogger("sextant.app.cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sextant",
        description="Multi-strategy quantitative crypto trading system. "
        "Does not place real orders in its current state.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Configuration profile: backtest | paper | live. Defaults to backtest.",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        type=Path,
        help="Directory holding base.yaml and the profile files.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Report the resolved configuration and preflight.")
    subparsers.add_parser("run", help="Perform the full startup sequence.")
    spike = subparsers.add_parser(
        "spike",
        help="SEXTANT-002 research spike: fetch public market data and measure the universe.",
    )
    spike.add_argument(
        "stage",
        choices=("collect-binance", "collect-kraken", "measure"),
        help="Which stage to run. The collect stages reach the network; measure does not.",
    )
    archive = subparsers.add_parser(
        "archive",
        help="SEXTANT-003: build the point-in-time calendar and bar store from the "
        "downloaded quarterly OHLCVT archives. Reaches no network.",
    )
    archive.add_argument(
        "stage",
        choices=("scan", "calendar", "ingest", "measure", "quarter-end-audit"),
        help="Which stage to run. Each is separately runnable and idempotent.",
    )
    benchmark = subparsers.add_parser(
        "benchmark",
        help="SEXTANT-004: calibrate the null. Runs the passive, random and "
        "single-asset constructs through the walk-forward engine. Reaches no network.",
    )
    benchmark.add_argument(
        "stage",
        choices=("null",),
        help="Which experiment to run.",
    )
    benchmark.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="Override the pre-registered seed count. For a smoke run only: a "
        "published result uses the registered count.",
    )
    binance = subparsers.add_parser(
        "binance",
        help="SEXTANT-005: acquire the Binance public spot archive and ingest it "
        "through the SEXTANT-003 store. The index and fetch stages reach the network.",
    )
    binance.add_argument(
        "stage",
        choices=("index", "plan", "fetch", "scan", "ingest", "calendar", "all"),
        help="Which stage to run. Each is separately runnable and idempotent.",
    )
    spike005 = subparsers.add_parser(
        "spike-005",
        help="SEXTANT-005: run the pre-registered variants, their nulls and their "
        "regimes, then render the results. Reaches no network.",
    )
    spike005.add_argument(
        "stage",
        choices=("run", "report"),
        help="Which stage to run. `run` executes the grid; `report` renders it.",
    )
    spike005.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="Override the pre-registered seed count. For a smoke run only: a "
        "published result uses the registered count.",
    )
    futures = subparsers.add_parser(
        "futures",
        help="SEXTANT-006: acquire the Binance USD-margined futures archive whole - "
        "funding, perpetual bars and the premium index. Index and fetch reach the network.",
    )
    futures.add_argument(
        "stage",
        choices=("index", "fetch", "ingest", "calendar", "all"),
        help="Which stage to run. Each is separately runnable and idempotent.",
    )
    f1 = subparsers.add_parser(
        "spike-006-f1",
        help="SEXTANT-006 family F1, cash-and-carry. `verify` checks the code against "
        "the registered specification and reports the pre-registration ordering. "
        "Reaches no network.",
    )
    f1.add_argument(
        "stage",
        choices=("verify", "ordering", "dataset", "run"),
        help="`verify` runs the drift guard and the commit gate; `ordering` prints the "
        "audit lines the report quotes, and is rerun after the results are committed; "
        "`dataset` measures the acquisition and writes pre-registration part 2; "
        "`run` executes the registered 36-trial grid and writes the result file.",
    )
    f1.add_argument(
        "--seeds",
        type=int,
        default=None,
        help="Override the null seed counts. A smoke run only: the result file records "
        "the override and says it is not publishable.",
    )
    subparsers.add_parser(
        "snapshot-universe",
        help="Record today's venue membership so future delistings need no "
        "reconstruction. Idempotent per UTC day.",
    )
    return parser


def _print_report(report: PreflightReport) -> None:
    print(f"  preflight: {'ok' if report.ok else 'FAILED'}")
    for check in report.checks:
        print(f"    [{check.status.value:>7}] {check.name}: {check.reason}")


def _command_status(profile: str | None, config_dir: Path | None) -> int:
    load_dotenv()
    chosen = resolve_profile(profile)
    settings = load_settings(profile=chosen, config_dir=config_dir)
    exchange = build_exchange_client(settings)
    capabilities = exchange.capabilities()
    credentials = credentials_for(settings.run.venue)
    report = run_preflight(
        settings=settings,
        capabilities=capabilities,
        credentials=credentials,
        withdrawal_permission=exchange.withdrawal_permission(),
    )

    print(f"sextant status (profile: {chosen})")
    print(f"  mode:      {settings.run.mode.value}")
    print(f"  {describe_mode(settings.run.mode)}")
    print(f"  venue:     {settings.run.venue}")
    print(f"  timeframe: {settings.run.timeframe.value}")
    print(f"  seed:      {settings.run.seed}")
    print(f"  effective capabilities ({len(capabilities)}):")
    for capability in capabilities:
        print(f"    - {capability.value}")
    _print_report(report)
    return EXIT_OK


def _command_run(profile: str | None, config_dir: Path | None) -> int:
    result = prepare(profile=profile, config_dir=config_dir)
    metadata = result.metadata
    _LOGGER.info(
        "run started",
        extra={
            "profile": metadata.profile,
            "seed": metadata.seed,
            "started_at": metadata.started_at.isoformat(),
            "skipped_checks": [check.name for check in result.preflight.skipped],
        },
    )
    print(f"sextant run {metadata.run_id} ({metadata.mode.value} on {metadata.venue})")
    print(f"  {describe_mode(metadata.mode)}")
    _print_report(result.preflight)
    _LOGGER.info("no engine is implemented yet; stopping after startup")
    print("  no engine is implemented yet (SEXTANT-001 is bootstrap only); stopping.")
    return EXIT_OK


def _command_spike(stage: str) -> int:
    """Run one research-spike stage.

    Kept behind its own subcommand rather than folded into ``run``: this reads
    public market data for a report, it is not a trading run, and conflating the
    two would put a network fetch on the startup path.
    """
    from sextant.app.spike import collect_binance, collect_kraken, measure_all

    if stage == "collect-binance":
        collect_binance()
    elif stage == "collect-kraken":
        collect_kraken()
    else:
        measure_all()
    return EXIT_OK


def _command_archive(stage: str) -> int:
    """Run one archive stage.

    Imported lazily for the same reason as the spike: these stages pull in the
    parquet store and the venue's archive reader, and ``sextant status`` has no
    business paying for either.
    """
    from sextant.adapters.storage.bars import ParquetBarStore
    from sextant.app import archive_measure
    from sextant.app.archive_ingest import (
        STORE_ROOT,
        build_calendar,
        ingest,
        load_calendar,
        load_manifest,
        scan,
    )

    if stage == "scan":
        scan()
        return EXIT_OK
    manifest = load_manifest()
    if stage == "calendar":
        build_calendar(manifest)
        return EXIT_OK
    if stage == "ingest":
        ingest(manifest, ParquetBarStore(STORE_ROOT), clock=SystemClock())
        return EXIT_OK
    if stage == "quarter-end-audit":
        from sextant.app.quarter_end_audit import run as audit_quarter_end

        audit_quarter_end(STORE_ROOT)
        return EXIT_OK
    archive_measure.measure_all(load_calendar(), ParquetBarStore(STORE_ROOT), manifest)
    return EXIT_OK


def _command_benchmark(stage: str, seeds: int | None) -> int:
    """Run the SEXTANT-004 null calibration.

    Behind its own subcommand rather than folded into ``run``: this produces a
    report, it is not a trading run, and it takes hours at the registered seed
    count.
    """
    from sextant.app.null_baseline import run_all

    if stage != "null":  # pragma: no cover - argparse restricts the choices
        raise SextantError(f"unknown benchmark stage {stage!r}")
    report = run_all(seed_count=seeds)
    print(
        f"  {len(report.constructs)} construct results, "
        f"{len(report.nulls)} null distributions, "
        f"{report.trial_count_including_nulls} trials recorded"
    )
    return EXIT_OK


def _command_snapshot_universe() -> int:
    """Record today's venue membership. The permanent fix for R6."""
    from sextant.app.archive_ingest import STORE_ROOT
    from sextant.app.universe_snapshot import run

    run(STORE_ROOT, SystemClock())
    return EXIT_OK


def _command_binance(stage: str) -> int:
    """SEXTANT-005 acquisition. Each stage is idempotent and resumable."""
    from sextant.app import binance_pipeline

    binance_pipeline.run_stage(stage)
    return EXIT_OK


def _command_futures(stage: str) -> int:
    """SEXTANT-006 acquisition. Each stage is idempotent and resumable."""
    from sextant.app import futures_pipeline

    futures_pipeline.run_stage(stage)
    return EXIT_OK


def _command_spike_006_f1(stage: str, seeds: int | None = None) -> int:
    """The F1 guards, runnable on their own with no dataset present.

    `verify` is what a reader runs to confirm that the committed specification and
    the code agree, and that the specification is committed at all. `ordering` is
    what produces the two SHAs and the ancestry between them that the report cites;
    it is rerun after the results have been committed, because the SHA of the commit
    that introduces a results file does not exist while the file is being written.
    """
    from sextant.app import spike_006_f1

    root = Path()
    if stage == "verify":
        spike_006_f1.assert_no_drift()
        provenance = spike_006_f1.registration_provenance(root=root)
        print(f"  specification: {provenance.config_path}")
        print(f"  committed:     {provenance.config_commit.cite()}")
        print(f"  head:          {provenance.head_sha[:12]}")
        budget = spike_006_f1.budget()
        print(
            f"  trial budget:  {budget.maximum_trials} trials "
            f"({len(budget.variants)} variants over {len(budget.cost_cells)} cells), enforced"
        )
        return EXIT_OK
    if stage == "dataset":
        from sextant.app import spike_006_f1_dataset

        written = spike_006_f1_dataset.write_manifest(path=spike_006_f1_dataset.DATASET_PATH)
        payload = json.loads(written.read_text(encoding="utf-8"))
        print(f"  written: {written.as_posix()}")
        print(spike_006_f1_dataset.render_summary(payload))
        return EXIT_OK
    if stage == "run":
        from sextant.app import spike_006_f1_run

        spike_006_f1_run.execute(repository_root=root, seed_override=seeds)
        return EXIT_OK
    audit = spike_006_f1.ordering_audit(root=root)
    for line in audit.lines():
        print(f"  {line}")
    return EXIT_OK if audit.is_verified else EXIT_ORDERING_UNVERIFIED


def _command_spike_005(stage: str, seeds: int | None) -> int:
    """SEXTANT-005. `run` executes the grid; `report` renders what it wrote."""
    if stage == "run":
        from sextant.app import spike_005_run

        spike_005_run.execute(seed_override=seeds)
        return EXIT_OK
    from sextant.app import spike_005_report

    spike_005_report.render()
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint. Returns a process exit code rather than calling sys.exit."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return _command_status(args.profile, args.config_dir)
        if args.command == "spike":
            return _command_spike(args.stage)
        if args.command == "spike-006-f1":
            return _command_spike_006_f1(args.stage, args.seeds)
        if args.command == "archive":
            return _command_archive(args.stage)
        if args.command == "benchmark":
            return _command_benchmark(args.stage, args.seeds)
        if args.command == "binance":
            return _command_binance(args.stage)
        if args.command == "futures":
            return _command_futures(args.stage)
        if args.command == "spike-005":
            return _command_spike_005(args.stage, args.seeds)
        if args.command == "snapshot-universe":
            return _command_snapshot_universe()
        return _command_run(args.profile, args.config_dir)
    except PreflightFailed as exc:
        print(f"startup refused: {exc}", file=sys.stderr)
        _print_report(exc.report)
        return EXIT_STARTUP_REFUSED
    except SextantError as exc:
        print(f"startup refused: {exc}", file=sys.stderr)
        return EXIT_STARTUP_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())

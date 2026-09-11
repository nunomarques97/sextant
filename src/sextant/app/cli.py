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
        choices=(
            "verify",
            "ordering",
            "dataset",
            "run",
            "depth",
            "spread",
            "estimator",
            "contraction",
            "report",
        ),
        help="`verify` runs the drift guard and the commit gate; `ordering` prints the "
        "audit lines the report quotes, and is rerun after the results are committed; "
        "`dataset` measures the acquisition and writes pre-registration part 2; "
        "`run` executes the registered 36-trial grid and writes the result file; "
        "`depth` acquires the order-book sample, but only when rule C3 asked for one; "
        "`spread` acquires the quoted-spread sample, but only when rule S1 fired; "
        "`estimator` computes rule E1's spread estimator and its calibration "
        "against that sample, and reports whether the estimator is adopted; "
        "`contraction` writes amendment 26.1's composition check on the largest "
        "month-on-month fall in pair count; "
        "`report` renders the result file as the results document.",
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


def _command_f1_spread() -> int:
    """Acquire the spread sample, and refuse to acquire it when rule S1 did not fire.

    The same discipline as the depth stage: the rule decides from the executed result
    file, not the person running the command. A stage that downloaded three gigabytes
    because it was invoked would make the registered condition decorative.
    """
    import json as _json

    from sextant.app import spike_006_f1_spread
    from sextant.app.spike_006_f1 import RESULTS_PATH, headline_cell
    from sextant.app.spike_006_f1_analysis import (
        assumption_could_be_carrying_the_verdict,
        rescues,
    )
    from sextant.app.spike_006_f1_world import build_world

    if not RESULTS_PATH.is_file():
        print(f"  no result file at {RESULTS_PATH.as_posix()}; rule S1 has nothing to read.")
        return EXIT_ORDERING_UNVERIFIED
    payload = _json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    items = rescues(payload, cell=headline_cell().label)
    if not assumption_could_be_carrying_the_verdict(items):
        print("  rule S1: with the assumed cost at zero, no variant clears criterion 1.")
        print("  the spread sample is not acquired, and that is the rule's answer.")
        return EXIT_OK
    clearing = [item.variant for item in items if item.clears_criterion_one]
    print(f"  rule S1 fired on: {', '.join(sorted(clearing))}")
    sample = spike_006_f1_spread.acquire(build_world(), raw_root=spike_006_f1_spread.SPREAD_ROOT)
    written = spike_006_f1_spread.write(sample, spike_006_f1_spread.SPREAD_RESULTS)
    print(f"  {sample.megabytes} MB over {len(sample.measured)} symbol-days")
    print(f"  written: {written.as_posix()}")
    return EXIT_OK


def _command_f1_estimator() -> int:
    """Compute rule E1's calibration, and report the adoption it decides.

    Reaches no network and reads no result file's verdict. The three clauses and their
    thresholds were committed before this command produced a number, so what it prints
    is an outcome rather than a choice.
    """
    from sextant.app import spike_006_f1_estimator
    from sextant.app.spike_006_f1 import ESTIMATOR_RULE

    calibration = spike_006_f1_estimator.calibrate()
    written = spike_006_f1_estimator.write(calibration, spike_006_f1_estimator.ESTIMATOR_RESULTS)
    for name, holds in (
        ("ordering", calibration.ordering_holds),
        ("magnitude", calibration.magnitude_holds),
        ("positivity", calibration.positivity_holds),
    ):
        print(f"  {name}: {'holds' if holds else 'does NOT hold'}")
    verdict = "ADOPTED from F2" if calibration.adopted else "NOT ADOPTED; the assumption is kept"
    print(f"  rule {ESTIMATOR_RULE}: {verdict}")
    print(f"  written: {written.as_posix()}")
    return EXIT_OK


def _command_f1_depth() -> int:
    """Acquire the depth sample, and refuse to acquire it when the rule did not ask.

    Rule C3 decides this from the executed result file, not from whoever is running
    the command. A stage that downloaded 160 MB because it was invoked would make the
    registered condition decorative.
    """
    import json as _json

    from sextant.app import spike_006_f1_depth
    from sextant.app.spike_006_f1 import RESULTS_PATH
    from sextant.app.spike_006_f1_analysis import analyse, capacity_report, depth_sample_is_needed
    from sextant.app.spike_006_f1_world import build_world

    if not RESULTS_PATH.is_file():
        print(f"  no result file at {RESULTS_PATH.as_posix()}; rule C3 has nothing to read.")
        return EXIT_ORDERING_UNVERIFIED
    payload = _json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    rows = analyse(payload)
    capacity = capacity_report(payload, rows)
    if not depth_sample_is_needed(capacity):
        print("  rule C3 reports capacity UNESTABLISHED for every variant.")
        print("  the depth sample is not acquired, and that is the rule's answer.")
        return EXIT_OK
    measured = sorted({item.variant for item in capacity if item.verdict.needs_depth_data})
    print(f"  rule C3 lands on a measured outcome for: {', '.join(measured)}")
    sample = spike_006_f1_depth.acquire(build_world(), raw_root=spike_006_f1_depth.DEPTH_ROOT)
    written = spike_006_f1_depth.write(sample, spike_006_f1_depth.DEPTH_RESULTS)
    print(f"  {sample.megabytes} MB over {len(sample.fetched)} symbol-days")
    print(f"  written: {written.as_posix()}")
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
    if stage == "depth":
        return _command_f1_depth()
    if stage == "spread":
        return _command_f1_spread()
    if stage == "estimator":
        return _command_f1_estimator()
    if stage == "contraction":
        from sextant.app import spike_006_f1_contraction
        from sextant.app.spike_006_f1_world import build_world

        found = spike_006_f1_contraction.examine(build_world())
        if found is None:
            print("  no rebalance loses pairs; amendment 26.1 has nothing to describe.")
            return EXIT_OK
        written = spike_006_f1_contraction.write(
            found, spike_006_f1_contraction.CONTRACTION_RESULTS
        )
        print(f"  {found.verdict()}")
        print(f"  written: {written.as_posix()}")
        return EXIT_OK
    if stage == "report":
        from sextant.app import spike_006_f1_report

        spike_006_f1_report.render()
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

"""Command-line entrypoints.

``sextant status`` resolves configuration and reports what this run would be
allowed to do, without refusing to print when preflight fails.
``sextant run`` performs the full startup sequence and stops, because there is
no engine yet.

This software does not place real orders in its current state.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sextant.app.config import load_settings, resolve_profile
from sextant.app.credentials import credentials_for, load_dotenv
from sextant.app.preflight import PreflightFailed, PreflightReport, run_preflight
from sextant.app.startup import describe_mode, prepare
from sextant.app.wiring import build_exchange_client
from sextant.domain.errors import SextantError

EXIT_OK = 0
EXIT_STARTUP_REFUSED = 2

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


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint. Returns a process exit code rather than calling sys.exit."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return _command_status(args.profile, args.config_dir)
        if args.command == "spike":
            return _command_spike(args.stage)
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

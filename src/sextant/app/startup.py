"""The startup sequence.

One ordered, testable path from "a process started" to "this run is allowed to
proceed". Kept out of the CLI so it can be exercised without argument parsing.

Order matters:

1. load ``.env`` if one exists (absent is normal, not an error);
2. resolve settings from base -> profile -> environment;
3. gate LIVE on the separate environment switch, before anything else happens;
4. read credentials and register them with the log redactor *before* the first
   log line, so nothing can leak in the interval;
5. wire the adapter for the venue this run names;
6. run preflight and refuse to continue if it fails.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from sextant.adapters.clocks import SystemClock
from sextant.app.config import load_settings, require_live_authorisation, resolve_profile
from sextant.app.credentials import Credentials, credentials_for, load_dotenv
from sextant.app.logging_setup import RunContext, configure_logging
from sextant.app.preflight import PreflightFailed, PreflightReport, run_preflight
from sextant.app.settings import LogFormat, Settings
from sextant.app.wiring import RunMetadata, build_clock, build_exchange_client, new_run_id
from sextant.domain.mode import RunMode
from sextant.ports.clock import Clock
from sextant.ports.exchange import ExchangeClient


@dataclass(frozen=True, slots=True)
class StartupResult:
    """Everything a run needs, once it has been allowed to start."""

    settings: Settings
    metadata: RunMetadata
    exchange: ExchangeClient
    clock: Clock
    rng: random.Random
    preflight: PreflightReport
    credentials: Credentials


def prepare(
    *,
    profile: str | None = None,
    config_dir: Path | None = None,
    dotenv_path: Path | None = None,
) -> StartupResult:
    """Resolve, authorise, wire and validate a run. Raises rather than degrading."""
    load_dotenv(dotenv_path)

    chosen_profile = resolve_profile(profile)
    settings = load_settings(profile=chosen_profile, config_dir=config_dir)
    mode = settings.run.mode

    # Before any wiring, any logging and any network-capable object exists.
    require_live_authorisation(mode)

    credentials = credentials_for(settings.run.venue)
    run_id = new_run_id()
    configure_logging(
        context=RunContext(run_id=run_id, mode=mode.value, venue=settings.run.venue),
        level=settings.logging.level,
        as_json=settings.logging.format is LogFormat.JSON,
        secret_values=credentials.secret_values,
    )

    exchange = build_exchange_client(settings)

    # SystemClock is an adapter, so reading wall time here is allowed. A backtest
    # driver replaces this instant with the start of its data window; until a
    # window exists there is nothing more meaningful to start the simulation at.
    started_at = SystemClock().now()
    clock = build_clock(mode, backtest_start=started_at)

    report = run_preflight(
        settings=settings,
        capabilities=exchange.capabilities(),
        credentials=credentials,
        withdrawal_permission=exchange.withdrawal_permission(),
    )
    if not report.ok:
        raise PreflightFailed(report)

    metadata = RunMetadata(
        run_id=run_id,
        mode=mode,
        venue=settings.run.venue,
        profile=chosen_profile,
        seed=settings.run.seed,
        started_at=started_at,
    )
    return StartupResult(
        settings=settings,
        metadata=metadata,
        exchange=exchange,
        clock=clock,
        rng=random.Random(settings.run.seed),  # noqa: S311 - simulation, not cryptography
        preflight=report,
        credentials=credentials,
    )


def describe_mode(mode: RunMode) -> str:
    """One line stating what this mode can and cannot do."""
    if mode is RunMode.LIVE:
        return "LIVE: gated. Places real orders once an engine exists."
    if mode is RunMode.PAPER:
        return "PAPER: simulated portfolio. No real order is ever sent."
    return "BACKTEST: historical data only. No credentials, no network, no orders."

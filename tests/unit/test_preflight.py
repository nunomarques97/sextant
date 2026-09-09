"""Preflight, mode by mode.

The table being tested: BACKTEST needs nothing, PAPER needs credentials only if
the run actually uses a credentialed capability, LIVE needs everything. A
preflight that demanded keys for a backtest would be worked around within a
week, so the skipping is as much the subject of these tests as the failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sextant.app.config import load_settings
from sextant.app.credentials import Credentials, credentials_for, key_variable, secret_variable
from sextant.app.preflight import (
    CheckStatus,
    PreflightReport,
    credentials_required,
    run_preflight,
)
from sextant.app.settings import Settings
from sextant.app.wiring import build_exchange_client
from sextant.domain.capability import Capability, CapabilitySet
from sextant.domain.mode import RunMode
from sextant.domain.venue import Venue
from sextant.ports.exchange import WithdrawalPermission
from tests.conftest import MINIMAL_BASE_CONFIG, write_config

NO_CREDENTIALS = Credentials(venue="kraken", api_key=None, api_secret=None)
FULL_CREDENTIALS = Credentials(
    venue="kraken", api_key="key-long-enough", api_secret="secret-long-enough"
)


def preflight(
    settings: Settings,
    *,
    credentials: Credentials = NO_CREDENTIALS,
    withdrawal: WithdrawalPermission = WithdrawalPermission.UNKNOWN,
) -> PreflightReport:
    client = build_exchange_client(settings)
    return run_preflight(
        settings=settings,
        capabilities=client.capabilities(),
        credentials=credentials,
        withdrawal_permission=withdrawal,
    )


def status_of(report: PreflightReport, name: str) -> CheckStatus:
    return next(check.status for check in report.checks if check.name == name)


def reason_of(report: PreflightReport, name: str) -> str:
    return next(check.reason for check in report.checks if check.name == name)


# --------------------------------------------------------------------------
# BACKTEST
# --------------------------------------------------------------------------


def test_backtest_passes_with_no_credentials_present(config_dir: Path) -> None:
    settings = load_settings(profile="backtest", config_dir=config_dir)
    report = preflight(settings)

    assert report.ok
    assert status_of(report, "credentials") is CheckStatus.SKIPPED
    assert status_of(report, "withdrawal_permission") is CheckStatus.SKIPPED


def test_backtest_states_why_it_skipped_each_check(config_dir: Path) -> None:
    settings = load_settings(profile="backtest", config_dir=config_dir)
    report = preflight(settings)

    assert {check.name for check in report.skipped} == {"credentials", "withdrawal_permission"}
    for check in report.skipped:
        assert check.reason.strip()


# --------------------------------------------------------------------------
# PAPER
# --------------------------------------------------------------------------


def test_paper_on_public_data_alone_needs_no_credentials(config_dir: Path) -> None:
    settings = load_settings(profile="paper", config_dir=config_dir)
    report = preflight(settings)

    assert report.ok
    assert status_of(report, "credentials") is CheckStatus.SKIPPED


def test_paper_that_asks_for_account_data_does_need_credentials(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SEXTANT_RUN__REQUIRED_CAPABILITIES", '["PUBLIC_MARKET_DATA","ACCOUNT_DATA"]'
    )
    settings = load_settings(profile="paper", config_dir=config_dir)

    report = preflight(settings)

    assert not report.ok
    assert status_of(report, "credentials") is CheckStatus.FAILED
    assert key_variable("kraken") in reason_of(report, "credentials")


# --------------------------------------------------------------------------
# LIVE
# --------------------------------------------------------------------------


def test_live_fails_with_no_credentials_present(config_dir: Path) -> None:
    settings = load_settings(profile="live", config_dir=config_dir)
    report = preflight(settings)

    assert not report.ok
    assert status_of(report, "credentials") is CheckStatus.FAILED


def test_live_still_fails_when_withdrawal_permission_cannot_be_determined(
    config_dir: Path,
) -> None:
    settings = load_settings(profile="live", config_dir=config_dir)
    report = preflight(
        settings,
        credentials=FULL_CREDENTIALS,
        withdrawal=WithdrawalPermission.UNKNOWN,
    )

    assert not report.ok
    assert status_of(report, "withdrawal_permission") is CheckStatus.FAILED
    assert "could not be determined" in reason_of(report, "withdrawal_permission")


def test_live_fails_when_the_key_can_withdraw(config_dir: Path) -> None:
    settings = load_settings(profile="live", config_dir=config_dir)
    report = preflight(
        settings,
        credentials=FULL_CREDENTIALS,
        withdrawal=WithdrawalPermission.PRESENT,
    )

    assert status_of(report, "withdrawal_permission") is CheckStatus.FAILED
    assert "revoke" in reason_of(report, "withdrawal_permission")


def test_live_passes_only_with_complete_credentials_that_cannot_withdraw(
    config_dir: Path,
) -> None:
    settings = load_settings(profile="live", config_dir=config_dir)
    report = preflight(
        settings,
        credentials=FULL_CREDENTIALS,
        withdrawal=WithdrawalPermission.ABSENT,
    )

    assert report.ok


# --------------------------------------------------------------------------
# Capability failures name their layer
# --------------------------------------------------------------------------


def test_a_capability_denied_by_jurisdiction_fails_preflight_and_names_the_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The venue supports it and the account permits it; only the jurisdiction says no."""
    restricted = dict(MINIMAL_BASE_CONFIG)
    restricted["jurisdictions"] = {
        "PT": {"allowed_capabilities": ["PUBLIC_MARKET_DATA", "HISTORICAL_OHLCV"]}
    }
    directory = write_config(tmp_path, base=restricted)
    monkeypatch.setenv("SEXTANT_RUN__REQUIRED_CAPABILITIES", '["SPOT_TRADING"]')
    settings = load_settings(profile="backtest", config_dir=directory)

    report = preflight(settings, credentials=FULL_CREDENTIALS)

    assert not report.ok
    assert status_of(report, "required_capabilities") is CheckStatus.FAILED
    assert "jurisdiction" in reason_of(report, "required_capabilities")


def test_a_capability_denied_by_the_account_names_the_account_layer(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SEXTANT_RUN__REQUIRED_CAPABILITIES", '["PUBLIC_MARKET_DATA","FUTURES_TRADING"]'
    )
    settings = load_settings(profile="backtest", config_dir=config_dir)

    report = preflight(settings)

    assert status_of(report, "required_capabilities") is CheckStatus.FAILED
    assert "account" in reason_of(report, "required_capabilities")


def test_a_run_naming_a_venue_with_no_configuration_fails(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEXTANT_RUN__VENUE", "coinbase")
    settings = load_settings(profile="backtest", config_dir=config_dir)

    report = run_preflight(
        settings=settings,
        capabilities=CapabilitySet.build(Venue("coinbase"), Capability, Capability, Capability),
        credentials=NO_CREDENTIALS,
        withdrawal_permission=WithdrawalPermission.ABSENT,
    )

    assert not report.ok
    assert status_of(report, "venue_configuration") is CheckStatus.FAILED
    assert "coinbase" in reason_of(report, "venue_configuration")


def test_a_disabled_venue_cannot_be_used_by_a_run(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEXTANT_EXCHANGES__KRAKEN__ENABLED", "false")
    settings = load_settings(profile="backtest", config_dir=config_dir)

    report = preflight(settings)

    assert not report.ok
    assert "disabled" in reason_of(report, "venue_configuration")


# --------------------------------------------------------------------------
# The credential decision itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "required", "expected"),
    [
        (RunMode.BACKTEST, (Capability.PUBLIC_MARKET_DATA,), False),
        (RunMode.BACKTEST, (Capability.ACCOUNT_DATA,), True),
        (RunMode.PAPER, (Capability.PUBLIC_MARKET_DATA, Capability.ORDER_BOOK), False),
        (RunMode.PAPER, (Capability.SPOT_TRADING,), True),
        (RunMode.LIVE, (Capability.PUBLIC_MARKET_DATA,), True),
    ],
)
def test_whether_a_run_needs_credentials_at_all(
    mode: RunMode, required: tuple[Capability, ...], expected: bool
) -> None:
    assert credentials_required(mode, required) is expected


def test_credentials_are_read_from_the_environment_by_a_generic_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(key_variable("kraken"), "abc123456")
    monkeypatch.setenv(secret_variable("kraken"), "def123456")

    credentials = credentials_for("kraken")

    assert credentials.is_complete
    assert credentials_for("binance").is_complete is False


def test_credentials_never_render_their_values() -> None:
    rendered = f"{FULL_CREDENTIALS!r} {FULL_CREDENTIALS}"
    assert "secret-long-enough" not in rendered
    assert "complete" in rendered

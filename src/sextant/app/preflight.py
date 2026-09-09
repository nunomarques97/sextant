"""Preflight.

Preflight validates only what the resolved mode and configuration actually
require, and states which checks it skipped and why. Imposing LIVE's
requirements on a backtest would be safety theatre: it would train everyone to
work around the checks, which is worse than not having them.

    mode      credentials                              withdrawal probe
    BACKTEST  never required                           not applicable
    PAPER     only if a credentialed capability is     only when credentials
              needed by the run                        are actually in use
    LIVE      mandatory                                mandatory

A withdrawal permission that is PRESENT, or that cannot be determined, aborts
startup. An API key whose permissions cannot be read has not been shown to be
harmless.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sextant.app.credentials import Credentials, key_variable, secret_variable
from sextant.app.settings import Settings
from sextant.domain.capability import (
    CREDENTIALED_CAPABILITIES,
    Capability,
    CapabilityNotAvailable,
    CapabilitySet,
)
from sextant.domain.errors import SextantError
from sextant.domain.mode import RunMode
from sextant.ports.exchange import WithdrawalPermission


class CheckStatus(StrEnum):
    """Outcome of a single preflight check."""

    PASSED = "passed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Check:
    """One preflight check and why it came out the way it did."""

    name: str
    status: CheckStatus
    reason: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Everything preflight concluded about this run."""

    mode: RunMode
    venue: str
    checks: tuple[Check, ...]

    @property
    def failures(self) -> tuple[Check, ...]:
        """The checks that failed."""
        return tuple(check for check in self.checks if check.status is CheckStatus.FAILED)

    @property
    def skipped(self) -> tuple[Check, ...]:
        """The checks that did not apply to this mode or configuration."""
        return tuple(check for check in self.checks if check.status is CheckStatus.SKIPPED)

    @property
    def ok(self) -> bool:
        """Whether the run may proceed."""
        return not self.failures


class PreflightFailed(SextantError):
    """Preflight refused to let the run start."""

    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        detail = "; ".join(f"{check.name}: {check.reason}" for check in report.failures)
        super().__init__(
            f"Preflight failed for mode={report.mode.value} venue={report.venue}. {detail}"
        )


def credentials_required(mode: RunMode, required: Sequence[Capability]) -> bool:
    """Whether this run needs API credentials at all.

    LIVE always does. Any other mode does only when it asks for a capability
    that cannot be exercised anonymously. Paper trading against public market
    data with a simulated portfolio therefore needs no keys.
    """
    if mode is RunMode.LIVE:
        return True
    return bool(CREDENTIALED_CAPABILITIES.intersection(required))


def _check_venue_configuration(settings: Settings) -> Check:
    name = "venue_configuration"
    venue = settings.run.venue
    exchange = settings.exchanges.get(venue)
    if exchange is None:
        return Check(name, CheckStatus.FAILED, f"run.venue {venue!r} has no exchanges entry")
    if not exchange.enabled:
        return Check(name, CheckStatus.FAILED, f"run.venue {venue!r} is configured as disabled")
    if exchange.jurisdiction not in settings.jurisdictions:
        return Check(
            name,
            CheckStatus.FAILED,
            f"jurisdiction {exchange.jurisdiction!r} for {venue!r} is not defined",
        )
    return Check(
        name, CheckStatus.PASSED, f"{venue} is enabled in jurisdiction {exchange.jurisdiction}"
    )


def _check_capabilities(settings: Settings, capabilities: CapabilitySet) -> Check:
    name = "required_capabilities"
    required = settings.run.required_capabilities
    if not required:
        return Check(name, CheckStatus.SKIPPED, "the run declares no required capabilities")
    for capability in required:
        try:
            capabilities.require(capability)
        except CapabilityNotAvailable as exc:
            return Check(
                name,
                CheckStatus.FAILED,
                f"{exc.capability.value} denied by the {exc.layer.value} layer",
            )
    return Check(
        name,
        CheckStatus.PASSED,
        f"all {len(required)} required capabilities are in the effective set",
    )


def _check_credentials(mode: RunMode, settings: Settings, credentials: Credentials) -> Check:
    name = "credentials"
    venue = settings.run.venue
    if not credentials_required(mode, settings.run.required_capabilities):
        return Check(
            name,
            CheckStatus.SKIPPED,
            f"mode {mode.value} with only public capabilities needs no credentials",
        )
    if not credentials.is_complete:
        return Check(
            name,
            CheckStatus.FAILED,
            f"set {key_variable(venue)} and {secret_variable(venue)} in the environment",
        )
    return Check(name, CheckStatus.PASSED, f"credentials present for {venue}")


def _check_withdrawal_permission(
    mode: RunMode,
    settings: Settings,
    credentials: Credentials,
    permission: WithdrawalPermission,
) -> Check:
    name = "withdrawal_permission"
    if not credentials_required(mode, settings.run.required_capabilities):
        return Check(name, CheckStatus.SKIPPED, "no credentials are in use for this run")
    if not credentials.is_complete:
        return Check(name, CheckStatus.SKIPPED, "credentials are missing; nothing to probe")
    if permission is WithdrawalPermission.PRESENT:
        return Check(
            name,
            CheckStatus.FAILED,
            "the API key carries withdrawal permission; revoke it before running",
        )
    if permission is WithdrawalPermission.UNKNOWN:
        return Check(
            name,
            CheckStatus.FAILED,
            "withdrawal permission could not be determined; the venue probe is not wired yet",
        )
    return Check(name, CheckStatus.PASSED, "the API key cannot withdraw")


def run_preflight(
    settings: Settings,
    capabilities: CapabilitySet,
    credentials: Credentials,
    withdrawal_permission: WithdrawalPermission,
) -> PreflightReport:
    """Run every check that applies, and report what was skipped and why."""
    mode = settings.run.mode
    checks = (
        _check_venue_configuration(settings),
        _check_capabilities(settings, capabilities),
        _check_credentials(mode, settings, credentials),
        _check_withdrawal_permission(mode, settings, credentials, withdrawal_permission),
    )
    return PreflightReport(mode=mode, venue=settings.run.venue, checks=checks)

"""Dependency wiring.

The only module in the system where a concrete adapter meets a port. Everything
it builds is returned as its port type, so a caller cannot accidentally depend
on which implementation it got.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sextant.adapters.clocks import SimulatedClock, SystemClock
from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.registry import adapter_for
from sextant.app.settings import Settings
from sextant.domain.mode import RunMode
from sextant.domain.time import Timestamp
from sextant.ports.clock import Clock
from sextant.ports.exchange import ExchangeClient


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """Everything needed to identify and reproduce a run.

    ``seed`` is recorded here rather than left implicit: a run whose seed is not
    written down is a run that cannot be reproduced, and a backtest that cannot
    be reproduced is an anecdote.
    """

    run_id: str
    mode: RunMode
    venue: str
    profile: str
    seed: int
    started_at: Timestamp


def new_run_id() -> str:
    """A fresh identifier for one run. Carried on every log record."""
    return uuid.uuid4().hex[:12]


def build_exchange_client(settings: Settings) -> ExchangeClient:
    """Construct the adapter for the venue this run names.

    The account and jurisdiction layers come from configuration. Nothing here
    inspects the venue's name to decide behaviour: the name is a dictionary key.
    """
    venue_name = settings.run.venue
    exchange_settings = settings.exchange(venue_name)
    jurisdiction = settings.jurisdiction_for(venue_name)
    adapter: type[BaseExchangeClient] = adapter_for(venue_name)
    return adapter(
        account_permitted=frozenset(exchange_settings.account_capabilities),
        jurisdiction_eligible=frozenset(jurisdiction.allowed_capabilities),
    )


def build_clock(mode: RunMode, *, backtest_start: Timestamp | None = None) -> Clock:
    """A wall clock for PAPER and LIVE, a caller-advanced clock for BACKTEST."""
    if mode is RunMode.BACKTEST:
        if backtest_start is None:
            raise ValueError("BACKTEST requires an explicit start instant for its clock")
        return SimulatedClock(current=backtest_start)
    return SystemClock()

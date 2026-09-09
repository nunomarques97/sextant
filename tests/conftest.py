"""Shared test fixtures and builders.

Two things matter here. First, no test may see the developer's real
environment: a suite that passes because ``SEXTANT_ALLOW_LIVE`` happens to be
set on one machine is worse than no suite. Second, config fixtures are written
to a temporary directory rather than pointed at ``config/``, so a test cannot
be made to pass by editing production configuration.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from sextant.domain.instrument import Instrument
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

MINIMAL_BASE_CONFIG: dict[str, object] = {
    "run": {
        "timeframe": "1h",
        "seed": 7,
        "required_capabilities": ["PUBLIC_MARKET_DATA", "HISTORICAL_OHLCV"],
    },
    "exchanges": {
        "kraken": {
            "enabled": True,
            "jurisdiction": "PT",
            "account_capabilities": [
                "PUBLIC_MARKET_DATA",
                "HISTORICAL_OHLCV",
                "ACCOUNT_DATA",
                "SPOT_TRADING",
            ],
        },
        "binance": {
            "enabled": True,
            "jurisdiction": "PT",
            "account_capabilities": ["PUBLIC_MARKET_DATA", "HISTORICAL_OHLCV"],
        },
    },
    "jurisdictions": {
        "PT": {
            "allowed_capabilities": [
                "PUBLIC_MARKET_DATA",
                "HISTORICAL_OHLCV",
                "ORDER_BOOK",
                "ACCOUNT_DATA",
                "SPOT_TRADING",
            ]
        }
    },
    "universe": {
        "quote_currencies": ["EUR", "USDT"],
        "min_listing_age_days": 180,
        "min_median_quote_volume": "250000",
        "median_volume_window_days": 30,
        "max_spread_bps": "25",
        "account_equity_quote": "1750",
        "max_positions": 12,
    },
    "logging": {"level": "INFO", "format": "json"},
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every sextant environment variable for the duration of a test."""
    for name in list(os.environ):
        if name.startswith("SEXTANT_"):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A temporary config directory holding a valid base and all three profiles."""
    return write_config(tmp_path)


def write_config(
    directory: Path,
    *,
    base: Mapping[str, object] | None = None,
    profiles: Mapping[str, Mapping[str, object]] | None = None,
) -> Path:
    """Write a config directory and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "base.yaml").write_text(
        yaml.safe_dump(dict(base if base is not None else MINIMAL_BASE_CONFIG)),
        encoding="utf-8",
    )
    defaults: dict[str, Mapping[str, object]] = {
        "backtest": {"run": {"mode": "backtest", "venue": "kraken"}},
        "paper": {"run": {"mode": "paper", "venue": "kraken"}},
        "live": {
            "run": {
                "mode": "live",
                "venue": "kraken",
                "required_capabilities": [
                    "PUBLIC_MARKET_DATA",
                    "HISTORICAL_OHLCV",
                    "ACCOUNT_DATA",
                    "SPOT_TRADING",
                ],
            }
        },
    }
    for name, content in (profiles if profiles is not None else defaults).items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(dict(content)), encoding="utf-8")
    return directory


def ts(text: str) -> Timestamp:
    """Shorthand for an ISO-8601 UTC timestamp."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def make_instrument(
    symbol: str,
    *,
    venue: str = "kraken",
    listed_at: str = "2020-01-01T00:00:00",
    delisted_at: str | None = None,
    base: str = "BTC",
    quote: str = "EUR",
) -> Instrument:
    """Build an instrument with sane defaults for tests that do not care about them."""
    return Instrument(
        venue=Venue(venue),
        symbol=symbol,
        base=base,
        quote=quote,
        listed_at=ts(listed_at),
        delisted_at=ts(delisted_at) if delisted_at is not None else None,
        tick_size=Price(Decimal("0.01")),
        lot_size=Quantity(Decimal("0.00001")),
        min_notional=Notional(Decimal("10")),
    )

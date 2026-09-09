"""Typed settings.

Layering, lowest priority first:

    config/base.yaml  ->  config/<profile>.yaml  ->  environment variables

Unknown keys are rejected rather than ignored, at both the top level and inside
every section. A typo in a risk threshold that silently keeps the default is the
kind of defect that only shows up in a live PnL statement.

Two environment variables deliberately live outside this model and cannot be
set from any YAML file:

* ``SEXTANT_PROFILE`` selects which profile file is layered on;
* ``SEXTANT_ALLOW_LIVE`` is the live-trading master switch.

Making the live switch a settings field would put it one YAML edit away from
being on.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from sextant.domain.capability import Capability
from sextant.domain.mode import RunMode
from sextant.domain.time import Timeframe

ENV_PREFIX = "SEXTANT_"
PROFILE_ENV_VAR = f"{ENV_PREFIX}PROFILE"
ALLOW_LIVE_ENV_VAR = f"{ENV_PREFIX}ALLOW_LIVE"
ALLOW_LIVE_EXPECTED_VALUE = "1"


class LogFormat(StrEnum):
    """How log records are rendered."""

    JSON = "json"
    TEXT = "text"


class RunSettings(BaseModel):
    """What this particular run is.

    ``venue`` is required and carries no default. A run names the venue it uses,
    explicitly, once. There is no primary venue and no fallback venue: cross-venue
    selection, if it is ever needed, arrives as an explicit router with stated
    rules, not as a default that quietly decides for everyone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RunMode = RunMode.BACKTEST
    venue: str = Field(min_length=1, description="Venue slug this run executes against.")
    timeframe: Timeframe = Timeframe.H1
    seed: int = Field(default=0, description="Recorded in run metadata; seeds all randomness.")
    required_capabilities: tuple[Capability, ...] = Field(
        default=(Capability.PUBLIC_MARKET_DATA, Capability.HISTORICAL_OHLCV),
        description="What this run needs the venue to permit. Drives preflight.",
    )


class ExchangeSettings(BaseModel):
    """One venue, configured as an independent peer of every other."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    jurisdiction: str = Field(min_length=2, description="Account holder's jurisdiction code.")
    account_capabilities: tuple[Capability, ...] = Field(
        description="The account layer: what this account's tier and key permissions allow."
    )


class JurisdictionSettings(BaseModel):
    """The jurisdictional eligibility layer, as data.

    Keyed by jurisdiction code rather than by venue, so a rule change is a
    configuration edit and never a code change.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_capabilities: tuple[Capability, ...]


class UniverseSettings(BaseModel):
    """Point-in-time universe admission thresholds.

    These are computable from information available at the decision date. No
    threshold here refers to a return, a ranking of today's largest assets, or
    anything else that needs hindsight.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    quote_currencies: tuple[str, ...]
    min_listing_age_days: int = Field(ge=0)
    min_median_quote_volume: Decimal
    median_volume_window_days: int = Field(ge=1)
    max_spread_bps: Decimal
    account_equity_quote: Decimal = Field(
        description="Account size the min_notional and lot_size feasibility test runs against."
    )
    max_positions: int = Field(ge=1)
    excluded_asset_classes: tuple[str, ...] = ()


class LoggingSettings(BaseModel):
    """Structured logging configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str = "INFO"
    format: LogFormat = LogFormat.JSON


class Settings(BaseSettings):
    """The fully resolved configuration for one run.

    ``extra="ignore"`` applies to the environment only: the process environment
    legitimately carries sibling variables (the profile selector, the live
    switch, API keys) that are not settings. Unknown keys coming from a YAML
    file are rejected explicitly by the loader, and every nested section forbids
    extras outright.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    run: RunSettings
    exchanges: Mapping[str, ExchangeSettings]
    jurisdictions: Mapping[str, JurisdictionSettings]
    universe: UniverseSettings
    logging: LoggingSettings = LoggingSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Put the YAML layers (passed as init values) *below* the environment.

        Pydantic's default puts init arguments first. Here the init argument is
        the merged YAML, which must be the weakest source, so it goes last.
        """
        return (env_settings, dotenv_settings, file_secret_settings, init_settings)

    def exchange(self, venue_name: str) -> ExchangeSettings:
        """The configuration block for one venue."""
        return self.exchanges[venue_name]

    def jurisdiction_for(self, venue_name: str) -> JurisdictionSettings:
        """The jurisdictional eligibility applying to a venue's configured jurisdiction."""
        return self.jurisdictions[self.exchanges[venue_name].jurisdiction]

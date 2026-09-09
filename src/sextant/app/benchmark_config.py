"""Reading the pre-registered benchmark configuration, and refusing drift.

``config/benchmarks.yaml`` was committed before any baseline ran, in its own
commit, so that the git history is the evidence that nothing was adjusted after
somebody saw a result. This module reads it and - the part that matters -
checks it against the cost assumptions the venue adapter holds in code.

The duplication between the two is deliberate. Pre-registration that can
silently disagree with the code it pre-registers is not pre-registration, so
:func:`assert_costs_match_the_registration` raises rather than warning, and the
runner will not produce a number under a configuration that has drifted.

No polars, no numpy, no venue name: the venue's own values arrive as an argument.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from sextant.domain.errors import SextantError
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.execution.costs import (
    CostAssumption,
    FeeSchedule,
    FillMix,
    LiquidityBand,
)
from sextant.engine.execution.fx import FxPolicy

BENCHMARKS_PATH = Path("config") / "benchmarks.yaml"


class RegistrationDrift(SextantError):
    """The code and the pre-registered configuration disagree.

    Raised, never warned about. A benchmark run under a configuration that has
    drifted from its registration is a result nobody can date.
    """


@dataclass(frozen=True, slots=True)
class PlanSpec:
    """One walk-forward shape, as registered."""

    name: str
    in_sample_months: int
    fold_count: int
    out_of_sample_months_total: int


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """One of the three ways every headline result is reported."""

    name: str
    quote_policy: str
    fx_policy: FxPolicy
    is_counterfactual: bool


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """The whole pre-registered configuration, parsed."""

    version: str
    registered_on: str
    first_usable_month: Timestamp
    usable_months: int
    plans: Mapping[str, PlanSpec]
    account_currency: str
    account_equity: Notional
    max_positions: int
    min_notional_fraction: Decimal
    lot_rounding_fraction: Decimal
    quote_policies: Mapping[str, frozenset[str]]
    variants: Sequence[VariantSpec]
    fx_pair_symbol: str
    fx_conversion_bps: Decimal
    maker_bps: Decimal
    taker_bps: Decimal
    fill_mix_maker_fractions: tuple[Decimal, ...]
    deep_floor: Notional
    mid_floor: Notional
    spread_bps: Mapping[LiquidityBand, Decimal]
    slippage_bps: Mapping[LiquidityBand, Decimal]
    funding_bps_per_day: Decimal
    haircut_fraction: Decimal
    null_positions: int
    seed_start: int
    seed_count: int
    percentiles: tuple[float, ...]
    bootstrap_resamples: int
    bootstrap_confidence: float
    bootstrap_generator_seed: int
    single_asset_symbol: str

    @property
    def seeds(self) -> range:
        """The exact seed set, as a range so it is stated rather than described."""
        return range(self.seed_start, self.seed_start + self.seed_count)

    @property
    def fill_mixes(self) -> tuple[FillMix, ...]:
        """The registered mixes, in the registered order."""
        return tuple(
            FillMix(maker_fraction=fraction, label=_mix_label(fraction))
            for fraction in self.fill_mix_maker_fractions
        )

    def as_metadata(self) -> Mapping[str, str]:
        """The registration, for the run manifest."""
        return {
            "benchmark_config_version": self.version,
            "benchmark_config_registered_on": self.registered_on,
            "benchmark_seed_start": str(self.seed_start),
            "benchmark_seed_count": str(self.seed_count),
        }


def _mix_label(fraction: Decimal) -> str:
    """How a fill mix is named in every report line."""
    if fraction == Decimal(1):
        return "100% maker (assumed)"
    if fraction == Decimal(0):
        return "100% taker (assumed)"
    percent = fraction * Decimal(100)
    return f"{percent:g}/{Decimal(100) - percent:g} maker/taker (assumed)"


def load(path: Path = BENCHMARKS_PATH) -> BenchmarkConfig:
    """Parse the pre-registered configuration."""
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise RegistrationDrift(f"{path} is not a mapping")

    window = _mapping(raw, "window")
    account = _mapping(raw, "account")
    costs = _mapping(raw, "costs")
    fees = _mapping(costs, "fees")
    bands = _mapping(costs, "liquidity_bands")
    experiment = _mapping(raw, "null_experiment")
    bootstrap = _mapping(experiment, "bootstrap")
    benchmarks = _mapping(raw, "benchmarks")

    return BenchmarkConfig(
        version=str(raw["version"]),
        registered_on=str(raw["registered_on"]),
        first_usable_month=_month(window["first_usable_month"]),
        usable_months=int(str(window["usable_months"])),
        plans={
            name: PlanSpec(
                name=name,
                in_sample_months=_int(_mapping(_mapping(raw, "plans"), name), "in_sample_months"),
                fold_count=_int(_mapping(_mapping(raw, "plans"), name), "fold_count"),
                out_of_sample_months_total=_int(
                    _mapping(_mapping(raw, "plans"), name), "out_of_sample_months_total"
                ),
            )
            for name in sorted(_mapping(raw, "plans"))
        },
        account_currency=str(account["currency"]),
        account_equity=Notional(Decimal(str(account["equity"]))),
        max_positions=_int(account, "max_positions"),
        min_notional_fraction=Decimal(str(account["min_notional_fraction"])),
        lot_rounding_fraction=Decimal(str(account["lot_rounding_fraction"])),
        quote_policies={
            name: frozenset(str(item) for item in _list(_mapping(raw, "quote_policies"), name))
            for name in _mapping(raw, "quote_policies")
        },
        variants=tuple(
            VariantSpec(
                name=str(item["name"]),
                quote_policy=str(item["quote_policy"]),
                fx_policy=FxPolicy(str(item["fx_policy"])),
                is_counterfactual=bool(item["is_counterfactual"]),
            )
            for item in _sequence(raw, "reporting_variants")
        ),
        fx_pair_symbol=str(_mapping(raw, "fx")["pair_symbol"]),
        fx_conversion_bps=Decimal(str(_mapping(raw, "fx")["conversion_bps"])),
        maker_bps=Decimal(str(fees["maker_bps"])),
        taker_bps=Decimal(str(fees["taker_bps"])),
        fill_mix_maker_fractions=tuple(
            Decimal(str(value)) for value in _list(_mapping(costs, "fill_mixes"), "maker_fractions")
        ),
        deep_floor=Notional(Decimal(str(bands["deep_floor"]))),
        mid_floor=Notional(Decimal(str(bands["mid_floor"]))),
        spread_bps=_bands(_mapping(costs, "spread_bps")),
        slippage_bps=_bands(_mapping(costs, "slippage_bps")),
        funding_bps_per_day=Decimal(str(costs["funding_bps_per_day"])),
        haircut_fraction=Decimal(str(costs["delisting_haircut_fraction"])),
        null_positions=_int(experiment, "positions"),
        seed_start=_int(experiment, "seed_start"),
        seed_count=_int(experiment, "seed_count"),
        percentiles=tuple(float(str(value)) for value in _list(experiment, "percentiles")),
        bootstrap_resamples=_int(bootstrap, "resamples"),
        bootstrap_confidence=float(str(bootstrap["confidence"])),
        bootstrap_generator_seed=_int(bootstrap, "generator_seed"),
        single_asset_symbol=str(_mapping(benchmarks, "single_asset")["symbol"]),
    )


def assert_costs_match_the_registration(
    config: BenchmarkConfig,
    *,
    schedule: FeeSchedule,
    spread: CostAssumption,
    slippage: CostAssumption,
    deep_floor: Notional,
    mid_floor: Notional,
    conversion_bps: Decimal,
) -> None:
    """Refuse to run if the venue adapter and the registration have drifted."""
    mismatches: list[str] = []
    if schedule.maker_bps != config.maker_bps:
        mismatches.append(f"maker fee: code {schedule.maker_bps}, registered {config.maker_bps}")
    if schedule.taker_bps != config.taker_bps:
        mismatches.append(f"taker fee: code {schedule.taker_bps}, registered {config.taker_bps}")
    if conversion_bps != config.fx_conversion_bps:
        mismatches.append(
            f"fx conversion: code {conversion_bps}, registered {config.fx_conversion_bps}"
        )
    if deep_floor.amount != config.deep_floor.amount:
        mismatches.append(
            f"deep band floor: code {deep_floor.amount}, registered {config.deep_floor.amount}"
        )
    if mid_floor.amount != config.mid_floor.amount:
        mismatches.append(
            f"mid band floor: code {mid_floor.amount}, registered {config.mid_floor.amount}"
        )
    for band in LiquidityBand:
        if spread.bps(band) != config.spread_bps[band]:
            mismatches.append(
                f"spread {band.value}: code {spread.bps(band)}, "
                f"registered {config.spread_bps[band]}"
            )
        if slippage.bps(band) != config.slippage_bps[band]:
            mismatches.append(
                f"slippage {band.value}: code {slippage.bps(band)}, "
                f"registered {config.slippage_bps[band]}"
            )
    if mismatches:
        raise RegistrationDrift(
            "The cost assumptions in the code no longer match "
            f"{BENCHMARKS_PATH} version {config.version}:\n  "
            + "\n  ".join(mismatches)
            + "\nA correction gets a new version identifier, a new commit and a full "
            "rerun. It never gets an edit in place."
        )


def _mapping(source: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = source.get(key)
    if not isinstance(value, dict):
        raise RegistrationDrift(f"{key!r} is missing or is not a mapping")
    return {str(name): item for name, item in value.items()}


def _sequence(source: Mapping[str, object], key: str) -> Sequence[Mapping[str, object]]:
    value = source.get(key)
    if not isinstance(value, list):
        raise RegistrationDrift(f"{key!r} is missing or is not a list")
    entries: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise RegistrationDrift(f"{key!r} holds a non-mapping entry")
        entries.append({str(name): field for name, field in item.items()})
    return entries


def _int(source: Mapping[str, object], key: str) -> int:
    value = source.get(key)
    if value is None:
        raise RegistrationDrift(f"{key!r} is missing")
    return int(str(value))


def _list(source: Mapping[str, object], key: str) -> Sequence[object]:
    value = source.get(key)
    if not isinstance(value, list):
        raise RegistrationDrift(f"{key!r} is missing or is not a list")
    return value


def _bands(source: Mapping[str, object]) -> Mapping[LiquidityBand, Decimal]:
    return {band: Decimal(str(source[band.value])) for band in LiquidityBand}


def _month(value: object) -> Timestamp:
    """A ``YYYY-MM-DD`` date from the configuration, as a UTC instant."""
    if isinstance(value, datetime):
        return Timestamp(value.replace(tzinfo=UTC))
    text = str(value)
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))

"""The point-in-time admission rules, as stated in docs/PHASE-0-FINDINGS.md §5.

Each rule returns one of three answers, not two:

* ``ADMIT`` - the instrument satisfies the rule at this instant;
* ``REJECT`` - it demonstrably does not;
* ``NOT_EVALUABLE`` - we do not have the data to decide.

The third is the one that earns its keep. A rule that cannot see a spread and
returns "fails" is indistinguishable in the totals from a rule that measured a
wide spread, and the report would then blame liquidity for what is actually a
missing dataset. ``admits()`` maps ``NOT_EVALUABLE`` to False, so an instrument
we cannot verify never enters a universe, while the evaluation keeps the two
apart for the reader.

Pure computation over already-fetched history. No I/O, no venue, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.engine.universe.statistics import InstrumentHistory

BASIS_POINTS = Decimal(10_000)


class RuleOutcome(StrEnum):
    """What a rule concluded about one instrument at one instant."""

    ADMIT = "admit"
    REJECT = "reject"
    NOT_EVALUABLE = "not_evaluable"


@runtime_checkable
class PointInTimeRule(Protocol):
    """A universe rule that can also say "I do not know"."""

    @property
    def name(self) -> str:
        """Short identifier, recorded in run metadata so a universe is reproducible."""
        ...

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Decide, using only information available at ``at``."""
        ...

    def admits(self, instrument: Instrument, at: Timestamp) -> bool:
        """Satisfies the domain ``UniverseRule`` protocol. Unknown means no."""
        ...


class _Rule:
    """Shared bridge from the three-valued answer to the domain's boolean one."""

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Implemented by each concrete rule."""
        raise NotImplementedError

    def admits(self, instrument: Instrument, at: Timestamp) -> bool:
        """Admit only on a positive verdict. Absence of evidence is not admission."""
        return self.evaluate(instrument, at) is RuleOutcome.ADMIT


@dataclass(frozen=True, slots=True)
class AccountParameters:
    """The account the *executable* universe is measured against.

    Per PO decision D2: the filter is evaluated against the most binding
    plausible constraint rather than the midpoint of a range, because a filter
    calibrated on the midpoint admits instruments the account could not actually
    trade at the bottom of the range.
    """

    equity_quote: Notional
    max_positions: int
    min_notional_fraction: Decimal = Decimal("0.25")
    lot_rounding_fraction: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.max_positions <= 0:
            raise ValueError("max_positions must be positive")

    @property
    def target_position(self) -> Notional:
        """Equity divided by the maximum number of concurrent positions."""
        return Notional(self.equity_quote.amount / Decimal(self.max_positions))

    @property
    def max_min_notional(self) -> Notional:
        """The largest venue minimum this account can work with."""
        return Notional(self.target_position.amount * self.min_notional_fraction)

    @property
    def max_rounding_loss(self) -> Notional:
        """The largest acceptable quantisation error, in quote units."""
        return Notional(self.target_position.amount * self.lot_rounding_fraction)


# -- Rule 1 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuoteCurrencyRule(_Rule):
    """The quote currency must be one the account can actually fund and trade."""

    allowed: frozenset[str]
    label: str = "quote_currency"

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return self.label

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit when the quote asset is in the configured policy set."""
        return RuleOutcome.ADMIT if instrument.quote in self.allowed else RuleOutcome.REJECT


# -- Rule 2 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListingAgeRule(_Rule):
    """The instrument must have been listed long enough to have a usable history.

    Defends against listing-pump artefacts, which are real, large and not
    repeatable, and against the look-ahead of scoring an instrument on a window
    that predates its own existence.

    An instrument whose listing window is ``UNVERIFIED`` is *not evaluable*
    here: never rejected and never admitted. This matters more than it looks. A
    venue that truncates its history at two years hands back a first bar that is
    the edge of its own window rather than the day the pair listed. Treating
    that edge as a listing date would age every instrument from the same
    fictional birthday and produce a confident, wrong universe.
    """

    minimum_days: int = 180

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "listing_age"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit when ``at`` is at least ``minimum_days`` after a sourced listing."""
        if instrument.provenance is Provenance.UNVERIFIED:
            return RuleOutcome.NOT_EVALUABLE
        age_reached = instrument.listed_at.plus(timedelta(days=self.minimum_days))
        return RuleOutcome.ADMIT if at >= age_reached else RuleOutcome.REJECT


# -- Rule 3 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MedianQuoteVolumeRule(_Rule):
    """Rolling median turnover in quote units must clear a floor.

    Defends against liquidity selection bias: illiquid names produce
    spectacular paper returns that evaporate the moment a real order arrives.
    """

    minimum: Notional
    histories: Mapping[InstrumentKey, InstrumentHistory]
    lookback_days: int = 30
    minimum_observations: int = 20

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "median_quote_volume"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit on a sufficiently backed median above the floor."""
        history = self.histories.get(instrument.key)
        if history is None:
            return RuleOutcome.NOT_EVALUABLE
        if history.observation_count_at(at, self.lookback_days) < self.minimum_observations:
            return RuleOutcome.NOT_EVALUABLE
        median = history.median_quote_volume(at, self.lookback_days)
        if median is None:
            return RuleOutcome.NOT_EVALUABLE
        return RuleOutcome.ADMIT if median.amount >= self.minimum.amount else RuleOutcome.REJECT


# -- Rule 4 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MedianSpreadRule(_Rule):
    """Typical spread must be tight enough for the strategy to survive costs.

    ``observed_bps`` is keyed by instrument and *by instant*, because a spread
    measured today says nothing about the spread in 2022. Where no measurement
    exists for the instant asked about, this returns ``NOT_EVALUABLE`` rather
    than guessing. Neither venue publishes historical quote data, so in practice
    this rule is evaluable only for instants at which we ran a live measurement,
    and the report states exactly which those are.
    """

    maximum_bps: Decimal
    observed_bps: Mapping[tuple[InstrumentKey, Timestamp], Decimal] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "median_spread"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit when a measured spread for this instant is inside the cap."""
        measured = self.observed_bps.get((instrument.key, at))
        if measured is None:
            return RuleOutcome.NOT_EVALUABLE
        return RuleOutcome.ADMIT if measured <= self.maximum_bps else RuleOutcome.REJECT


# -- Rule 5 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinNotionalFeasibilityRule(_Rule):
    """The venue's minimum order value must be small against a target position.

    An account rule, not a market rule. Per D1 it belongs to the executable
    universe only: an instrument we cannot size a position in is still perfectly
    good evidence about whether a signal works.
    """

    account: AccountParameters

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "min_notional_feasible"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit when the venue minimum fits inside the account's allowance."""
        if instrument.min_notional.amount <= 0:
            return RuleOutcome.ADMIT
        allowed = self.account.max_min_notional.amount
        return (
            RuleOutcome.ADMIT if instrument.min_notional.amount <= allowed else RuleOutcome.REJECT
        )


# -- Rule 6 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LotSizeFeasibilityRule(_Rule):
    """Quantity granularity must not round a target position away.

    One lot step, priced at the instrument's close, is the worst-case
    quantisation error on a position. It must stay small against the position
    itself, or the executed size stops resembling the intended one.
    """

    account: AccountParameters
    histories: Mapping[InstrumentKey, InstrumentHistory]

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "lot_size_feasible"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Admit when one lot step is worth less than the rounding allowance."""
        history = self.histories.get(instrument.key)
        if history is None:
            return RuleOutcome.NOT_EVALUABLE
        close = history.close_as_of(at)
        if close is None:
            return RuleOutcome.NOT_EVALUABLE
        step_value = instrument.lot_size.amount * close.amount
        if step_value <= 0:
            return RuleOutcome.ADMIT
        allowed = self.account.max_rounding_loss.amount
        return RuleOutcome.ADMIT if step_value <= allowed else RuleOutcome.REJECT


# -- Rule 7 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExcludedAssetClassRule(_Rule):
    """Stablecoins, wrapped duplicates, leveraged tokens and staked derivatives.

    Excluded by construction, on the grounds stated in the Phase 0 findings:
    their return process is either a peg, a copy of another member's, or a
    path-dependent decay, and none of the three is what a cross-sectional
    strategy is trying to rank.
    """

    excluded_bases: frozenset[str]
    excluded_quotes: frozenset[str] = frozenset()

    @property
    def name(self) -> str:
        """Identifier recorded in run metadata."""
        return "asset_class"

    def evaluate(self, instrument: Instrument, at: Timestamp) -> RuleOutcome:
        """Reject when either leg is in an excluded class."""
        if instrument.base in self.excluded_bases:
            return RuleOutcome.REJECT
        if instrument.quote in self.excluded_quotes:
            return RuleOutcome.REJECT
        return RuleOutcome.ADMIT

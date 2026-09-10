"""What a trade costs, itemised, with every assumption labelled as one.

Invariant 8 says no strategy is evaluated on gross PnL. This module is where the
subtraction is defined, and the whole design principle is that a single blended
number is never acceptable output. Fees behave differently from spread, spread
behaves differently from slippage, and funding accrues with time rather than
with the trade. Summing them before anyone has seen them destroys the only
information that would let a reader judge which of them is load-bearing.

Three kinds of number appear here and they are not interchangeable
------------------------------------------------------------------

**Contractual.** The venue's published fee schedule. We know these.

**Assumed, because no data exists.** Spread and slippage. Neither venue
publishes historical quote data - established in SEXTANT-002 - so there is no
measurement to use and there never will be for a past date. These are
deliberately pessimistic configured values, and every object carrying one also
carries the prose stating its basis. :class:`CostAssumption` exists so that a
number and its justification cannot be separated.

**Hypothesised, and testable later.** The maker/taker fill mix. The engine
assumes post-only limit orders and therefore maker fees. That is a hypothesis
about execution, not a property of it: a post-only order that never fills earns
no fee at all and no position either. It stays a hypothesis until paper trading
measures the real fill ratio, and until then every headline result is reported
at three mixes so the reader can see whether the conclusion depends on it. See
:class:`FillMix`.

Why spread is charged even under the maker assumption
-----------------------------------------------------

A resting post-only order does not cross the spread, so charging half of it
looks like double-counting. It is charged anyway, for two reasons. A post-only
order that fills has usually been filled by someone who wanted to trade against
it, which is adverse selection with the same sign as paying the spread. And the
alternative - assuming both maker fees and zero spread - is the single most
flattering combination available, which is exactly the combination this project
should refuse to grant itself. It is a pessimistic assumption, it is labelled
as one, and it is separately reported so it can be dialled out and the
difference seen.

Liquidity bands rather than one global number
----------------------------------------------

Spread and slippage error is worst on thin names, and thin names are where a
cross-sectional strategy believes it has found something. One global spread
assumption is therefore wrong in precisely the place it matters, and wrong in
the flattering direction. The bands are cut on trailing median quote turnover -
the same statistic the universe's liquidity rule already uses - so an instrument
cannot be in a different band for admission than it is for costing.

Pure computation. No I/O, no clock, no venue: a venue supplies the schedule as
data, and this module never learns its name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.universe.statistics import InstrumentHistory
from sextant.ports.cost import CostBreakdown, LiquidityRole, OrderSide

BASIS_POINTS = Decimal(10_000)


class CostModelError(DomainError):
    """A cost model was configured with something it cannot price."""


class LiquidityBand(StrEnum):
    """How thick an instrument's book is assumed to be.

    ``UNKNOWN`` is its own band and is priced at the worst assumption rather
    than at the middle one. An instrument whose turnover we cannot compute at
    the decision instant is not an average instrument; it is an instrument we
    know nothing about, and the cheapest way to be wrong about it is to assume
    it is cheap.
    """

    DEEP = "deep"
    MID = "mid"
    THIN = "thin"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CostAssumption:
    """A configured cost in basis points, inseparable from why it is that number.

    ``basis`` is prose and it is mandatory. A configured number travelling
    without its justification is how an assumption turns into a fact somewhere
    downstream, and the type is what stops that happening.
    """

    by_band: Mapping[LiquidityBand, Decimal]
    basis: str
    label: str

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise CostModelError(
                f"{self.label} has no stated basis. A configured cost without its "
                "justification is indistinguishable from a measurement."
            )
        missing = [band for band in LiquidityBand if band not in self.by_band]
        if missing:
            raise CostModelError(
                f"{self.label} has no value for {[band.value for band in missing]}. "
                "Every band must be priced, including UNKNOWN."
            )
        for band, value in self.by_band.items():
            if value < 0:
                raise CostModelError(f"{self.label} is negative for {band.value}: {value}")

    def bps(self, band: LiquidityBand) -> Decimal:
        """The assumed cost for this band, in basis points."""
        return self.by_band[band]

    def as_metadata(self) -> Mapping[str, str]:
        """The assumption in a form a run manifest carries verbatim."""
        return {
            f"{self.label}_bps_by_band": ", ".join(
                f"{band.value}={self.by_band[band]}" for band in LiquidityBand
            ),
            f"{self.label}_basis": self.basis,
            f"{self.label}_kind": "assumption",
        }


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """A venue's published maker and taker fees, in basis points.

    Contractual rather than assumed: this is what the venue says it charges.
    What is *not* contractual is which of the two rates a given fill attracts,
    and that is :class:`FillMix`'s problem rather than this one's.
    """

    maker_bps: Decimal
    taker_bps: Decimal
    tier: str
    source: str

    def __post_init__(self) -> None:
        if self.maker_bps < 0 or self.taker_bps < 0:
            raise CostModelError(
                f"Fees must not be negative: maker={self.maker_bps}, taker={self.taker_bps}"
            )

    def bps_for(self, role: LiquidityRole) -> Decimal:
        """The published rate for one role."""
        return self.maker_bps if role is LiquidityRole.MAKER else self.taker_bps

    def as_metadata(self) -> Mapping[str, str]:
        """The schedule in a form a run manifest carries verbatim."""
        return {
            "fee_tier": self.tier,
            "fee_maker_bps": str(self.maker_bps),
            "fee_taker_bps": str(self.taker_bps),
            "fee_source": self.source,
            "fee_kind": "published schedule",
        }


@dataclass(frozen=True, slots=True)
class FillMix:
    """What fraction of fills are assumed to earn the maker fee.

    **This is a hypothesis and the type says so.** ``is_assumption`` is a
    constant True and cannot be set otherwise; ``label`` is what appears in
    every report line. The obligation to replace this with a measurement belongs
    to the paper-trading phase and is written into ``docs/LIVE-GATES.md``.
    """

    maker_fraction: Decimal
    label: str

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.maker_fraction <= Decimal(1):
            raise CostModelError(
                f"maker_fraction must be in [0, 1], got {self.maker_fraction}. It is a "
                "fraction of fills, not a rate."
            )

    @property
    def is_assumption(self) -> bool:
        """Always True. A fill mix is never a measurement in a backtest."""
        return True

    def effective_fee_bps(self, schedule: FeeSchedule) -> Decimal:
        """The blended fee this mix implies against a published schedule."""
        taker_fraction = Decimal(1) - self.maker_fraction
        return self.maker_fraction * schedule.maker_bps + taker_fraction * schedule.taker_bps

    def as_metadata(self) -> Mapping[str, str]:
        """The hypothesis in a form a run manifest carries verbatim."""
        return {
            "fill_mix": self.label,
            "fill_mix_maker_fraction": str(self.maker_fraction),
            "fill_mix_kind": (
                "ASSUMPTION - not measured. The engine assumes post-only limit orders "
                "and therefore maker fees. The real maker/taker fill ratio is an "
                "obligation of the paper-trading phase, per docs/LIVE-GATES.md."
            ),
        }


#: The three mixes every headline result is reported at. If a result's sign
#: flips across these, it depends on an execution property nobody has
#: demonstrated.
ALL_MAKER = FillMix(maker_fraction=Decimal(1), label="100% maker (assumed)")
HALF_AND_HALF = FillMix(maker_fraction=Decimal("0.5"), label="50/50 maker/taker (assumed)")
ALL_TAKER = FillMix(maker_fraction=Decimal(0), label="100% taker (assumed)")

REPORTED_FILL_MIXES: tuple[FillMix, ...] = (ALL_MAKER, HALF_AND_HALF, ALL_TAKER)


@runtime_checkable
class LiquidityClassifier(Protocol):
    """Something that can put an instrument in a liquidity band at an instant."""

    def band_at(self, key: InstrumentKey, at: Timestamp) -> LiquidityBand:
        """Which band this instrument falls into, using only data known at ``at``."""
        ...


@dataclass(frozen=True, slots=True)
class MedianTurnoverBands:
    """Bands cut on trailing median quote turnover, point-in-time.

    The same statistic and the same window the liquidity rule uses, so an
    instrument cannot be deep enough to enter the universe and thin enough to be
    costed as illiquid, or the reverse.
    """

    histories: Mapping[InstrumentKey, InstrumentHistory]
    deep_floor: Notional
    mid_floor: Notional
    lookback_days: int = 30
    minimum_observations: int = 20

    def band_at(self, key: InstrumentKey, at: Timestamp) -> LiquidityBand:
        """Which band this instrument falls into at ``at``, or UNKNOWN."""
        history = self.histories.get(key)
        if history is None:
            return LiquidityBand.UNKNOWN
        if history.observation_count_at(at, self.lookback_days) < self.minimum_observations:
            return LiquidityBand.UNKNOWN
        median = history.median_quote_volume(at, self.lookback_days)
        if median is None:
            return LiquidityBand.UNKNOWN
        if median.amount >= self.deep_floor.amount:
            return LiquidityBand.DEEP
        if median.amount >= self.mid_floor.amount:
            return LiquidityBand.MID
        return LiquidityBand.THIN

    def as_metadata(self) -> Mapping[str, str]:
        """The band thresholds in a form a run manifest carries verbatim."""
        return {
            "liquidity_band_deep_floor": str(self.deep_floor.amount),
            "liquidity_band_mid_floor": str(self.mid_floor.amount),
            "liquidity_band_statistic": (
                f"median quote turnover over {self.lookback_days} days, at least "
                f"{self.minimum_observations} observations, point-in-time"
            ),
        }


@dataclass(frozen=True, slots=True)
class TradeCost:
    """The cost of one trade, in quote-currency units, itemised.

    Currency rather than basis points, because this is what the ledger
    subtracts. The basis points that produced each line are carried alongside so
    that a reader can see the rate as well as the amount, and so that a report
    can show either without recomputing anything.
    """

    notional: Notional
    band: LiquidityBand
    fee: Notional
    spread: Notional
    slippage: Notional
    fee_bps: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal

    @property
    def total(self) -> Notional:
        """Everything this trade costs. Never the only figure reported.

        Funding is deliberately absent: it accrues with holding time rather than
        with the trade, so it is not a property of a trade at all. See
        :meth:`ItemisedCostModel.funding_over`.
        """
        return Notional(self.fee.amount + self.spread.amount + self.slippage.amount)


@dataclass(frozen=True, slots=True)
class ItemisedCostModel:
    """A venue's costs, assembled from a schedule, a fill mix and two assumptions.

    Satisfies the :class:`~sextant.ports.cost.CostModel` port, which prices one
    trade at one stated role. The engine does not use that entry point: a fill
    *mix* is not a role, and pretending a 50/50 assumption is a single maker
    trade would put the assumption somewhere a reader cannot see it. The engine
    calls :meth:`cost_of` instead, which prices the blend and says so.
    """

    schedule: FeeSchedule
    fill_mix: FillMix
    spread: CostAssumption
    slippage: CostAssumption
    liquidity: LiquidityClassifier
    schedule_by_venue: Mapping[Venue, FeeSchedule] = field(default_factory=dict)
    """Published schedules for a strategy whose legs trade on different ones.

    Empty by default, in which case ``schedule`` prices every leg and the
    behaviour is exactly what it was before this field existed. A cash-and-carry
    pair needs it: the same venue charges spot and perpetual futures on separate
    published schedules, and blending them into one average would report a fee
    line no venue ever charged.

    This is not venue branching. No venue name appears here or anywhere in the
    engine - only a mapping the wiring layer built from configuration, which is
    exactly the form invariant 2 requires. A key absent from the mapping falls
    back to ``schedule`` rather than raising, because a strategy on one venue must
    not have to enumerate it."""
    funding_bps_per_day: Decimal = Decimal(0)
    """Zero for spot: there is no financing leg on an unlevered cash purchase.

    Carried as its own line anyway, and charged **per day held** rather than per
    trade. A funding figure that scales with trade count is a fee wearing the
    wrong name, and it would price a monthly rebalance and a daily one
    identically. A cost that is structurally absent and a cost nobody measured
    look the same in a report that omits the row, so the row stays."""

    def cost_of(self, key: InstrumentKey, notional: Notional, at: Timestamp) -> TradeCost:
        """Price one trade of ``notional`` in ``key`` at ``at``, itemised.

        ``notional`` is taken as an absolute exposure: a sale costs what a
        purchase of the same size costs, which is true of every line here.
        """
        gross = abs(notional.amount)
        band = self.liquidity.band_at(key, at)
        fee_bps = self.fill_mix.effective_fee_bps(self.schedule_for(key))
        spread_bps = self.spread.bps(band)
        slippage_bps = self.slippage.bps(band)
        return TradeCost(
            notional=Notional(gross),
            band=band,
            fee=Notional(gross * fee_bps / BASIS_POINTS),
            spread=Notional(gross * spread_bps / BASIS_POINTS),
            slippage=Notional(gross * slippage_bps / BASIS_POINTS),
            fee_bps=fee_bps,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
        )

    def schedule_for(self, key: InstrumentKey) -> FeeSchedule:
        """The published schedule this instrument's venue charges."""
        return self.schedule_by_venue.get(key.venue, self.schedule)

    def funding_over(self, notional: Notional, days_held: int) -> Notional:
        """What holding ``notional`` for ``days_held`` days costs in financing.

        Proportional to time, not to the number of trades. Zero for spot, which
        is every position in this project so far, and the shape is right for the
        day a leveraged or perpetual instrument appears.
        """
        if days_held < 0:
            raise CostModelError(f"days_held must not be negative, got {days_held}")
        rate = self.funding_bps_per_day * Decimal(days_held) / BASIS_POINTS
        return Notional(abs(notional.amount) * rate)

    def estimate(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: Quantity,
        reference_price: Price,
        role: LiquidityRole,
        at: Timestamp,
    ) -> CostBreakdown:
        """Port conformance: one trade at one explicit role, in basis points.

        ``side`` does not enter the arithmetic. Every line here is symmetric in
        direction, and a model that charged more to sell than to buy would be
        claiming something about this venue that nobody has measured.
        """
        band = self.liquidity.band_at(instrument.key, at)
        del side, quantity, reference_price
        return CostBreakdown(
            fee_bps=self.schedule_for(instrument.key).bps_for(role),
            spread_bps=self.spread.bps(band),
            slippage_bps=self.slippage.bps(band),
            funding_bps=self.funding_bps_per_day,
        )

    def with_fill_mix(self, mix: FillMix) -> ItemisedCostModel:
        """The same model at a different fill mix, for the three-way report."""
        return ItemisedCostModel(
            schedule=self.schedule,
            fill_mix=mix,
            spread=self.spread,
            slippage=self.slippage,
            liquidity=self.liquidity,
            schedule_by_venue=self.schedule_by_venue,
            funding_bps_per_day=self.funding_bps_per_day,
        )

    def as_metadata(self) -> Mapping[str, str]:
        """Every assumption and every published rate, for the run manifest."""
        per_venue: dict[str, str] = {}
        for venue, schedule in sorted(self.schedule_by_venue.items()):
            for name, value in schedule.as_metadata().items():
                per_venue[f"{venue.name}_{name}"] = value
        return {
            **per_venue,
            **self.schedule.as_metadata(),
            **self.fill_mix.as_metadata(),
            **self.spread.as_metadata(),
            **self.slippage.as_metadata(),
            "funding_bps_per_day": str(self.funding_bps_per_day),
            "funding_basis": (
                "zero: these are spot positions with no financing leg. Charged per day "
                "held rather than per trade, so a change of rebalance frequency would "
                "move it. Reported as its own line so that structurally absent and never "
                "measured stay distinct."
            ),
        }

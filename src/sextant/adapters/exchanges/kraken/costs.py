"""This venue's fee schedule and cost assumptions, as data.

The arithmetic lives in :mod:`sextant.engine.execution.costs`, which never
learns whose fees it is applying. What is venue-specific is the numbers, and
they belong here beside the rest of this venue's adapter.

The fee tier, and one disagreement worth recording
---------------------------------------------------

The SEXTANT-004 brief specifies tier 1 at **0.40% maker and 0.80% taker**, and
that is what is implemented. It is worth stating once that this venue publishes
a *Pro* spot schedule whose lowest tier is materially cheaper, and that the
0.40/0.80 pair corresponds to its simple buy/sell path rather than to its order
book. The brief's numbers are therefore conservative rather than wrong, and
being conservative about costs is the right direction for this project to err
in, so they are used unchanged. If the account ends up trading through the Pro
order book, every net figure in every report improves and none of the
conclusions in this phase get worse.

Stablecoin and FX pairs sit on their own, cheaper schedule, which is what the
EUR/USD conversion leg is priced at.

Spread and slippage are assumptions, and are labelled as such everywhere
------------------------------------------------------------------------

No historical quote data exists for this venue - SEXTANT-002 established that
and it is not recoverable. The values below were chosen to be pessimistic rather
than accurate, and each carries the prose that says so. They are not
measurements, nothing in the codebase presents them as measurements, and the
type they are carried in refuses to be constructed without a stated basis.

The bands are cut at 5,000,000 and 1,000,000 quote units of trailing median
daily turnover. The lower cut sits deliberately above the universe's own
250,000 liquidity floor, so that an instrument scraping into the universe is
costed as thin rather than as typical.
"""

from __future__ import annotations

from decimal import Decimal

from sextant.domain.money import Notional
from sextant.engine.execution.costs import (
    CostAssumption,
    FeeSchedule,
    LiquidityBand,
)
from sextant.engine.execution.fx import FxLeg, FxPolicy, FxRates

#: The spot schedule the brief specifies. Contractual, not assumed.
SPOT_FEES = FeeSchedule(
    maker_bps=Decimal(40),
    taker_bps=Decimal(80),
    tier="tier 1 (30-day volume below the first published threshold)",
    source=(
        "SEXTANT-004 brief, stated as this venue's tier 1 spot schedule: 0.40% maker, "
        "0.80% taker. Conservative against the venue's Pro order-book schedule; see the "
        "module docstring."
    ),
)

#: The stablecoin and FX pair schedule, used for the currency conversion leg.
FX_PAIR_FEE_BPS = Decimal(20)

#: Turnover floors, in the instrument's quote currency, for the liquidity bands.
DEEP_BAND_FLOOR = Notional(Decimal(5_000_000))
MID_BAND_FLOOR = Notional(Decimal(1_000_000))

SPREAD_ASSUMPTION = CostAssumption(
    by_band={
        LiquidityBand.DEEP: Decimal(10),
        LiquidityBand.MID: Decimal(25),
        LiquidityBand.THIN: Decimal(60),
        LiquidityBand.UNKNOWN: Decimal(60),
    },
    basis=(
        "ASSUMPTION, not a measurement. No historical quote data exists for this venue "
        "(SEXTANT-002), so no past spread is recoverable and none ever will be. These are "
        "half-spread charges in basis points of notional, chosen to be pessimistic: 10 bps "
        "on names turning over more than 5,000,000 a day, 25 bps between 1,000,000 and "
        "5,000,000, and 60 bps below that. The thin figure is deliberately punitive because "
        "that is where the error is largest and where a cross-sectional strategy believes "
        "it finds edge. An instrument whose turnover cannot be computed at the decision "
        "instant is priced at the thin rate rather than the average one. Charged on every "
        "trade including those assumed to be maker fills: a resting order that gets filled "
        "has usually been filled by someone who wanted to trade against it."
    ),
    label="spread",
)

SLIPPAGE_ASSUMPTION = CostAssumption(
    by_band={
        LiquidityBand.DEEP: Decimal(5),
        LiquidityBand.MID: Decimal(10),
        LiquidityBand.THIN: Decimal(25),
        LiquidityBand.UNKNOWN: Decimal(25),
    },
    basis=(
        "ASSUMPTION, not a measurement. Market impact and price drift between decision and "
        "fill, in basis points of notional, on the same liquidity bands as the spread "
        "assumption and at roughly half its size. Nothing in this dataset can calibrate it: "
        "the archive carries daily bars, and slippage is an intraday quantity. Chosen to be "
        "pessimistic, and reported on its own line so that a result's sensitivity to it can "
        "be read off rather than argued about."
    ),
    label="slippage",
)

FX_CONVERSION_BASIS = (
    "ASSUMPTION as to the applicable schedule, though a much better founded one than the "
    "spread and slippage figures. This venue publishes a separate, cheaper fee schedule for "
    "stablecoin and FX pairs, and EUR/USD is one of those pairs; 20 bps is its tier 1 rate. "
    "It is not verified against the venue's live schedule by this run, and it is charged "
    "once on the way into the foreign currency and once on the way back out."
)


def fx_leg(policy: FxPolicy, rates: FxRates | None = None) -> FxLeg:
    """The currency leg under one policy, priced at this venue's FX schedule."""
    return FxLeg(
        policy=policy,
        conversion_bps=FX_PAIR_FEE_BPS,
        basis=FX_CONVERSION_BASIS,
        rates=rates,
    )

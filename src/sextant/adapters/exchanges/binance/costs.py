"""This venue's fee schedule and cost assumptions, as data.

The arithmetic lives in :mod:`sextant.engine.execution.costs`, which never
learns whose fees it is applying. What is venue-specific is the numbers, and
they belong here beside the rest of this venue's adapter.

The fee tier
-------------

Spot VIP 0: **0.1000 per cent maker and 0.1000 per cent taker**, without the
fee discount the venue offers for paying in its own token and without any
volume tier. Both legs are deliberately taken at the standard rate rather than
the discounted one, because the discount is a thing an account has to opt into
and hold a balance for, and a cost model that assumes it is a cost model that
assumes a decision nobody has made.

Maker and taker being equal has a consequence worth stating: **the fill mix has
no effect on fees at this tier**. SEXTANT-004 reported every figure at three
fill mixes because the other venue charges twice as much to take as to make, and
a conclusion that moved across them depended on an unproven execution
assumption. Here the assumption cannot move the fee line at all, so one cell is
reported instead of three. It still moves the spread line, which is charged on
every fill either way, and that is reported unchanged.

Spread and slippage are assumptions, and are labelled as such everywhere
------------------------------------------------------------------------

The values below are **identical to the ones SEXTANT-004 used for the other
venue**, and that is deliberate. This venue is deeper and its real spreads are
almost certainly tighter, so carrying the previous phase's numbers over is
pessimistic. More importantly it removes any question of the spread assumption
having been retuned to suit a new dataset: a cost assumption that changes at the
same time as the venue changes is a cost assumption nobody can audit. If they
are ever revised it will be in a new pre-registered version, with both results
retained.

No historical quote data was acquired for this venue and the archive carries
daily bars, so no past spread is recoverable and slippage - an intraday quantity
- cannot be calibrated from it at all. Neither is presented as a measurement
anywhere, and the type they are carried in refuses to be constructed without the
prose that says so.
"""

from __future__ import annotations

from decimal import Decimal

from sextant.domain.money import Notional
from sextant.engine.execution.costs import CostAssumption, FeeSchedule, LiquidityBand
from sextant.engine.execution.fx import FxLeg, FxPolicy, FxRates

#: The venue's published spot schedule at the entry tier. Contractual, not assumed.
SPOT_FEES = FeeSchedule(
    maker_bps=Decimal(10),
    taker_bps=Decimal(10),
    tier="VIP 0 (30-day volume below the first published threshold), no token discount",
    source=(
        "The venue's published spot trading fee schedule: 0.1000% maker and 0.1000% taker "
        "at VIP 0. The discount for paying fees in the venue's own token is deliberately "
        "not applied; see the module docstring."
    ),
)

#: The conversion leg between the account's currency and the quote asset. The
#: venue prices EUR/USDT as an ordinary spot pair rather than on a separate
#: stablecoin schedule, so the spot rate is what the leg is charged at.
FX_PAIR_FEE_BPS = Decimal(10)

#: Turnover floors, in the instrument's quote currency, for the liquidity bands.
#: Unchanged from SEXTANT-004, and both sit far above the universe's own 250,000
#: liquidity floor so that an instrument scraping into the universe is costed as
#: thin rather than as typical.
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
        "ASSUMPTION, not a measurement, and identical to the figures SEXTANT-004 used for "
        "the other venue. No historical quote data was acquired here and the archive carries "
        "daily bars, so no past spread is recoverable from it. These are half-spread charges "
        "in basis points of notional: 10 bps on names turning over more than 5,000,000 a day, "
        "25 bps between 1,000,000 and 5,000,000, and 60 bps below that. The thin figure is "
        "deliberately punitive because that is where the error is largest and where a "
        "cross-sectional strategy believes it finds edge. An instrument whose turnover cannot "
        "be computed at the decision instant is priced at the thin rate rather than the "
        "average one. Charged on every trade including those assumed to be maker fills: a "
        "resting order that gets filled has usually been filled by someone who wanted to "
        "trade against it. Carrying the previous phase's numbers unchanged is what makes it "
        "impossible to have retuned them to suit this dataset."
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
        "ASSUMPTION, not a measurement, and identical to the figures SEXTANT-004 used for "
        "the other venue. Market impact and price drift between decision and fill, in basis "
        "points of notional, on the same liquidity bands as the spread assumption and at "
        "roughly half its size. Nothing in this dataset can calibrate it: the archive carries "
        "daily bars and slippage is an intraday quantity. Reported on its own line so that a "
        "result's sensitivity to it can be read off rather than argued about."
    ),
    label="slippage",
)

FX_CONVERSION_BASIS = (
    "ASSUMPTION as to the applicable schedule, though a well founded one. The account's "
    "currency and the quote asset are both sides of an ordinary spot pair on this venue, so "
    "the conversion is charged at the same VIP 0 spot rate of 10 bps rather than at a "
    "separate stablecoin tariff. It is not verified against the venue's live schedule by this "
    "run, and it is charged once on the way into the quote asset and once on the way back out."
)


def fx_leg(policy: FxPolicy, rates: FxRates | None = None) -> FxLeg:
    """The currency leg under one policy, priced at this venue's schedule."""
    return FxLeg(
        policy=policy,
        conversion_bps=FX_PAIR_FEE_BPS,
        basis=FX_CONVERSION_BASIS,
        rates=rates,
    )

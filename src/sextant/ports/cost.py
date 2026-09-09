"""The CostModel port.

Execution costs are first-class. No strategy in this system is ever evaluated on
gross PnL: the backtester asks a CostModel what the trade actually costs, and
the answer is subtracted before any performance number is produced.

Which venue is best for a strategy is therefore an output of backtesting rather
than an assumption, because the same strategy priced through two venues' cost
models produces two different net results.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sextant.domain.instrument import Instrument
from sextant.domain.money import Price, Quantity
from sextant.domain.time import Timestamp


class OrderSide(StrEnum):
    """Which way a fill goes."""

    BUY = "buy"
    SELL = "sell"


class LiquidityRole(StrEnum):
    """Whether the order added liquidity or took it. Fees differ, often by a lot."""

    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Every component of the cost of one trade, in basis points of notional.

    Kept itemised rather than summed because the components behave differently:
    fees are contractual, spread and slippage are market state, and funding
    accrues with holding time rather than with the trade.
    """

    fee_bps: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    funding_bps: Decimal

    @property
    def total_bps(self) -> Decimal:
        """The all-in cost of the trade in basis points."""
        return self.fee_bps + self.spread_bps + self.slippage_bps + self.funding_bps


@runtime_checkable
class CostModel(Protocol):
    """What it costs to trade a given instrument on a given venue."""

    def estimate(
        self,
        instrument: Instrument,
        side: OrderSide,
        quantity: Quantity,
        reference_price: Price,
        role: LiquidityRole,
        at: Timestamp,
    ) -> CostBreakdown:
        """The estimated cost of one trade, itemised, in basis points of notional."""
        ...

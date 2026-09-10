"""How often a carry book may turn over before its fees eat the carry.

Amendment 27. A verdict of "survives" or "dies" at one fee schedule compresses the
quantity that actually binds into a word, and makes the answer useless the moment a
fee schedule changes. A break-even turnover carries the fee level in its denominator,
so a reader with different fees divides again instead of asking for another backtest.

The unit, and why it is the whole book
--------------------------------------

A **round trip** is opening both legs of every held pair and closing them again. Per
book rather than per pair, because that is the unit the fee arithmetic already uses
and because a per-pair figure would depend on the position count, which differs
between variants.

Everything is expressed in **basis points of equity**, not of leg notional, so the
numerator and the denominator share units. One pair consumes ``s * (1 + m)`` of
capital, so one leg's notional per unit of equity at full deployment is ``1 / (1 + m)``
and a round trip charged at ``f`` basis points of leg notional costs
``f / (1 + m)`` basis points of equity.

What this deliberately leaves out
---------------------------------

**The conversion leg**, charged twice for a whole run rather than per rebalance:
folding a fixed cost into a per-round-trip figure would attribute it to turnover.

**Spread and slippage**, which are assumptions rather than published rates and which
also scale with turnover. The fee break-even is therefore an **upper bound** on
allowable turnover rather than an estimate of it, and :attr:`BreakEven.is_upper_bound`
exists so no report can print the number without that being true on the object.

Exact Decimal arithmetic throughout. No floats, no arrays, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sextant.domain.errors import DomainError

#: Reporting scale for a turnover figure. Two decimals: the difference between 2.83
#: and 2.84 round trips a year is not a distinction any of this can support.
TURNOVER_SCALE = Decimal("0.01")

#: Amendment 27's D2b threshold. A full monthly rebalance is twelve round trips a
#: year, so this is the frequency the family actually trades at.
MONTHLY_ROUND_TRIPS = Decimal(12)


class BreakEvenUndefined(DomainError):
    """The break-even cannot be computed, and is not silently reported as zero."""


def fee_of_equity_bps(round_trip_fee_bps: Decimal, margin_fraction: Decimal) -> Decimal:
    """One full-book round trip's fees, in basis points of equity.

    ``round_trip_fee_bps`` is in basis points of one leg's notional - both legs opened
    and both closed - and the margin fraction converts it to equity.
    """
    if margin_fraction < 0:
        raise BreakEvenUndefined(f"margin_fraction must not be negative, got {margin_fraction}")
    if round_trip_fee_bps <= 0:
        raise BreakEvenUndefined(
            f"A round trip must cost something to have a break-even; got {round_trip_fee_bps} "
            "basis points. A zero-fee schedule has no break-even turnover and saying it is "
            "infinite would be a number nobody can act on."
        )
    return round_trip_fee_bps / (Decimal(1) + margin_fraction)


@dataclass(frozen=True, slots=True)
class BreakEven:
    """The three numbers amendment 27 registers, and the sentence they support."""

    gross_return_bps_per_year: Decimal
    """Out-of-sample gross return before every cost line, annualised, in bps of equity.

    Reported explicitly so both break-evens can be recomputed at any fee level. It is
    the numerator, and it is the only input that needs a run."""
    research_fee_of_equity_bps: Decimal
    execution_fee_of_equity_bps: Decimal
    realised_fees_bps_per_year: Decimal
    """Fees the engine actually charged in the headline cell, annualised, in bps of
    equity, with the conversion leg excluded."""

    def __post_init__(self) -> None:
        for name, value in (
            ("research_fee_of_equity_bps", self.research_fee_of_equity_bps),
            ("execution_fee_of_equity_bps", self.execution_fee_of_equity_bps),
        ):
            if value <= 0:
                raise BreakEvenUndefined(f"{name} must be positive, got {value}")
        if self.realised_fees_bps_per_year < 0:
            raise BreakEvenUndefined(
                f"Fees charged cannot be negative, got {self.realised_fees_bps_per_year}"
            )

    @property
    def is_upper_bound(self) -> bool:
        """Always True, and it is a property so a report cannot omit saying so.

        Spread and slippage also scale with turnover and are excluded here, so the
        break-even on total cost is strictly lower than either figure below.
        """
        return True

    @property
    def realised_round_trips_per_year(self) -> Decimal:
        """Turnover, measured by inverting the same arithmetic the fees came from.

        From the fee line rather than from reconstructed traded notional, so no
        separate estimator can disagree with it.
        """
        return self.realised_fees_bps_per_year / self.research_fee_of_equity_bps

    @property
    def break_even_at_research_fees(self) -> Decimal | None:
        """Round trips a year the gross carry pays for at research fees."""
        return self._break_even(self.research_fee_of_equity_bps)

    @property
    def break_even_at_execution_fees(self) -> Decimal | None:
        """The same at the execution venue's published fees."""
        return self._break_even(self.execution_fee_of_equity_bps)

    def _break_even(self, fee: Decimal) -> Decimal | None:
        """Gross carry divided by one round trip's cost, or None if there is no carry.

        ``None`` rather than zero when the gross return is not positive: a book that
        did not earn gross has no turnover at which fees are covered, and reporting
        that as "zero round trips" invites reading it as a tight but real threshold.
        """
        if self.gross_return_bps_per_year <= 0:
            return None
        return self.gross_return_bps_per_year / fee

    def survives(self, fee: Decimal) -> bool | None:
        """Whether realised turnover sits inside the break-even at this fee level."""
        threshold = self._break_even(fee)
        if threshold is None:
            return False
        return self.realised_round_trips_per_year <= threshold

    @property
    def survives_research_fees(self) -> bool | None:
        return self.survives(self.research_fee_of_equity_bps)

    @property
    def survives_execution_fees(self) -> bool | None:
        return self.survives(self.execution_fee_of_equity_bps)

    @property
    def d2b_holds(self) -> bool | None:
        """D2b: the execution break-even is below a monthly rebalance.

        ``None`` when there is no gross carry to divide, which is neither a
        confirmation nor a refutation and is reported as unresolved.
        """
        threshold = self.break_even_at_execution_fees
        if threshold is None:
            return None
        return threshold < MONTHLY_ROUND_TRIPS

    def predicted_execution_fees_bps_per_year(self) -> Decimal:
        """What the re-cost's own fee line must come back as, to rounding.

        The consistency check amendment 27.2 registers: this figure and the re-cost's
        measured fee line are computed by different routes, one arithmetic and one from
        a run, and a disagreement means one of them is wrong.
        """
        return self.realised_round_trips_per_year * self.execution_fee_of_equity_bps

    def sentence(self) -> str:
        """The one sentence amendment 27 asks for, in whichever of three forms holds."""
        realised = _shown(self.realised_round_trips_per_year)
        if self.gross_return_bps_per_year <= 0:
            return (
                f"The best variant earned nothing gross, so no turnover covers its fees at "
                f"either schedule; it realised {realised} full-book round trips a year."
            )
        research = _shown(self.break_even_at_research_fees)
        execution = _shown(self.break_even_at_execution_fees)
        verdict = (
            "so it clears both"
            if self.survives_execution_fees
            else (
                "so it clears the research schedule and not the execution one"
                if self.survives_research_fees
                else "so it clears neither"
            )
        )
        return (
            f"The best variant's gross carry pays for {research} full-book round trips a year at "
            f"research fees and {execution} at execution fees, against {realised} realised, "
            f"{verdict}. Both figures are upper bounds: spread and slippage also scale with "
            "turnover and are excluded."
        )

    def as_json(self) -> dict[str, object]:
        return {
            "unit": "round trips per year, whole book",
            "gross_return_bps_per_year": str(self.gross_return_bps_per_year),
            "research_fee_of_equity_bps": str(
                self.research_fee_of_equity_bps.quantize(TURNOVER_SCALE)
            ),
            "execution_fee_of_equity_bps": str(
                self.execution_fee_of_equity_bps.quantize(TURNOVER_SCALE)
            ),
            "break_even_at_research_fees": _shown(self.break_even_at_research_fees),
            "break_even_at_execution_fees": _shown(self.break_even_at_execution_fees),
            "realised_round_trips_per_year": _shown(self.realised_round_trips_per_year),
            "realised_fees_bps_per_year": str(self.realised_fees_bps_per_year),
            "survives_research_fees": self.survives_research_fees,
            "survives_execution_fees": self.survives_execution_fees,
            "d2b_holds": self.d2b_holds,
            "d2b_threshold_round_trips": str(MONTHLY_ROUND_TRIPS),
            "predicted_execution_fees_bps_per_year": str(
                self.predicted_execution_fees_bps_per_year().quantize(TURNOVER_SCALE)
            ),
            "figures_are_upper_bounds": self.is_upper_bound,
            "upper_bound_reason": (
                "Spread and slippage also scale with turnover, are identical at both venues, "
                "and are excluded here. The break-even on total cost is strictly lower than "
                "either figure. The conversion leg is excluded too: it is charged twice for a "
                "whole run rather than per rebalance."
            ),
            "sentence": self.sentence(),
        }


def _shown(value: Decimal | None) -> str:
    """A turnover figure at reporting scale, or the reason there is not one."""
    if value is None:
        return "undefined: no positive gross carry to cover fees with"
    return str(value.quantize(TURNOVER_SCALE))


def d2a_holds(
    *, clearing_turnovers: tuple[Decimal, ...], all_turnovers: tuple[Decimal, ...]
) -> bool | None:
    """D2a: every clearing variant is at or below the median realised turnover.

    ``None`` when nothing cleared, which is unresolved rather than confirmed. A
    prediction about clearing variants cannot be confirmed by there being none, and
    reporting it as held would be the softening the brief forbids.
    """
    if not all_turnovers:
        raise BreakEvenUndefined("D2a needs the whole variant set's turnover to take a median.")
    if not clearing_turnovers:
        return None
    ordered = sorted(all_turnovers)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2 == 1
        else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
    )
    return all(value <= median for value in clearing_turnovers)


__all__ = [
    "MONTHLY_ROUND_TRIPS",
    "TURNOVER_SCALE",
    "BreakEven",
    "BreakEvenUndefined",
    "d2a_holds",
    "fee_of_equity_bps",
]

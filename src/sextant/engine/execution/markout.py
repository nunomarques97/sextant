"""Marking a position out of an instrument whose history simply stops.

The defect this models is a property of quarterly archives, and it is not
recoverable from them. A quarterly archive holds the instruments listed at the
end of that quarter, so an instrument that died *inside* a quarter is dropped
from that quarter's file and its final partial quarter exists nowhere. Its
observed series therefore ends at a quarter boundary, up to three months before
it actually stopped trading.

**The bias has a direction.** A delisting announcement usually craters the
price, and the crater falls entirely inside the unobserved window. Marking a
position out at the last observed close therefore books an exit at a price that
predates the bad news. That flatters every strategy holding a name when it dies,
which is precisely the population a cross-sectional strategy over-weights.

So the exit price is the last observed close **less an explicit haircut**. The
default of 20% is a placeholder chosen to be pessimistic rather than accurate,
and the point of this module is that it is visible, tunable and separately
reported. If a strategy's verdict moves between a 0% and a 50% haircut, the
verdict is a statement about how dead names are handled, not about the strategy.

This is a mark-out assumption. It is not a cost model: no fee, spread, slippage
or funding figure appears here, and none belongs here. Those go through the
``CostModel`` port.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Notional, Price, Quantity

#: The default haircut. Deliberately pessimistic and deliberately round: it is a
#: stated assumption awaiting evidence, and a precise-looking number would imply
#: a calibration that does not exist.
DEFAULT_HAIRCUT = Decimal("0.20")

#: The fractions every result reports, so the reader can see the assumption's
#: whole range rather than the one value someone chose.
SENSITIVITY_FRACTIONS: tuple[Decimal, ...] = (
    Decimal("0.00"),
    DEFAULT_HAIRCUT,
    Decimal("0.50"),
)


class SeriesEnd(StrEnum):
    """Why an instrument's observed history stops where it does.

    The haircut applies to ``DELISTED`` alone. A series that stops because the
    archive stops is not a delisting, and haircutting it would invent a loss;
    a series that stops because the instrument is still trading at the edge of
    the data is not a delisting either.
    """

    DELISTED = "delisted"
    """A membership diff shows the instrument gone. The unobserved tail is real."""

    STILL_LISTED = "still_listed"
    """The instrument was listed at the last instant the data covers."""

    UNDETERMINED = "undetermined"
    """Membership at the end of the series falls inside a bracket."""

    @property
    def takes_haircut(self) -> bool:
        """Whether a position in this instrument is marked out at a discount."""
        return self is SeriesEnd.DELISTED


@dataclass(frozen=True, slots=True)
class DelistingHaircut:
    """The mark-out assumption, as a value that travels into run metadata."""

    fraction: Decimal = DEFAULT_HAIRCUT

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.fraction < Decimal(1):
            raise ValueError(
                f"Haircut fraction must be in [0, 1), got {self.fraction}. A haircut of "
                "1 or more marks a position out at zero or below, which is a different "
                "claim and needs its own model."
            )

    @property
    def basis_points(self) -> Decimal:
        """The haircut in basis points, for reports that state costs that way."""
        return self.fraction * Decimal(10_000)

    def exit_price(self, last_close: Price) -> Price:
        """The price a position is marked out at, given the last observed close."""
        return Price(last_close.amount * (Decimal(1) - self.fraction))

    def as_metadata(self) -> Mapping[str, str]:
        """The assumption in a form a run record can carry verbatim."""
        return {
            "delisting_haircut_fraction": str(self.fraction),
            "delisting_haircut_basis": (
                "last observed close less this fraction, applied only where a "
                "membership diff shows the instrument delisted"
            ),
        }

    def __str__(self) -> str:
        return f"{self.fraction * 100}% of the last observed close"


@dataclass(frozen=True, slots=True)
class MarkOut:
    """One position closed out, with the assumption's effect stated separately.

    ``gross_proceeds`` and ``proceeds`` are both carried so that the haircut's
    contribution is a subtraction the reader can perform, rather than a number
    folded into a total and lost.
    """

    instrument: InstrumentKey
    quantity: Quantity
    last_close: Price
    reason: SeriesEnd
    haircut: DelistingHaircut

    @property
    def applied_fraction(self) -> Decimal:
        """The fraction actually applied. Zero unless the instrument was delisted."""
        return self.haircut.fraction if self.reason.takes_haircut else Decimal(0)

    @property
    def exit_price(self) -> Price:
        """The price this position is booked out at."""
        if not self.reason.takes_haircut:
            return self.last_close
        return self.haircut.exit_price(self.last_close)

    @property
    def gross_proceeds(self) -> Notional:
        """What the position would realise at the last observed close."""
        return Notional(self.last_close.amount * self.quantity.amount)

    @property
    def proceeds(self) -> Notional:
        """What the position realises once the assumption is applied."""
        return Notional(self.exit_price.amount * self.quantity.amount)

    @property
    def haircut_cost(self) -> Notional:
        """How much of the result this assumption alone is responsible for."""
        return Notional(self.gross_proceeds.amount - self.proceeds.amount)


def mark_out(
    instrument: InstrumentKey,
    quantity: Quantity,
    last_close: Price,
    reason: SeriesEnd,
    haircut: DelistingHaircut,
) -> MarkOut:
    """Close one position out of an instrument whose series has ended."""
    return MarkOut(
        instrument=instrument,
        quantity=quantity,
        last_close=last_close,
        reason=reason,
        haircut=haircut,
    )


@dataclass(frozen=True, slots=True)
class HaircutEffect:
    """What one haircut setting costs across a whole population of mark-outs."""

    fraction: Decimal
    marked_out: int
    haircut_applied_to: int
    gross_proceeds: Notional
    proceeds: Notional

    @property
    def total_cost(self) -> Notional:
        """The assumption's total effect, in quote units."""
        return Notional(self.gross_proceeds.amount - self.proceeds.amount)

    @property
    def cost_fraction_of_gross(self) -> Decimal:
        """The effect as a fraction of what the same exits would realise unhaircut.

        ``Decimal(0)`` when there is nothing to mark out. Zero because there is
        no effect, not because the effect is unknown: an empty population has a
        measured effect of nothing.
        """
        if self.gross_proceeds.amount == 0:
            return Decimal(0)
        return self.total_cost.amount / self.gross_proceeds.amount


@dataclass(frozen=True, slots=True)
class Position:
    """A position that has to be closed because its instrument's series ended."""

    instrument: InstrumentKey
    quantity: Quantity
    last_close: Price
    reason: SeriesEnd


def haircut_sensitivity(
    positions: Sequence[Position],
    fractions: Iterable[Decimal] = SENSITIVITY_FRACTIONS,
) -> tuple[HaircutEffect, ...]:
    """The same population marked out at several haircuts, for side-by-side reading.

    This is the number the brief asks to be reported at 0%, 20% and 50%. If a
    strategy's verdict flips across that range, the strategy is an artefact of
    how dead names are handled and the range is what shows it.
    """
    effects: list[HaircutEffect] = []
    for fraction in fractions:
        haircut = DelistingHaircut(fraction=fraction)
        marked = [
            mark_out(item.instrument, item.quantity, item.last_close, item.reason, haircut)
            for item in positions
        ]
        effects.append(
            HaircutEffect(
                fraction=fraction,
                marked_out=len(marked),
                haircut_applied_to=sum(1 for item in marked if item.reason.takes_haircut),
                gross_proceeds=Notional(
                    sum((item.gross_proceeds.amount for item in marked), Decimal(0))
                ),
                proceeds=Notional(sum((item.proceeds.amount for item in marked), Decimal(0))),
            )
        )
    return tuple(effects)

"""The delisting haircut.

The point of the model is not the number, it is that the number is visible and
that its effect on any result can be read off. So most of what is asserted here
is about separation: gross and net stated apart, the haircut applied only where
a delisting was actually sourced, and the same population priced across the
whole 0% to 50% range.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Price, Quantity
from sextant.domain.venue import Venue
from sextant.engine.execution.markout import (
    DEFAULT_HAIRCUT,
    SENSITIVITY_FRACTIONS,
    DelistingHaircut,
    Position,
    SeriesEnd,
    haircut_sensitivity,
    mark_out,
)

VENUE = Venue("kraken")


def position(symbol: str, reason: SeriesEnd, close: str = "100", size: str = "2") -> Position:
    """A position that has to be closed because its instrument's series ended."""
    return Position(
        instrument=InstrumentKey(VENUE, symbol),
        quantity=Quantity(Decimal(size)),
        last_close=Price(Decimal(close)),
        reason=reason,
    )


def test_the_default_is_the_pessimistic_placeholder_the_brief_states() -> None:
    assert Decimal("0.20") == DEFAULT_HAIRCUT
    assert DelistingHaircut().fraction == Decimal("0.20")
    assert DelistingHaircut().basis_points == Decimal(2000)


def test_a_haircut_of_one_or_more_is_refused() -> None:
    """Marking out at zero or below is a different claim and needs its own model."""
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        DelistingHaircut(fraction=Decimal(1))
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        DelistingHaircut(fraction=Decimal("-0.1"))


def test_the_assumption_travels_into_run_metadata() -> None:
    """Visible and tunable is the whole requirement. A comment is neither."""
    metadata = DelistingHaircut(fraction=Decimal("0.35")).as_metadata()

    assert metadata["delisting_haircut_fraction"] == "0.35"
    assert "last observed close" in metadata["delisting_haircut_basis"]
    assert "35.00%" in str(DelistingHaircut(fraction=Decimal("0.35")))


def test_only_a_sourced_delisting_takes_the_haircut() -> None:
    """A series that stops because the archive stops is not a delisting.

    Haircutting one would invent a loss, which is the same class of error as
    failing to haircut a real delisting, only in the other direction.
    """
    haircut = DelistingHaircut()

    dead = mark_out(
        InstrumentKey(VENUE, "ANTEUR"),
        Quantity(Decimal(2)),
        Price(Decimal(100)),
        SeriesEnd.DELISTED,
        haircut,
    )
    alive = mark_out(
        InstrumentKey(VENUE, "XBTEUR"),
        Quantity(Decimal(2)),
        Price(Decimal(100)),
        SeriesEnd.STILL_LISTED,
        haircut,
    )
    unknown = mark_out(
        InstrumentKey(VENUE, "RNDREUR"),
        Quantity(Decimal(2)),
        Price(Decimal(100)),
        SeriesEnd.UNDETERMINED,
        haircut,
    )

    assert dead.exit_price == Price(Decimal(80))
    assert alive.exit_price == Price(Decimal(100))
    assert unknown.exit_price == Price(Decimal(100))
    assert dead.applied_fraction == Decimal("0.20")
    assert alive.applied_fraction == Decimal(0)
    assert SeriesEnd.DELISTED.takes_haircut
    assert not SeriesEnd.UNDETERMINED.takes_haircut


def test_the_assumptions_contribution_is_stated_separately_from_the_total() -> None:
    """Folded into one number it becomes unreadable, which defeats the point."""
    marked = mark_out(
        InstrumentKey(VENUE, "ANTEUR"),
        Quantity(Decimal(2)),
        Price(Decimal(100)),
        SeriesEnd.DELISTED,
        DelistingHaircut(),
    )

    assert marked.gross_proceeds.amount == Decimal(200)
    assert marked.proceeds.amount == Decimal(160)
    assert marked.haircut_cost.amount == Decimal(40)


def test_a_zero_haircut_leaves_the_result_untouched() -> None:
    marked = mark_out(
        InstrumentKey(VENUE, "ANTEUR"),
        Quantity(Decimal(2)),
        Price(Decimal(100)),
        SeriesEnd.DELISTED,
        DelistingHaircut(fraction=Decimal(0)),
    )

    assert marked.haircut_cost.amount == Decimal(0)
    assert marked.proceeds == marked.gross_proceeds


# -- the sensitivity ---------------------------------------------------------


def test_the_reported_range_is_zero_twenty_and_fifty_percent() -> None:
    assert (Decimal("0.00"), Decimal("0.20"), Decimal("0.50")) == SENSITIVITY_FRACTIONS


def test_the_same_population_is_priced_across_the_whole_range() -> None:
    """If a verdict flips across this range it is an artefact, and this is what shows it."""
    population = [
        position("ANTEUR", SeriesEnd.DELISTED),
        position("WAVESEUR", SeriesEnd.DELISTED),
        position("XBTEUR", SeriesEnd.STILL_LISTED),
        position("RNDREUR", SeriesEnd.UNDETERMINED),
    ]

    effects = haircut_sensitivity(population)

    assert [effect.fraction for effect in effects] == list(SENSITIVITY_FRACTIONS)
    assert all(effect.marked_out == 4 for effect in effects)
    assert all(effect.haircut_applied_to == 2 for effect in effects)
    # 800 gross across four positions; the haircut touches 400 of it.
    assert effects[0].total_cost.amount == Decimal(0)
    assert effects[1].total_cost.amount == Decimal(80)
    assert effects[2].total_cost.amount == Decimal(200)
    assert effects[2].cost_fraction_of_gross == Decimal("0.25")


def test_an_empty_population_has_a_measured_effect_of_nothing() -> None:
    """Zero because there is no effect, not because the effect is unknown."""
    effects = haircut_sensitivity([])

    assert all(effect.marked_out == 0 for effect in effects)
    assert all(effect.cost_fraction_of_gross == Decimal(0) for effect in effects)


def test_a_population_with_no_delistings_is_unaffected_at_every_setting() -> None:
    effects = haircut_sensitivity([position("XBTEUR", SeriesEnd.STILL_LISTED)])

    assert {effect.total_cost.amount for effect in effects} == {Decimal(0)}


def test_a_custom_range_may_be_asked_for() -> None:
    effects = haircut_sensitivity(
        [position("ANTEUR", SeriesEnd.DELISTED)], (Decimal("0.10"), Decimal("0.90"))
    )

    assert [effect.fraction for effect in effects] == [Decimal("0.10"), Decimal("0.90")]
    assert effects[1].total_cost.amount == Decimal(180)

"""Point-in-time universe resolution.

The two failure directions are tested separately because they are different
bugs. Including something that had not listed yet is look-ahead bias. Excluding
something that was trading then but has since been delisted is survivorship
bias. A universe that is only checked in one direction reliably has the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from sextant.domain.instrument import Instrument
from sextant.domain.time import Timestamp
from sextant.domain.universe import Universe
from tests.conftest import make_instrument, ts


@dataclass(frozen=True, slots=True)
class QuoteCurrencyRule:
    """A minimal rule, used to prove rules compose with the listing window."""

    quote: str

    @property
    def name(self) -> str:
        return f"quote_is_{self.quote}"

    def admits(self, instrument: Instrument, at: Timestamp) -> bool:
        del at
        return instrument.quote == self.quote


def test_an_instrument_listed_after_the_decision_date_is_excluded() -> None:
    future_listing = make_instrument("NEWEUR", listed_at="2024-06-01T00:00:00")
    universe = Universe.of([future_listing])

    assert universe.members_at(ts("2024-01-01T00:00:00")) == frozenset()
    assert universe.members_at(ts("2024-06-01T00:00:00")) == frozenset({future_listing})


def test_an_instrument_delisted_after_the_decision_date_is_included() -> None:
    """Survivorship bias, tested from the direction people forget."""
    later_delisted = make_instrument(
        "GONEEUR", listed_at="2019-01-01T00:00:00", delisted_at="2022-05-01T00:00:00"
    )
    universe = Universe.of([later_delisted])

    assert universe.members_at(ts("2021-03-01T00:00:00")) == frozenset({later_delisted})
    assert universe.members_at(ts("2023-03-01T00:00:00")) == frozenset()


def test_the_listing_window_is_inclusive_of_listing_and_exclusive_of_delisting() -> None:
    instrument = make_instrument(
        "EDGEEUR", listed_at="2021-01-01T00:00:00", delisted_at="2021-12-31T00:00:00"
    )
    assert instrument.is_listed_at(ts("2021-01-01T00:00:00")) is True
    assert instrument.is_listed_at(ts("2021-12-31T00:00:00")) is False


def test_membership_changes_over_time_within_one_universe() -> None:
    early = make_instrument(
        "EARLYEUR", listed_at="2019-01-01T00:00:00", delisted_at="2021-01-01T00:00:00"
    )
    late = make_instrument("LATEEUR", listed_at="2022-01-01T00:00:00")
    universe = Universe.of([early, late])

    assert universe.members_at(ts("2020-01-01T00:00:00")) == frozenset({early})
    assert universe.members_at(ts("2021-06-01T00:00:00")) == frozenset()
    assert universe.members_at(ts("2023-01-01T00:00:00")) == frozenset({late})


def test_rules_narrow_the_set_and_are_recorded_by_name() -> None:
    euro = make_instrument("BTCEUR", quote="EUR")
    tether = make_instrument("BTCUSDT", quote="USDT")
    universe = Universe.of([euro, tether], [QuoteCurrencyRule("EUR")])

    assert universe.members_at(ts("2023-01-01T00:00:00")) == frozenset({euro})
    assert universe.rule_names == ("quote_is_EUR",)


def test_a_rule_cannot_readmit_an_instrument_outside_its_listing_window() -> None:
    """The listing check is unconditional; no rule can switch it off."""

    @dataclass(frozen=True, slots=True)
    class AdmitEverything:
        @property
        def name(self) -> str:
            return "admit_everything"

        def admits(self, instrument: Instrument, at: Timestamp) -> bool:
            del instrument, at
            return True

    not_yet_listed = make_instrument("FUTUREEUR", listed_at="2030-01-01T00:00:00")
    universe = Universe.of([not_yet_listed], [AdmitEverything()])

    assert universe.members_at(ts("2024-01-01T00:00:00")) == frozenset()


def test_instruments_are_keyed_by_venue_and_symbol() -> None:
    on_one = make_instrument("BTCEUR", venue="kraken")
    on_other = make_instrument("BTCEUR", venue="binance")

    assert on_one != on_other
    assert len({on_one, on_other}) == 2
    assert str(on_one.key) == "kraken:BTCEUR"

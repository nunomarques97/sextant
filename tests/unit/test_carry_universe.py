"""The carry universe's two new rules, and the window rule that resolves the dates.

Section 6 of the F1 pre-registration adds two rules SEXTANT-005 had no need for,
and both exist because the funding stream *is* the return here rather than an
overhead. They are tested for the direction they fail in, because both could
plausibly have been written the flattering way round:

- **funding evaluability** rejects a perpetual with a hole in its published
  settlements. The tempting alternative is to sum whatever is on file, which
  ranks a partial sum against whole ones and always understates the gap;
- **bar coverage** rejects a leg whose lookback is too sparse to price, and is
  *not evaluable* when no history exists at all. Ignorance about an instrument and
  a finding about it are different answers, per invariant 9.

The window rule is tested against its own arithmetic rather than against the
archive, so a change to either can be seen for what it is.

Nothing here reads the real archive or writes anywhere. Every history, calendar
and settlement is constructed in the test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.app.spike_006_f1_world import (
    has_leveraged_stem,
    is_scaled_unit,
    month_at_or_after,
    monthly_instants,
    months_in_window,
)
from sextant.domain.instrument import Instrument
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.universe.rules import (
    BarCoverageRule,
    FundingEvaluabilityRule,
    RuleOutcome,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory
from tests.harness import ts

PERP = Venue("binance_perp")
DECISION = ts("2024-04-01T00:00:00")


def perpetual(symbol: str = "BTCUSDT", base: str = "BTC") -> Instrument:
    """One perpetual leg, with its base stated rather than derived."""
    return Instrument(
        venue=PERP,
        symbol=symbol,
        base=base,
        quote="USDT",
        listed_at=ts("2020-01-01T00:00:00"),
        tick_size=Price(Decimal(0)),
        lot_size=Quantity(Decimal(0)),
        min_notional=Notional(Decimal(0)),
        delisted_at=None,
    )


def settlements(*, count: int, skip: int | None = None, interval_hours: int = 8) -> RealisedFunding:
    """``count`` settlements ending at the decision instant, optionally with a hole.

    Built backwards from the decision instant so the last payment lands exactly on
    it, which is the shape a real month has: the rebalance instant is itself a
    settlement instant.
    """
    step = interval_hours * 60 * 60 * 1000
    rows = []
    for index in range(count):
        at = Timestamp.from_epoch_millis(DECISION.epoch_millis - index * step)
        if skip is not None and index == skip:
            continue
        rows.append(Settlement(at=at, rate=Decimal("0.0001"), interval_hours=interval_hours))
    return RealisedFunding(by_instrument={perpetual().key: tuple(reversed(rows))})


def history(*, present: int) -> InstrumentHistory:
    """``present`` daily bars ending at the decision instant.

    The held days are the most recent ones, which is the benign shape: a rule that
    merely counted would pass either way, and the count is what is being tested.
    """
    day_millis = 24 * 60 * 60 * 1000
    observations = [
        DailyObservation(
            open_time=Timestamp.from_epoch_millis(DECISION.epoch_millis - (index + 1) * day_millis),
            close=Price(Decimal(100)),
            quote_volume=Notional(Decimal(1_000_000)),
        )
        for index in range(present)
    ]
    return InstrumentHistory.of(perpetual().key, observations)


# ---------------------------------------------------------------------------
# Funding evaluability
# ---------------------------------------------------------------------------


def test_a_complete_month_of_settlements_is_admitted() -> None:
    """Ninety eight-hourly payments over thirty days, no hole."""
    rule = FundingEvaluabilityRule(schedule=settlements(count=95), trailing_days=30)
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.ADMIT


def test_one_missing_settlement_rejects_the_whole_month() -> None:
    """The rule that fires on the real archive, and the reason it exists.

    The venue did not publish one 04:00 settlement on 2026-06-24 for the
    four-hourly contracts, and the trailing sum for those symbols is therefore a
    partial sum. Ranking a partial sum against whole ones understates it, always in
    the same direction, so the asset leaves the universe for that month instead.
    """
    rule = FundingEvaluabilityRule(schedule=settlements(count=95, skip=40), trailing_days=30)
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.REJECT


def test_a_perpetual_with_no_settlements_at_all_is_rejected() -> None:
    """Not "not evaluable": the schedule can say at the instant that it has none."""
    rule = FundingEvaluabilityRule(schedule=RealisedFunding(by_instrument={}), trailing_days=30)
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.REJECT


def test_a_hole_at_the_start_of_the_window_is_caught() -> None:
    """A gap at the front is as disqualifying as one in the middle.

    Thirty days is ninety eight-hourly intervals, so index 89 is the earliest
    settlement inside the span. Removing it leaves the first in-span payment two
    intervals after the window opens, which is a hole at the front rather than a
    series that simply starts later.
    """
    rule = FundingEvaluabilityRule(schedule=settlements(count=95, skip=89), trailing_days=30)
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.REJECT


def test_a_settlement_missing_outside_the_window_does_not_disqualify() -> None:
    """The rule reads the trailing window and nothing before it.

    A contract that was published patchily a year ago is evaluable today, and
    treating an old hole as disqualifying would shrink the universe on the strength
    of data no variant reads.
    """
    rule = FundingEvaluabilityRule(schedule=settlements(count=95, skip=94), trailing_days=30)
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.ADMIT


def test_the_four_hourly_cadence_is_read_from_the_venue_s_own_column() -> None:
    """A four-hourly contract needs twice the payments, and the rule knows it.

    Inferring the cadence from the gaps would let a hole redefine the interval and
    declare the series complete, which is the defect the Settlement type carries
    ``interval_hours`` to prevent.
    """
    complete = FundingEvaluabilityRule(
        schedule=settlements(count=185, interval_hours=4), trailing_days=30
    )
    assert complete.evaluate(perpetual(), DECISION) is RuleOutcome.ADMIT
    holed = FundingEvaluabilityRule(
        schedule=settlements(count=185, skip=90, interval_hours=4), trailing_days=30
    )
    assert holed.evaluate(perpetual(), DECISION) is RuleOutcome.REJECT


# ---------------------------------------------------------------------------
# Bar coverage
# ---------------------------------------------------------------------------


def test_a_dense_lookback_is_admitted() -> None:
    rule = BarCoverageRule(
        histories={perpetual().key: history(present=90)},
        lookback_days=90,
        minimum_fraction=Decimal("0.90"),
    )
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.ADMIT


def test_a_lookback_exactly_at_the_floor_is_admitted() -> None:
    """The boundary is inclusive, and it is the registered 0.90."""
    rule = BarCoverageRule(
        histories={perpetual().key: history(present=81)},
        lookback_days=90,
        minimum_fraction=Decimal("0.90"),
    )
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.ADMIT


def test_a_sparse_lookback_is_rejected() -> None:
    """A leg priced from a third of its days turns a hedge into a one-sided bet."""
    rule = BarCoverageRule(
        histories={perpetual().key: history(present=30)},
        lookback_days=90,
        minimum_fraction=Decimal("0.90"),
    )
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.REJECT


def test_no_history_at_all_is_not_evaluable_rather_than_rejected() -> None:
    """Invariant 9: ignorance about an instrument is not a finding about it."""
    rule = BarCoverageRule(histories={}, lookback_days=90, minimum_fraction=Decimal("0.90"))
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.NOT_EVALUABLE


def test_a_zero_length_lookback_is_not_evaluable_rather_than_dividing_by_zero() -> None:
    rule = BarCoverageRule(
        histories={perpetual().key: history(present=90)},
        lookback_days=0,
        minimum_fraction=Decimal("0.90"),
    )
    assert rule.evaluate(perpetual(), DECISION) is RuleOutcome.NOT_EVALUABLE


# ---------------------------------------------------------------------------
# The pairing exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol", ["1000PEPEUSDT", "1000SHIBUSDT", "10000SATSUSDT", "1000000MOGUSDT"]
)
def test_a_scaled_unit_contract_is_excluded(symbol: str) -> None:
    """Pairing one with spot needs a unit conversion, which nobody registered."""
    assert is_scaled_unit(symbol) is True


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "1INCHUSDT", "1MBABYDOGEUSDT"])
def test_an_ordinary_contract_is_not_treated_as_scaled(symbol: str) -> None:
    """Including ones whose ticker merely starts with a digit."""
    assert is_scaled_unit(symbol) is False


@pytest.mark.parametrize("base", ["BTCUP", "ETHDOWN", "XRPBULL", "EOSBEAR"])
def test_a_leveraged_token_stem_is_excluded(base: str) -> None:
    assert has_leveraged_stem(base) is True


@pytest.mark.parametrize("base", ["BTC", "UP", "DOWN", "BULL", "BEAR"])
def test_a_bare_suffix_is_not_a_leveraged_token(base: str) -> None:
    """The stem must be longer than the suffix, or a real asset gets deleted."""
    assert has_leveraged_stem(base) is False


# ---------------------------------------------------------------------------
# The window rule
# ---------------------------------------------------------------------------


def test_a_month_start_resolves_to_itself() -> None:
    """No rounding up when the instant already is a month boundary."""
    resolved = month_at_or_after(ts("2020-11-01T00:00:00"))
    assert (resolved.year, resolved.month) == (2020, 11)


def test_a_mid_month_instant_rounds_up_to_the_next_month() -> None:
    """The rule says at or after, so a partial month is not usable."""
    resolved = month_at_or_after(ts("2020-10-29T00:00:00"))
    assert (resolved.year, resolved.month) == (2020, 11)


def test_rounding_up_from_december_rolls_the_year() -> None:
    resolved = month_at_or_after(ts("2020-12-15T00:00:00"))
    assert (resolved.year, resolved.month) == (2021, 1)


def test_an_instant_one_second_past_a_month_start_rounds_up() -> None:
    """A month that has already begun cannot be the first usable month."""
    resolved = month_at_or_after(ts("2020-11-01T00:00:01"))
    assert (resolved.year, resolved.month) == (2020, 12)


def test_the_month_count_spans_the_window_and_excludes_its_end() -> None:
    """2020-11 to 2026-08 is 69 months, which is what the archive resolves to."""
    first = month_at_or_after(ts("2020-11-01T00:00:00"))
    last = month_at_or_after(ts("2026-08-01T00:00:00"))
    assert months_in_window(first, last) == 69


def test_the_instants_include_the_window_s_own_end() -> None:
    """The last instant liquidates rather than opening, so it is present."""
    instants = monthly_instants(
        month_at_or_after(ts("2024-01-01T00:00:00")),
        month_at_or_after(ts("2024-04-01T00:00:00")),
    )
    assert [at.isoformat()[:10] for at in instants] == [
        "2024-01-01",
        "2024-02-01",
        "2024-03-01",
        "2024-04-01",
    ]


def test_the_instants_roll_the_year() -> None:
    """A window crossing December must not stop at it."""
    instants = monthly_instants(
        month_at_or_after(ts("2023-11-01T00:00:00")),
        month_at_or_after(ts("2024-02-01T00:00:00")),
    )
    assert [at.isoformat()[:7] for at in instants] == ["2023-11", "2023-12", "2024-01", "2024-02"]

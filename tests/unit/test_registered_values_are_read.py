"""Every registered value, perturbed, to prove some code path actually reads it.

Two registered values in this family were verified by the drift guard and consumed
by nothing: the funding schedule and the delisting haircut's either-side flag. Both
guards passed, because a guard that compares a configuration against a constant
proves the specification says what it says and proves nothing about whether the
constant reaches the machinery.

**A keyword test is not enough either.** Asserting that ``funding=world.funding``
appears in the wiring proves the name was typed, not that the value changes an
answer. The invariant that catches the whole class is mechanical: run a fixture
twice with two materially different values for one registered parameter, and assert
the outputs differ. A parameter whose perturbation changes nothing is unread,
whatever the wiring says.

So this module builds a small in-memory ``World`` - a real one, with real
instruments, calendars, a funding schedule and a carry universe - and puts it
through the family's own ``build_engine``. Every assertion therefore exercises the
F1 wiring rather than the engine in isolation, which is where both defects lived.

What is covered, and what is not
--------------------------------

Covered by perturbation: the per-leg fees, the fill mix, spread, slippage, the
spread-and-slippage multiplier, the margin fraction, account equity, the delisting
haircut fraction, the either-side flag, the funding schedule, the position count,
the rebalance cadence, the in-sample months and the fold count, and the four
universe thresholds that carry a number.

Not covered by perturbation, with the reason stated in each test: the engine lookback
and the FX conversion charge, which need a fixture longer and a routing wider than
this module builds. Both are asserted present in the wiring and named here so the gap
is visible rather than implied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from sextant.adapters.exchanges.binance.archive import Month
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.app import spike_006_f1_engine as wiring
from sextant.app.spike_006_f1 import REGISTERED_CELLS, VariantSpec
from sextant.app.spike_006_f1_run import build_variant
from sextant.app.spike_006_f1_world import (
    PERP_VENUE,
    SPOT_VENUE,
    CalendarSeriesEnd,
    CarryUniverse,
    Census,
    ResolvedWindow,
)
from sextant.app.spike_006_f1_world import World as CarryWorld
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.backtest.ledger import Ledger
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.execution.fx import CurrencyRouting, FxRates
from sextant.engine.strategies.carry import CarrySignal, PremiumIndex
from sextant.engine.universe.rules import (
    BarCoverageRule,
    FundingEvaluabilityRule,
    ListingAgeRule,
    MedianQuoteVolumeRule,
    RuleOutcome,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory
from tests.harness import InMemoryBarRepository, daily_bars, ts

#: The real venues, not fixture ones. The carry pairing rule decides which leg is
#: which by comparing an instrument's venue against the registered perpetual venue,
#: so a fixture with invented venue names would put both legs on the same side and
#: the pairing would refuse before any perturbation could be observed.
SPOT = SPOT_VENUE
PERP = PERP_VENUE

DATA_START = "2020-10-01T00:00:00"
DATA_DAYS = 500
FIRST_MONTH = Month(2021, 1)
BASES = ("AAA", "BBB", "CCC")

#: A rate high enough that a funding line is unmistakable against rounding.
FUNDING_RATE = "0.0005"


def leg(base: str, venue: Venue) -> Instrument:
    """One leg, with its base asset stated rather than sliced off the symbol."""
    return Instrument(
        venue=venue,
        symbol=f"{base}USDT",
        base=base,
        quote="USDT",
        listed_at=ts("2020-01-01T00:00:00"),
        tick_size=Price(Decimal("0.0001")),
        lot_size=Quantity(Decimal("0.00000001")),
        min_notional=Notional(Decimal(1)),
    )


def _months(count: int) -> tuple[Month, ...]:
    """Consecutive months from the fixture's first, for the listing calendars."""
    out: list[Month] = []
    year, month = FIRST_MONTH.year, FIRST_MONTH.month
    for _ in range(count):
        out.append(Month(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(out)


def _calendar(
    venue: Venue, symbols: Sequence[str], months: Sequence[Month]
) -> BinanceListingCalendar:
    """A calendar in which every symbol is present in every month."""
    return BinanceListingCalendar.from_presence(
        venue,
        {symbol: [True] * len(months) for symbol in symbols},
        list(months),
    )


def _funding(rates: Mapping[InstrumentKey, str]) -> RealisedFunding:
    """Eight-hourly settlements, each instrument at its own rate."""
    start = ts(DATA_START)
    return RealisedFunding.of(
        {
            key: tuple(
                Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal(rate))
                for step in range(DATA_DAYS * 3)
            )
            for key, rate in rates.items()
        }
    )


#: Three price levels. The harness derives a bar's quote volume from its close, and
#: the liquidity signal reads the bars rather than the histories, so the price level
#: is what the ranking sees. Both legs of a base always share a level, so the hedge
#: still cancels exactly and every figure remains a cost or a funding line.
LEVELS = ("300", "200", "100")

#: How often the ranking rotates by one place, in days. Shorter than a quarter on
#: purpose: a monthly variant then catches rotations a quarterly one sleeps through,
#: which is the whole difference cadence is supposed to make.
ROTATION_DAYS = 60

#: Funding rate multiples per base, in the opposite order to the price levels, so a
#: signal ranking on liquidity and one ranking on funding cannot accidentally select
#: the same pairs.
RATE_MULTIPLES = {"AAA": Decimal(1), "BBB": Decimal(2), "CCC": Decimal(3)}


def _closes(index: int, *, rotate: bool) -> tuple[str, ...]:
    """One base's daily close path, at a fixed level or rotating through the three."""
    if not rotate:
        return (LEVELS[index],) * DATA_DAYS
    return tuple(LEVELS[(index + day // ROTATION_DAYS) % len(LEVELS)] for day in range(DATA_DAYS))


def _history(key: InstrumentKey, closes: tuple[str, ...]) -> InstrumentHistory:
    """A daily history matching the bars, for the cost model's liquidity bands."""
    start = ts(DATA_START)
    return InstrumentHistory.of(
        key,
        (
            DailyObservation(
                open_time=start.plus(timedelta(days=day)),
                close=Price(Decimal(closes[day])),
                quote_volume=Notional(Decimal(closes[day]) * Decimal(1000)),
            )
            for day in range(DATA_DAYS)
        ),
    )


def world(
    *,
    funding_rate: str = FUNDING_RATE,
    with_funding: bool = True,
    rotate_liquidity: bool = False,
) -> CarryWorld:
    """A small carry world: three pairs, flat prices, a real funding schedule.

    Flat prices on purpose. The hedge cancels exactly on identical legs, so every
    figure this fixture produces is a cost line or a funding line, and a perturbation
    that moves one of those moves it visibly rather than against a background of
    market noise.
    """
    months = _months(24)
    instruments: dict[InstrumentKey, Instrument] = {}
    spot_histories: dict[InstrumentKey, InstrumentHistory] = {}
    perp_histories: dict[InstrumentKey, InstrumentHistory] = {}
    repository = InMemoryBarRepository()
    members: list[InstrumentKey] = []
    for index, base in enumerate(BASES):
        spot, perp = leg(base, SPOT), leg(base, PERP)
        closes = _closes(index, rotate=rotate_liquidity)
        for item in (spot, perp):
            instruments[item.key] = item
            repository.add(daily_bars(item, first_day=DATA_START, closes=closes))
        spot_histories[spot.key] = _history(spot.key, closes)
        perp_histories[perp.key] = _history(perp.key, closes)
        members.extend((spot.key, perp.key))

    instants = tuple(month.starts_at for month in months)
    universe = CarryUniverse(
        label="carry/test",
        members=dict.fromkeys(instants, tuple(sorted(members))),
        bases=dict.fromkeys(instants, BASES),
    )
    spot_calendar = _calendar(SPOT, [f"{base}USDT" for base in BASES], months)
    perp_calendar = _calendar(PERP, [f"{base}USDT" for base in BASES], months)
    rates = {
        leg(base, PERP).key: str(Decimal(funding_rate) * RATE_MULTIPLES[base]) for base in BASES
    }
    return CarryWorld(
        instruments=instruments,
        perpetual_histories=perp_histories,
        spot_histories=spot_histories,
        repository=_routed(repository),
        spot_calendar=spot_calendar,
        perpetual_calendar=perp_calendar,
        series_end=CalendarSeriesEnd(by_venue={SPOT: spot_calendar, PERP: perp_calendar}),
        funding=_funding(rates) if with_funding else RealisedFunding.of({}),
        premium=PremiumIndex(by_instrument={}),
        fx_rates=FxRates(
            foreign_currency="USDT",
            account_currency="EUR",
            observations={},
            source="the perturbation fixture quotes everything in the account currency",
        ),
        routing=CurrencyRouting(
            account_currency="EUR",
            quote_by_symbol={key.symbol: "EUR" for key in instruments},
        ),
        universe=universe,
        window=ResolvedWindow(
            first_month=months[0],
            last_month_end=months[-1],
            fx_first_day=ts(DATA_START),
            perpetual_last_month=months[-1],
            spot_last_month=months[-1],
            usable_months=len(months),
        ),
        instants=instants,
        regimes=(),
        eur_closes={},
        census=Census(
            perpetual_symbols_seen=len(BASES),
            scaled_unit_symbols=(),
            perpetuals_without_premium=(),
            perpetuals_without_bars=(),
            perpetual_rejections={},
            perpetual_not_evaluable={},
            spot_rejections={},
            spot_not_evaluable={},
            bases_without_spot_leg=0,
            bases_without_perpetual_leg=0,
            undetermined_membership=0,
        ),
    )


def _routed(repository: InMemoryBarRepository) -> object:
    """The venue-routed repository the world declares, over one in-memory store."""
    from sextant.adapters.storage.repository import VenueRoutedBarRepository

    return VenueRoutedBarRepository(by_venue={SPOT: repository, PERP: repository})


_CACHE: dict[str, CarryWorld] = {}


def cached_world(**options: object) -> CarryWorld:
    """The fixture world, built once per distinct set of options.

    Twenty perturbations over a twenty-four-month world with five hundred bars a leg
    is a slow suite if each one rebuilds it. The cache is keyed on the options
    because two tests that ask for different worlds must not share one.
    """
    key = repr(sorted(options.items()))
    if key not in _CACHE:
        _CACHE[key] = world(**options)  # type: ignore[arg-type]
    return _CACHE[key]


TURNOVER_SPEC = VariantSpec(label="probe", signal=CarrySignal.TURNOVER, positions=2)


def ledger_of(
    *,
    cell_overrides: Mapping[str, object] | None = None,
    spec: VariantSpec = TURNOVER_SPEC,
    built: CarryWorld | None = None,
    in_sample: int | None = None,
    folds: int | None = None,
) -> Ledger:
    """Run the probe variant through the family's own wiring and keep the ledger."""
    subject = built if built is not None else cached_world()
    headline = next(item for item in REGISTERED_CELLS if item.is_headline)
    if cell_overrides:
        headline = replace(headline, **cell_overrides)  # type: ignore[arg-type]
    cell = wiring.cell_from(headline)
    engine = wiring.build_engine(subject, cell)
    plan = wiring.walk_forward_plan(subject, fold_count=folds if folds is not None else 2)
    if in_sample is not None:
        from sextant.engine.backtest.window import anchored_plan

        plan = anchored_plan(
            first_month=subject.window.first_month.starts_at,
            total_months=subject.window.usable_months,
            in_sample_months=in_sample,
            fold_count=folds if folds is not None else 2,
        )
    return wiring.run_allocator(engine, build_variant(spec, subject), plan, "probe").ledger


def signature(ledger: Ledger) -> tuple[str, ...]:
    """Every figure a perturbation could move, as text so nothing rounds together."""
    costs = ledger.costs
    return (
        str(ledger.net_pnl.amount),
        str(ledger.gross_pnl.amount),
        str(ledger.turnover.amount),
        str(costs.fees.amount),
        str(costs.spread.amount),
        str(costs.slippage.amount),
        str(costs.funding.amount),
        str(costs.fx_conversion.amount),
        str(costs.delisting.amount),
    )


# ---------------------------------------------------------------------------
# The cell: fees, fill mix, spread, slippage, the stress multiplier
# ---------------------------------------------------------------------------


BASELINE = ledger_of()


@pytest.mark.parametrize(
    ("field", "value", "line"),
    [
        ("spot_maker_bps", Decimal(200), "fees"),
        ("spot_taker_bps", Decimal(200), "fees"),
        ("futures_maker_bps", Decimal(200), "fees"),
        ("futures_taker_bps", Decimal(200), "fees"),
        ("maker_fraction", Decimal(1), "fees"),
        ("spread_and_slippage_multiplier", Decimal(4), "spread"),
    ],
)
def test_perturbing_a_registered_cell_value_moves_the_line_it_prices(
    field: str, value: Decimal, line: str
) -> None:
    """A cell value that changes no cost line is a cell value nothing read."""
    perturbed = ledger_of(cell_overrides={field: value})
    assert signature(perturbed) != signature(BASELINE), f"{field} moved nothing at all"
    before = getattr(BASELINE.costs, line).amount
    after = getattr(perturbed.costs, line).amount
    assert after != before, f"{field} moved something, but not the {line} line"


def test_the_stress_multiplier_moves_slippage_as_well_as_spread() -> None:
    """Section 8 doubles both, so a multiplier that moved one would be half-wired."""
    perturbed = ledger_of(cell_overrides={"spread_and_slippage_multiplier": Decimal(4)})
    assert perturbed.costs.spread.amount != BASELINE.costs.spread.amount
    assert perturbed.costs.slippage.amount != BASELINE.costs.slippage.amount


def test_the_fill_mix_only_matters_because_maker_and_taker_differ() -> None:
    """The reason section 8 makes the headline the middle cell rather than the cheapest.

    The futures schedule charges maker and taker differently, so the mix moves the
    fee line. Stated as a test because if the two were ever equal the mix would
    silently stop being an assumption that matters, and the report would still label
    it as one.
    """
    maker_only = ledger_of(cell_overrides={"maker_fraction": Decimal(1)})
    taker_only = ledger_of(cell_overrides={"maker_fraction": Decimal(0)})
    assert maker_only.costs.fees.amount != taker_only.costs.fees.amount


# ---------------------------------------------------------------------------
# The module constants build_engine passes
# ---------------------------------------------------------------------------


def test_the_funding_schedule_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first of the two defects, as a perturbation rather than a keyword check."""
    del monkeypatch
    without = ledger_of(built=cached_world(with_funding=False))
    assert BASELINE.costs.funding.amount != Decimal(0)
    assert without.costs.funding.amount == Decimal(0)
    assert signature(BASELINE) != signature(without)


def test_the_funding_rate_itself_is_read_and_not_only_its_presence() -> None:
    """A schedule consumed as a boolean would pass the test above and fail this."""
    doubled = ledger_of(built=cached_world(funding_rate="0.0010"))
    assert doubled.costs.funding.amount != BASELINE.costs.funding.amount


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("sextant.app.spike_006_f1_engine.ACCOUNT_EQUITY", Notional(Decimal(9000))),
        ("sextant.app.spike_006_f1_run.MARGIN_FRACTION", Decimal("0.60")),
    ],
)
def test_perturbing_a_registered_constant_changes_the_run(
    monkeypatch: pytest.MonkeyPatch, target: str, value: object
) -> None:
    """Each constant, moved where the code that reads it resolves the name.

    By dotted path rather than on one module, because the margin fraction is read by
    the strategy builder and the equity by the engine wiring. Patching a name onto a
    module that never imported it would perturb nothing while appearing to.
    """
    monkeypatch.setattr(target, value)
    assert signature(ledger_of()) != signature(BASELINE), f"{target} moved nothing"


def test_the_haircut_fraction_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only observable on a book that loses a position, so it gets a delisting world."""
    delisted = replace(cached_world(), series_end=_delisting_oracle(leg("AAA", PERP).key))
    baseline = ledger_of(built=delisted)
    assert baseline.costs.delisting.amount != Decimal(0), "the fixture must delist something"
    monkeypatch.setattr(wiring, "HAIRCUT_FRACTION", Decimal("0.80"))
    after = ledger_of(built=delisted).costs.delisting.amount
    assert after != baseline.costs.delisting.amount


def test_the_either_side_haircut_flag_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second defect. It only shows on a book that holds a short and loses it.

    The flag decides whether the haircut is charged on the absolute value of a
    position or on its signed value, so it can only be observed when a SHORT position
    takes one. A fixture whose positions all survive would pass whatever the flag
    said, which is exactly how the defect survived until now.
    """
    subject = cached_world()
    perp = leg("AAA", PERP)
    delisted = replace(
        subject,
        series_end=_delisting_oracle(perp.key),
    )
    on_either_side = ledger_of(built=delisted)
    monkeypatch.setattr(wiring, "HAIRCUT_ON_EITHER_SIDE", False)
    one_side_only = ledger_of(built=delisted)
    assert on_either_side.costs.delisting.amount != Decimal(0), (
        "the fixture must actually delist something, or the flag cannot be observed"
    )
    assert on_either_side.costs.delisting.amount != one_side_only.costs.delisting.amount


def _delisting_oracle(key: InstrumentKey) -> object:
    """A series-end oracle that reports one instrument as delisted and the rest listed."""
    from sextant.engine.execution.markout import SeriesEnd

    class OneDelisting:
        """Delisted for one key, still listed for every other."""

        def series_end_at(self, subject: InstrumentKey, at: Timestamp) -> SeriesEnd:
            del at
            return SeriesEnd.DELISTED if subject == key else SeriesEnd.STILL_LISTED

    return OneDelisting()


# ---------------------------------------------------------------------------
# The variant: positions, cadence, the sign filter
# ---------------------------------------------------------------------------


def test_the_position_count_is_read() -> None:
    """Two pairs and three are different books, and the weights say so."""
    three = ledger_of(spec=replace(TURNOVER_SPEC, positions=3))
    assert signature(three) != signature(BASELINE)


def test_the_rebalance_cadence_is_read() -> None:
    """Amendment 6's one field, against a universe whose ranking actually rotates.

    A fixture whose top-N never changes cannot see cadence: the monthly variant trades
    once and then holds, which is what the quarterly one does too. So this runs against
    a world whose liquidity ranking swings, so a slower cadence holds a stale selection
    through rotations the monthly one acts on.

    The *difference* is asserted and the *direction* is not, deliberately. Slower is
    not always less traded: a variant that skips two months also lets its weights
    drift twice as far, and the correction it eventually makes can be larger than the
    two it skipped. On the real dataset the quarterly variant does turn over less than
    its monthly twin, but that is a result rather than a theorem, and a test that
    assumed it would be asserting the finding instead of the wiring.
    """
    rotating = cached_world(rotate_liquidity=True)
    monthly = ledger_of(built=rotating)
    quarterly = ledger_of(spec=replace(TURNOVER_SPEC, rebalance_months=3), built=rotating)
    assert monthly.turnover.amount > Decimal(0), "the fixture must actually rotate"
    assert quarterly.turnover.amount != monthly.turnover.amount
    assert signature(quarterly) != signature(monthly)


def test_the_signal_is_read() -> None:
    """Two signals that rank the same three pairs differently must hold different books.

    The fixture gives each base its own turnover and its own funding rate, in opposite
    orders, so the liquidity ranking and the funding ranking disagree by construction.
    A signal nothing read would produce one book for both.
    """
    funding_ranked = ledger_of(
        spec=VariantSpec(label="probe-funding", signal=CarrySignal.FUNDING_30, positions=2)
    )
    assert funding_ranked.costs.funding.amount != BASELINE.costs.funding.amount, (
        "the two signals selected the same pairs, so this fixture cannot see the signal"
    )


def test_the_positive_filter_is_read() -> None:
    """With every rate positive the filter admits everything; with none, nothing.

    Run against a world whose funding is negative throughout, so the filter has
    something to exclude. A filter nothing read would hold the same book either way.
    """
    negative = cached_world(funding_rate="-0.0005")
    unfiltered = ledger_of(
        spec=VariantSpec(label="probe-all", signal=CarrySignal.FUNDING_30, positions=2),
        built=negative,
    )
    filtered = ledger_of(
        spec=VariantSpec(
            label="probe-positive",
            signal=CarrySignal.FUNDING_30,
            positions=2,
            require_positive=True,
        ),
        built=negative,
    )
    assert filtered.turnover.amount == Decimal(0), "nothing qualifies, so nothing is held"
    assert unfiltered.turnover.amount > Decimal(0)


# ---------------------------------------------------------------------------
# The walk-forward shape
# ---------------------------------------------------------------------------


def test_the_in_sample_month_count_is_read() -> None:
    """Fitting months are never scored, so moving them moves the scored window."""
    longer = ledger_of(in_sample=18)
    assert len(longer.rebalances) != len(BASELINE.rebalances)


def test_the_fold_count_changes_nothing_for_this_family_and_that_is_correct() -> None:
    """A finding, recorded as an assertion rather than left as a surprise.

    ``anchored_plan`` partitions the same out-of-sample span into more or fewer folds,
    and the union of the scored windows is ``total_months - in_sample_months`` either
    way. A fold boundary is a *fitting* boundary, and no F1 variant fits anything:
    all nine are parameter-free, so there is nothing for a fold to refit and the ledger
    comes out identical.

    So ``fold_count`` is a registered value that no F1 result reads. That is not a
    defect and not something to wire away: it would matter the moment a family fits a
    parameter, and F2 onwards may. It is asserted here so the fact is visible in the
    suite, and stated in the results document rather than left for a reader to find.
    """
    assert signature(ledger_of(folds=4)) == signature(BASELINE)
    assert len(ledger_of(folds=4).rebalances) == len(BASELINE.rebalances)


# ---------------------------------------------------------------------------
# The universe thresholds
# ---------------------------------------------------------------------------
#
# Perturbed against the rules directly rather than through ``build_world``, which
# needs the real archive. Each rule is given an instrument that sits between the two
# thresholds, so a rule reading its own threshold must answer differently and a rule
# ignoring it cannot.


def _instrument_listed(days_ago: int, at: Timestamp) -> Instrument:
    """One instrument whose listing is a stated number of days before ``at``.

    Sourced provenance, because the rule answers NOT_EVALUABLE on an unverified
    listing whatever the threshold is, and a fixture that never reaches the
    comparison would pass for both thresholds and prove nothing.
    """
    return replace(
        leg("AAA", PERP),
        listed_at=at.plus(timedelta(days=-days_ago)),
        provenance=Provenance.VENUE_API,
    )


def test_the_listing_age_threshold_is_read() -> None:
    """An instrument 300 days old clears 180 and fails 720."""
    at = ts("2022-01-01T00:00:00")
    subject = _instrument_listed(300, at)
    assert ListingAgeRule(minimum_days=180).evaluate(subject, at) is RuleOutcome.ADMIT
    assert ListingAgeRule(minimum_days=720).evaluate(subject, at) is RuleOutcome.REJECT


def test_the_median_quote_volume_threshold_is_read() -> None:
    """A history at 100,000 a day clears a 250,000 floor only if the floor is lower."""
    at = ts("2022-01-01T00:00:00")
    key = leg("AAA", PERP).key
    history = _history(key, ("100",) * DATA_DAYS)
    histories = {key: history}
    lenient = MedianQuoteVolumeRule(minimum=Notional(Decimal(50_000)), histories=histories)
    strict = MedianQuoteVolumeRule(minimum=Notional(Decimal(5_000_000)), histories=histories)
    subject = leg("AAA", PERP)
    assert lenient.evaluate(subject, at) is RuleOutcome.ADMIT
    assert strict.evaluate(subject, at) is RuleOutcome.REJECT


def test_the_minimum_observation_count_is_read() -> None:
    """A rule that ignored it would admit a median taken over three days."""
    at = ts("2020-10-05T00:00:00")
    key = leg("AAA", PERP).key
    histories = {key: _history(key, ("100",) * DATA_DAYS)}
    subject = leg("AAA", PERP)
    few = MedianQuoteVolumeRule(
        minimum=Notional(Decimal(50_000)), histories=histories, minimum_observations=2
    )
    many = MedianQuoteVolumeRule(
        minimum=Notional(Decimal(50_000)), histories=histories, minimum_observations=200
    )
    assert few.evaluate(subject, at) is RuleOutcome.ADMIT
    assert many.evaluate(subject, at) is RuleOutcome.NOT_EVALUABLE


def test_the_funding_trailing_window_is_read() -> None:
    """A schedule complete for thirty days and holed at ninety must answer differently.

    The hole is placed between the two windows, so the shorter one cannot see it and
    the longer one must. A rule ignoring its window would give one answer for both.
    """
    at = ts("2022-01-01T00:00:00")
    key = leg("AAA", PERP).key
    start = at.plus(timedelta(days=-120))
    # Step 180 sits sixty days before ``at``: inside the ninety-day window and
    # outside the thirty-day one. The series runs past ``at`` so the shorter window
    # is not holed at its own end, which would make both answers REJECT and the
    # threshold invisible.
    rows = tuple(
        Settlement(at=start.plus(timedelta(hours=8 * step)), rate=Decimal("0.0001"))
        for step in range(122 * 3)
        if step != 180
    )
    schedule = RealisedFunding.of({key: rows})
    subject = leg("AAA", PERP)
    assert (
        FundingEvaluabilityRule(schedule=schedule, trailing_days=30).evaluate(subject, at)
        is RuleOutcome.ADMIT
    )
    assert (
        FundingEvaluabilityRule(schedule=schedule, trailing_days=90).evaluate(subject, at)
        is RuleOutcome.REJECT
    )


def test_the_bar_coverage_fraction_is_read() -> None:
    """A history covering 95 per cent of its lookback clears 0.90 and fails 0.99."""
    at = ts("2022-01-01T00:00:00")
    key = leg("AAA", PERP).key
    start = at.plus(timedelta(days=-100))
    kept = tuple(
        DailyObservation(
            open_time=start.plus(timedelta(days=day)),
            close=Price(Decimal(100)),
            quote_volume=Notional(Decimal(100_000)),
        )
        for day in range(100)
        if day % 20
    )
    histories = {key: InstrumentHistory.of(key, kept)}
    subject = leg("AAA", PERP)
    lenient = BarCoverageRule(
        histories=histories, lookback_days=90, minimum_fraction=Decimal("0.90")
    )
    strict = BarCoverageRule(
        histories=histories, lookback_days=90, minimum_fraction=Decimal("0.99")
    )
    assert lenient.evaluate(subject, at) is RuleOutcome.ADMIT
    assert strict.evaluate(subject, at) is RuleOutcome.REJECT


# ---------------------------------------------------------------------------
# What this module does not perturb, said out loud
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "why_not_perturbed"),
    [
        (
            "lookback_days=ENGINE_LOOKBACK_DAYS",
            "observing it needs a fixture whose history is shorter than the lookback, "
            "which this three-pair world is not",
        ),
        (
            "fx=conversion_leg(world)",
            "observing the conversion charge needs a foreign-quoted routing, and this "
            "world quotes everything in the account currency so the leg is inert",
        ),
    ],
)
def test_the_unperturbed_wirings_are_named_rather_than_forgotten(
    keyword: str, why_not_perturbed: str
) -> None:
    """The gap is visible in the suite rather than implied by its absence.

    A keyword check is weak evidence and this module exists because of that. It is
    recorded here anyway for the two values a perturbation cannot reach with this
    fixture, so that a reader counting covered parameters can see which two are not
    and why, instead of assuming the list is complete.
    """
    import inspect

    source = inspect.getsource(wiring.build_engine)
    assert keyword in source, f"{keyword} must be wired; not perturbed because {why_not_perturbed}"

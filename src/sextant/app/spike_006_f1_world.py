"""SEXTANT-006 F1: the world every carry run shares.

Two archives, two venues, one book. The spot legs come from the archive
SEXTANT-005 built; the perpetual legs, their funding settlements and their premium
index come from the futures archive this task acquired. Loaded once, resolved once,
and reused by every variant and every null, because none of it can differ between
them.

What is resolved here, and in this order
-----------------------------------------

1. **the two instrument sets.** Spot from one root, perpetuals from the other,
   each keyed by its own venue and dated by its own sourced listing calendar;
2. **the window**, from the rule in section 7 rather than from a pair of dates.
   The rule reads listing metadata and FX availability only, and no return is
   consulted to resolve it;
3. **the carry universe at every rebalance**, as the intersection of two
   independently evaluated policies: the perpetual leg's and the spot leg's. Both
   report per-rule tallies, so what excluded an asset is a number rather than an
   impression;
4. **the funding schedule and the premium index**, read straight from the
   published settlements;
5. **the regimes**, cut on the same reference series and the same cascade
   SEXTANT-005 used.

Why the universe is two policies intersected rather than one
------------------------------------------------------------

Section 6's rules do not all apply to the same leg. Listing age and turnover are
registered against the *perpetual*; both legs need sourced membership and bar
coverage; funding evaluability exists only on the perpetual. Running one policy
over a merged candidate set would have to decide, silently, which leg each rule
meant. Two policies say it out loud, and the pairing rule then has nothing left to
do but intersect on base asset - which is also where the count of assets lost to
the exact-base-match requirement comes from.

Nothing here fits anything. There is no parameter in this module a result could
move.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.binance.archive import Month
from sextant.adapters.exchanges.binance.capabilities import VENUE as SPOT_VENUE
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.bars import ParquetBarStore
from sextant.adapters.storage.derivatives import FundingStore, PremiumStore
from sextant.adapters.storage.panel import PanelRow, load_daily_panel
from sextant.adapters.storage.repository import ParquetBarRepository, VenueRoutedBarRepository
from sextant.app.binance_archive import (
    CALENDAR_NAME as SPOT_CALENDAR_NAME,
)
from sextant.app.binance_archive import (
    INDEX_NAME as SPOT_INDEX_NAME,
)
from sextant.app.binance_archive import (
    PLAN_NAME as SPOT_PLAN_NAME,
)
from sextant.app.binance_archive import (
    STORE_ROOT as SPOT_ROOT,
)
from sextant.app.binance_archive import (
    AcquisitionPlan,
    MonthIndex,
    split_symbol,
)
from sextant.app.futures_archive import (
    CALENDAR_NAME as PERP_CALENDAR_NAME,
)
from sextant.app.futures_archive import (
    INDEX_NAME as PERP_INDEX_NAME,
)
from sextant.app.futures_archive import (
    PERP_VENUE,
    FuturesIndex,
)
from sextant.app.futures_archive import (
    STORE_ROOT as PERP_ROOT,
)
from sextant.app.futures_archive import (
    TREES as PERP_TREES,
)
from sextant.app.panels import fx_rates_from, reference_closes_from
from sextant.app.spike import EXCLUDED_BASES
from sextant.app.spike_006_f1 import (
    ACCOUNT_CURRENCY,
    FOREIGN_CURRENCY,
    FUNDING_TRAILING_DAYS,
    FX_FIRST_MONTH_LOOKBACK_DAYS,
    FX_SYMBOL,
    LONGEST_LOOKBACK_DAYS,
    MIN_BAR_COVERAGE_FRACTION,
    MIN_LISTING_AGE_DAYS,
    MIN_MEDIAN_QUOTE_VOLUME,
    MINIMUM_MONTHS_TO_PROCEED,
    QUOTE_ASSET,
    REGIME_SYMBOL,
    TURNOVER_MIN_OBSERVATIONS,
)
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.listing import MembershipState
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.execution.funding import RealisedFunding, Settlement
from sextant.engine.execution.fx import CurrencyRouting, FxRates
from sextant.engine.execution.markout import SeriesEnd
from sextant.engine.regime.segmentation import RegimeInputs, segment
from sextant.engine.strategies.carry import PremiumIndex, pairs_from
from sextant.engine.universe.policy import UniversePolicy
from sextant.engine.universe.rules import (
    BarCoverageRule,
    ExcludedAssetClassRule,
    FundingEvaluabilityRule,
    ListingAgeRule,
    MedianQuoteVolumeRule,
    QuoteCurrencyRule,
    SourcedMembershipRule,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory

#: Perpetuals whose contract is a scaled unit of the underlying: 1000PEPEUSDT is
#: a thousand PEPE per contract. Pairing one with its spot counterpart needs a
#: unit-conversion rule, and a rule invented after the data was seen is a degree
#: of freedom. Excluded by section 6 rule 1, and the count is reported.
SCALED_UNIT_PREFIXES = ("1000", "10000", "1000000")

#: Section 6's leveraged-token stem rule, carried from SEXTANT-005 unchanged.
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class WorldIncomplete(RuntimeError):
    """The dataset cannot support the registered specification. Nothing may run."""


@dataclass(frozen=True, slots=True)
class CarryUniverse:
    """The carry universe at every rebalance, as both legs of every admitted pair.

    The engine resolves candidates from this and hands them to the strategy, which
    re-derives the pairing by exact base match. Emitting both legs rather than a
    pair object keeps the engine's universe port unchanged: it still answers with
    instrument keys and knows nothing about carry.
    """

    label: str
    members: Mapping[Timestamp, tuple[InstrumentKey, ...]]
    bases: Mapping[Timestamp, tuple[str, ...]]

    @property
    def policy_name(self) -> str:
        """Recorded in the run manifest."""
        return self.label

    def executable_at(self, at: Timestamp) -> tuple[InstrumentKey, ...]:
        """Both legs of every admitted pair at ``at``, sorted."""
        return self.members.get(at, ())

    @property
    def sizes(self) -> tuple[int, ...]:
        """How many pairs stood at each rebalance, in instant order."""
        return tuple(len(self.bases[at]) for at in sorted(self.bases))


@dataclass(frozen=True, slots=True)
class Census:
    """Why assets were not in the carry universe, counted rather than described.

    Per-rule tallies are independent: an asset failing three rules appears in
    three counts. That is the same choice :class:`UniverseEvaluation` makes and for
    the same reason - the question worth answering is which threshold binds, not
    which rule happened to run first.
    """

    perpetual_symbols_seen: int
    scaled_unit_symbols: tuple[str, ...]
    perpetuals_without_premium: tuple[str, ...]
    perpetuals_without_bars: tuple[str, ...]
    perpetual_rejections: Mapping[str, int]
    perpetual_not_evaluable: Mapping[str, int]
    spot_rejections: Mapping[str, int]
    spot_not_evaluable: Mapping[str, int]
    bases_without_spot_leg: int
    bases_without_perpetual_leg: int
    undetermined_membership: int

    def as_json(self) -> dict[str, object]:
        """Every count, for the results file and the report."""
        return {
            "perpetual_symbols_seen": self.perpetual_symbols_seen,
            "scaled_unit_symbols_excluded": list(self.scaled_unit_symbols),
            "perpetuals_without_premium_series": list(self.perpetuals_without_premium),
            "perpetuals_without_bar_series": list(self.perpetuals_without_bars),
            "perpetual_rejections_by_rule": dict(sorted(self.perpetual_rejections.items())),
            "perpetual_not_evaluable_by_rule": dict(sorted(self.perpetual_not_evaluable.items())),
            "spot_rejections_by_rule": dict(sorted(self.spot_rejections.items())),
            "spot_not_evaluable_by_rule": dict(sorted(self.spot_not_evaluable.items())),
            "bases_admitted_on_the_perpetual_leg_with_no_spot_leg": self.bases_without_spot_leg,
            "bases_admitted_on_the_spot_leg_with_no_perpetual_leg": (
                self.bases_without_perpetual_leg
            ),
            "undetermined_membership_observations": self.undetermined_membership,
        }


@dataclass(frozen=True, slots=True)
class ResolvedWindow:
    """What the section 7 rule resolved to, and what it consulted to do it."""

    first_month: Month
    last_month_end: Month
    fx_first_day: Timestamp
    perpetual_last_month: Month
    spot_last_month: Month
    usable_months: int

    def as_json(self) -> dict[str, object]:
        return {
            "first_usable_month": self.first_month.label,
            "last_usable_month_end": self.last_month_end.label,
            "usable_months": self.usable_months,
            "fx_first_day": self.fx_first_day.isoformat(),
            "perpetual_archive_last_month": self.perpetual_last_month.label,
            "spot_archive_last_month": self.spot_last_month.label,
            "resolved_by": (
                "the rule in pre-registration section 7, from listing metadata and FX "
                "availability only. No return was consulted."
            ),
        }


@dataclass(frozen=True, slots=True)
class CalendarSeriesEnd:
    """Why a series stopped, answered by a sourced listing calendar.

    One per venue. The haircut applies to a delisting and to nothing else: a
    series that stops because the archive stops is not a delisting, and an instant
    inside a membership bracket is undetermined. Neither takes the haircut.
    """

    by_venue: Mapping[Venue, BinanceListingCalendar]

    def series_end_at(self, key: InstrumentKey, at: Timestamp) -> SeriesEnd:
        """Whether ``key`` had stopped trading by ``at``, allowing for ignorance."""
        calendar = self.by_venue.get(key.venue)
        if calendar is None:
            return SeriesEnd.UNDETERMINED
        state = calendar.membership_at(key.symbol, at)
        if state is MembershipState.LISTED:
            return SeriesEnd.STILL_LISTED
        if state is MembershipState.NOT_LISTED:
            return SeriesEnd.DELISTED
        return SeriesEnd.UNDETERMINED


@dataclass(frozen=True, slots=True)
class World:
    """Everything the engine needs that is the same across every carry run."""

    instruments: Mapping[InstrumentKey, Instrument]
    perpetual_histories: Mapping[InstrumentKey, InstrumentHistory]
    spot_histories: Mapping[InstrumentKey, InstrumentHistory]
    repository: VenueRoutedBarRepository
    spot_calendar: BinanceListingCalendar
    perpetual_calendar: BinanceListingCalendar
    series_end: CalendarSeriesEnd
    funding: RealisedFunding
    premium: PremiumIndex
    fx_rates: FxRates
    routing: CurrencyRouting
    universe: CarryUniverse
    window: ResolvedWindow
    instants: tuple[Timestamp, ...]
    regimes: tuple[RegimeInputs, ...]
    eur_closes: Mapping[Timestamp, Decimal]
    census: Census

    @property
    def histories(self) -> Mapping[InstrumentKey, InstrumentHistory]:
        """Both legs' histories, for the cost model's liquidity bands.

        The band is cut on the instrument being traded, so both legs need one. A
        missing history bands as UNKNOWN, which is the most expensive band, so an
        absent history costs more rather than less.
        """
        return {**self.spot_histories, **self.perpetual_histories}


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


def build_world(*, spot_root: Path = SPOT_ROOT, perp_root: Path = PERP_ROOT) -> World:
    """Load both archives once and resolve everything a run reuses."""
    spot_calendar = BinanceListingCalendar.read_json(spot_root / SPOT_CALENDAR_NAME)
    perp_calendar = BinanceListingCalendar.read_json(perp_root / PERP_CALENDAR_NAME)
    spot_index = MonthIndex.read_json(spot_root / SPOT_INDEX_NAME)
    spot_plan = AcquisitionPlan.read_json(spot_root / SPOT_PLAN_NAME, spot_index)
    perp_index = FuturesIndex.read_json(perp_root / PERP_INDEX_NAME)

    print("[f1] loading the spot panel")
    spot_panel = load_daily_panel(spot_root, SPOT_VENUE, Timeframe.D1)
    print(f"[f1] {len(spot_panel)} spot series")
    print("[f1] loading the perpetual panel")
    perp_panel = load_daily_panel(perp_root, PERP_VENUE, Timeframe.D1)
    print(f"[f1] {len(perp_panel)} perpetual series")

    spot_instruments, spot_histories = _instruments_from(
        spot_panel, spot_calendar, venue=SPOT_VENUE
    )
    perp_all, perp_histories_all = _instruments_from(perp_panel, perp_calendar, venue=PERP_VENUE)
    scaled = tuple(sorted(item.symbol for item in perp_all.values() if is_scaled_unit(item.symbol)))
    perp_instruments = {
        key: item for key, item in perp_all.items() if not is_scaled_unit(item.symbol)
    }
    perp_histories = {
        key: value for key, value in perp_histories_all.items() if key in perp_instruments
    }
    print(
        f"[f1] {len(spot_instruments)} spot and {len(perp_instruments)} perpetual candidates "
        f"with a priced daily series; {len(scaled)} scaled-unit perpetuals excluded"
    )

    funding = _funding_from(perp_root, perp_instruments)
    premium, without_premium = _premium_from(perp_root, perp_instruments)
    fx_rates = fx_rates_from(
        spot_panel,
        symbol=FX_SYMBOL,
        foreign_currency=FOREIGN_CURRENCY,
        account_currency=ACCOUNT_CURRENCY,
    )
    window = _resolve_window(
        spot_panel=spot_panel,
        spot_plan=spot_plan,
        perp_index=perp_index,
    )
    print(
        f"[f1] window {window.first_month.label} to {window.last_month_end.label}: "
        f"{window.usable_months} usable months"
    )
    if window.usable_months < MINIMUM_MONTHS_TO_PROCEED:
        raise WorldIncomplete(
            f"The resolved window carries {window.usable_months} usable months against the "
            f"registered floor of {MINIMUM_MONTHS_TO_PROCEED}. Section 7 fixes verdict (C) on "
            "sample size alone in this case, and no variant is run."
        )

    instants = monthly_instants(window.first_month, window.last_month_end)
    universe, census = _carry_universe(
        instants=instants,
        spot_instruments=spot_instruments,
        perp_instruments=perp_instruments,
        spot_histories=spot_histories,
        perp_histories=perp_histories,
        spot_calendar=spot_calendar,
        perp_calendar=perp_calendar,
        funding=funding,
        scaled=scaled,
        without_premium=without_premium,
        without_bars=_perpetuals_without_bars(perp_root, perp_panel),
    )
    sizes = universe.sizes
    print(
        f"[f1] carry universe: {min(sizes)} to {max(sizes)} pairs across {len(instants)} "
        f"rebalances (median {sorted(sizes)[len(sizes) // 2]})"
    )
    if min(sizes) == 0:
        empty = [at.isoformat()[:10] for at in sorted(universe.bases) if not universe.bases[at]]
        raise WorldIncomplete(
            "Section 7 requires the carry universe to be non-empty at every rebalance. It is "
            f"empty at {len(empty)} of them, first {empty[0]}. The window rule has not been "
            "satisfied and nothing may run against this resolution."
        )

    eur_closes = reference_closes_from(spot_panel, fx_rates, symbol=REGIME_SYMBOL)
    regimes = segment(eur_closes, instants)

    instruments: dict[InstrumentKey, Instrument] = {**spot_instruments, **perp_instruments}
    return World(
        instruments=instruments,
        perpetual_histories=perp_histories,
        spot_histories=spot_histories,
        repository=VenueRoutedBarRepository(
            by_venue={
                SPOT_VENUE: ParquetBarRepository(store=ParquetBarStore(spot_root)),
                PERP_VENUE: ParquetBarRepository(store=ParquetBarStore(perp_root)),
            }
        ),
        spot_calendar=spot_calendar,
        perpetual_calendar=perp_calendar,
        series_end=CalendarSeriesEnd(
            by_venue={SPOT_VENUE: spot_calendar, PERP_VENUE: perp_calendar}
        ),
        funding=funding,
        premium=premium,
        fx_rates=fx_rates,
        routing=CurrencyRouting(
            account_currency=ACCOUNT_CURRENCY,
            quote_by_symbol={key.symbol: item.quote for key, item in instruments.items()},
        ),
        universe=universe,
        window=window,
        instants=instants,
        regimes=regimes,
        eur_closes=eur_closes,
        census=census,
    )


def is_scaled_unit(symbol: str) -> bool:
    """Whether the contract is a scaled multiple of the underlying.

    ``1000PEPEUSDT`` is a thousand PEPE per contract. The stem must itself look
    like a base asset, so a genuine asset whose ticker begins with those digits is
    not silently deleted - the same defensive shape the leveraged-token stem rule
    uses.
    """
    for prefix in SCALED_UNIT_PREFIXES:
        if symbol.startswith(prefix) and len(symbol) > len(prefix) + len(QUOTE_ASSET):
            return True
    return False


def has_leveraged_stem(base: str) -> bool:
    """Whether the base is a leveraged token by the registered stem rule."""
    return any(base.endswith(suffix) and len(base) > len(suffix) for suffix in LEVERAGED_SUFFIXES)


def _instruments_from(
    panel: Mapping[str, tuple[PanelRow, ...]],
    calendar: BinanceListingCalendar,
    *,
    venue: Venue,
) -> tuple[dict[InstrumentKey, Instrument], dict[InstrumentKey, InstrumentHistory]]:
    """Instruments and daily histories for one venue's panel.

    Tick, lot and minimum notional are zero for every instrument, which makes the
    two feasibility rules inert. No historical instrument-constraint record exists
    for a delisted contract on this venue, so values read from today's endpoint
    would describe survivors and nothing else. Zero for everything is
    non-selective; the alternative is selectively strict on the names that
    happened to survive.
    """
    instruments: dict[InstrumentKey, Instrument] = {}
    histories: dict[InstrumentKey, InstrumentHistory] = {}
    for symbol in sorted(panel):
        entry = calendar.entry_for(symbol)
        parts = split_symbol(symbol)
        if entry is None or parts is None:
            continue
        listed_by = entry.spells[0].certainly_listed_by
        if listed_by is None:
            continue
        base, quote = parts
        key = InstrumentKey(venue, symbol)
        instruments[key] = Instrument(
            venue=venue,
            symbol=symbol,
            base=base,
            quote=quote,
            listed_at=listed_by,
            tick_size=Price(Decimal(0)),
            lot_size=Quantity(Decimal(0)),
            min_notional=Notional(Decimal(0)),
            delisted_at=None,
            provenance=entry.provenance,
        )
        histories[key] = InstrumentHistory.of(
            key,
            (
                DailyObservation(
                    open_time=Timestamp.from_epoch_millis(row.open_time_ms),
                    close=Price(Decimal(row.close)),
                    quote_volume=Notional(Decimal(row.close) * Decimal(row.volume)),
                )
                for row in panel[symbol]
                if Decimal(row.close) > 0
            ),
        )
    return instruments, histories


def _funding_from(
    perp_root: Path, instruments: Mapping[InstrumentKey, Instrument]
) -> RealisedFunding:
    """Published settlements for every perpetual, indexed for lookup by span."""
    store = FundingStore(perp_root)
    by_instrument: dict[InstrumentKey, tuple[Settlement, ...]] = {}
    for key in sorted(instruments):
        # `has_series` first, deliberately: the store distinguishes an absent
        # series from an empty one and raises on the former, which is invariant 10
        # holding at the storage boundary. Catching that exception here would
        # collapse the distinction the store went to the trouble of keeping.
        if not store.has_series(key.venue, key.symbol):
            continue
        rows = store.read_series(key.venue, key.symbol)
        if not rows:
            continue
        by_instrument[key] = tuple(
            Settlement(
                at=row.settled_at,
                rate=row.as_decimal,
                interval_hours=row.interval_hours,
            )
            for row in rows
        )
    settlements = sum(len(value) for value in by_instrument.values())
    print(f"[f1] funding: {len(by_instrument)} series, {settlements} settlements")
    return RealisedFunding(by_instrument=by_instrument)


def _premium_from(
    perp_root: Path, instruments: Mapping[InstrumentKey, Instrument]
) -> tuple[PremiumIndex, tuple[str, ...]]:
    """The premium-index closes, and which perpetuals publish none.

    A perpetual with no premium series is unrankable by the premium variant and is
    counted as not-evaluable there. It is not excluded from the universe: the other
    seven variants do not read the premium index, and dropping an asset from every
    variant because one of them cannot score it would make the universe depend on
    which variants exist.
    """
    store = PremiumStore(perp_root)
    closes: dict[InstrumentKey, tuple[tuple[Timestamp, Decimal], ...]] = {}
    missing: list[str] = []
    for key in sorted(instruments):
        if not store.has_series(key.venue, key.symbol):
            missing.append(key.symbol)
            continue
        rows = store.read_series(key.venue, key.symbol)
        if not rows:
            missing.append(key.symbol)
            continue
        closes[key] = tuple(
            (row.open_time.plus(Timeframe.D1.duration), row.as_decimal) for row in rows
        )
    print(f"[f1] premium index: {len(closes)} series, {len(missing)} perpetuals publish none")
    return PremiumIndex(by_instrument=closes), tuple(sorted(missing))


def _perpetuals_without_bars(perp_root: Path, panel: Mapping[str, object]) -> tuple[str, ...]:
    """Perpetuals with a funding series and no kline series.

    The listing calendar is built from kline presence, deliberately, so one of
    these has no calendar entry and cannot enter the universe. Counted because
    silently dropping a contract the venue published cash flows for is exactly the
    kind of quiet exclusion invariant 9 exists to make visible.
    """
    funding_symbols = FundingStore(perp_root).symbols(PERP_VENUE)
    return tuple(sorted(funding_symbols - set(panel)))


def _resolve_window(
    *,
    spot_panel: Mapping[str, tuple[PanelRow, ...]],
    spot_plan: AcquisitionPlan,
    perp_index: FuturesIndex,
) -> ResolvedWindow:
    """Apply section 7's rule. Listing metadata and FX availability only.

    The first usable month is the earliest month start at least
    ``FX_FIRST_MONTH_LOOKBACK_DAYS`` after the first day the archive holds an FX
    bar, so the 180-day listing age, the 90-day longest lookback and the 30-day
    turnover window are all satisfiable with a ragged-edge month to spare.

    The last usable month end is the start of the last month for which *both*
    archives hold a complete object set, which requires the month after it to be
    present too: a symbol absent from the final published month cannot be
    distinguished from one whose final object has not been published yet.
    """
    fx_rows = spot_panel.get(FX_SYMBOL)
    if not fx_rows:
        raise WorldIncomplete(f"No {FX_SYMBOL} series in the spot store; EUR cannot be priced.")
    fx_first_day = Timestamp.from_epoch_millis(fx_rows[0].open_time_ms)
    earliest = fx_first_day.plus(timedelta(days=FX_FIRST_MONTH_LOOKBACK_DAYS))
    first_month = month_at_or_after(earliest)

    perp_last = max(
        item.month
        for tree in PERP_TREES
        for objects in perp_index.for_tree(tree).values()
        for item in objects
    )
    spot_last = spot_plan.archive_last_month
    both_last = min(perp_last, spot_last)
    return ResolvedWindow(
        first_month=first_month,
        last_month_end=both_last,
        fx_first_day=fx_first_day,
        perpetual_last_month=perp_last,
        spot_last_month=spot_last,
        usable_months=months_in_window(first_month, both_last),
    )


def month_at_or_after(instant: Timestamp) -> Month:
    """The first calendar month whose start is at or after ``instant``."""
    moment = instant.value
    if moment.day == 1 and (moment.hour, moment.minute, moment.second) == (0, 0, 0):
        return Month(year=moment.year, month=moment.month)
    year, month = moment.year, moment.month + 1
    if month > 12:
        year, month = year + 1, 1
    return Month(year=year, month=month)


def months_in_window(first: Month, last_end: Month) -> int:
    """How many whole months the window ``[first, last_end)`` spans."""
    return (last_end.year - first.year) * 12 + (last_end.month - first.month)


def monthly_instants(first: Month, last_end: Month) -> tuple[Timestamp, ...]:
    """Every rebalance instant from ``first``, plus the window's own end.

    The final instant is the window's end rather than a rebalance: it is where the
    last fold liquidates. Present in the sequence so that the closing book is
    priced and charged at a real instant rather than left open at the edge.
    """
    instants: list[Timestamp] = []
    year, month = first.year, first.month
    while (year, month) <= (last_end.year, last_end.month):
        instants.append(Month(year=year, month=month).starts_at)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return tuple(instants)


def carry_policies(
    *,
    spot_histories: Mapping[InstrumentKey, InstrumentHistory],
    perp_histories: Mapping[InstrumentKey, InstrumentHistory],
    spot_calendar: BinanceListingCalendar,
    perp_calendar: BinanceListingCalendar,
    funding: RealisedFunding,
) -> tuple[UniversePolicy, UniversePolicy]:
    """The registered rule cascade for each leg, in the registered order.

    Extracted so the same rule list serves the rebalance instants and any other
    instant a rule-fixed sample has to be selected at. Two copies of a cascade is two
    cascades: the one that ran and the one a sample was chosen by.
    """
    perpetual_policy = UniversePolicy.of(
        "carry/perpetual",
        (
            QuoteCurrencyRule(allowed=frozenset({QUOTE_ASSET})),
            SourcedMembershipRule(oracle=perp_calendar),
            ListingAgeRule(minimum_days=MIN_LISTING_AGE_DAYS),
            FundingEvaluabilityRule(schedule=funding, trailing_days=FUNDING_TRAILING_DAYS),
            MedianQuoteVolumeRule(
                minimum=MIN_MEDIAN_QUOTE_VOLUME,
                histories=perp_histories,
                lookback_days=FUNDING_TRAILING_DAYS,
                minimum_observations=TURNOVER_MIN_OBSERVATIONS,
            ),
            BarCoverageRule(
                histories=perp_histories,
                lookback_days=LONGEST_LOOKBACK_DAYS,
                minimum_fraction=MIN_BAR_COVERAGE_FRACTION,
            ),
            ExcludedAssetClassRule(excluded_bases=frozenset(EXCLUDED_BASES)),
        ),
    )
    spot_policy = UniversePolicy.of(
        "carry/spot",
        (
            QuoteCurrencyRule(allowed=frozenset({QUOTE_ASSET})),
            SourcedMembershipRule(oracle=spot_calendar),
            BarCoverageRule(
                histories=spot_histories,
                lookback_days=LONGEST_LOOKBACK_DAYS,
                minimum_fraction=MIN_BAR_COVERAGE_FRACTION,
            ),
            ExcludedAssetClassRule(excluded_bases=frozenset(EXCLUDED_BASES)),
        ),
    )
    return perpetual_policy, spot_policy


def carry_bases_at(world: World, at: Timestamp) -> tuple[str, ...]:
    """The carry universe's base assets at an instant that need not be a rebalance.

    The universe the grid ran on is keyed by rebalance instant. A sample whose rule
    names a different date - the depth sample's 2022-12-31, for one - has to be
    selected at that date and not at the nearest rebalance, or the sample is not the
    sample that was registered. Same cascade, same order, one instant.
    """
    perpetual_policy, spot_policy = carry_policies(
        spot_histories=world.spot_histories,
        perp_histories=world.perpetual_histories,
        spot_calendar=world.spot_calendar,
        perp_calendar=world.perpetual_calendar,
        funding=world.funding,
    )
    spot_candidates = tuple(
        item
        for item in world.instruments.values()
        if item.key in world.spot_histories and not has_leveraged_stem(item.base)
    )
    perp_candidates = tuple(
        item
        for item in world.instruments.values()
        if item.key in world.perpetual_histories and not has_leveraged_stem(item.base)
    )
    perp_bases = {
        world.instruments[key].base
        for key in perpetual_policy.evaluate(perp_candidates, at).members
    }
    spot_bases = {
        world.instruments[key].base for key in spot_policy.evaluate(spot_candidates, at).members
    }
    return tuple(sorted(perp_bases & spot_bases))


def _carry_universe(
    *,
    instants: Sequence[Timestamp],
    spot_instruments: Mapping[InstrumentKey, Instrument],
    perp_instruments: Mapping[InstrumentKey, Instrument],
    spot_histories: Mapping[InstrumentKey, InstrumentHistory],
    perp_histories: Mapping[InstrumentKey, InstrumentHistory],
    spot_calendar: BinanceListingCalendar,
    perp_calendar: BinanceListingCalendar,
    funding: RealisedFunding,
    scaled: tuple[str, ...],
    without_premium: tuple[str, ...],
    without_bars: tuple[str, ...],
) -> tuple[CarryUniverse, Census]:
    """Two policies, evaluated independently, intersected on base asset."""
    perpetual_policy, spot_policy = carry_policies(
        spot_histories=spot_histories,
        perp_histories=perp_histories,
        spot_calendar=spot_calendar,
        perp_calendar=perp_calendar,
        funding=funding,
    )
    perp_candidates = tuple(
        item for item in perp_instruments.values() if not has_leveraged_stem(item.base)
    )
    spot_candidates = tuple(
        item for item in spot_instruments.values() if not has_leveraged_stem(item.base)
    )
    perp_by_base = {item.base: item for item in perp_candidates}
    spot_by_base = {item.base: item for item in spot_candidates}

    members: dict[Timestamp, tuple[InstrumentKey, ...]] = {}
    bases: dict[Timestamp, tuple[str, ...]] = {}
    perp_rejections: dict[str, int] = {}
    perp_unknown: dict[str, int] = {}
    spot_rejections: dict[str, int] = {}
    spot_unknown: dict[str, int] = {}
    no_spot = 0
    no_perp = 0
    undetermined = 0

    for at in instants:
        perp_evaluation = perpetual_policy.evaluate(perp_candidates, at)
        spot_evaluation = spot_policy.evaluate(spot_candidates, at)
        _accumulate(perp_rejections, perp_evaluation.rejected_by)
        _accumulate(perp_unknown, perp_evaluation.not_evaluable_by)
        _accumulate(spot_rejections, spot_evaluation.rejected_by)
        _accumulate(spot_unknown, spot_evaluation.not_evaluable_by)
        perp_bases = {perp_instruments[key].base for key in perp_evaluation.members}
        spot_bases = {spot_instruments[key].base for key in spot_evaluation.members}
        paired = sorted(perp_bases & spot_bases)
        no_spot += len(perp_bases - spot_bases)
        no_perp += len(spot_bases - perp_bases)
        undetermined += len(perp_calendar.undetermined_at(at))
        keys: list[InstrumentKey] = []
        for base in paired:
            keys.append(spot_by_base[base].key)
            keys.append(perp_by_base[base].key)
        members[at] = tuple(sorted(keys))
        bases[at] = tuple(paired)

    census = Census(
        perpetual_symbols_seen=len(perp_instruments) + len(scaled),
        scaled_unit_symbols=scaled,
        perpetuals_without_premium=without_premium,
        perpetuals_without_bars=without_bars,
        perpetual_rejections=perp_rejections,
        perpetual_not_evaluable=perp_unknown,
        spot_rejections=spot_rejections,
        spot_not_evaluable=spot_unknown,
        bases_without_spot_leg=no_spot,
        bases_without_perpetual_leg=no_perp,
        undetermined_membership=undetermined,
    )
    return CarryUniverse(label="carry/USDT", members=members, bases=bases), census


def _accumulate(into: dict[str, int], counts: Mapping[str, tuple[InstrumentKey, ...]]) -> None:
    """Add one instant's per-rule tallies into the running census."""
    for rule, keys in counts.items():
        into[rule] = into.get(rule, 0) + len(keys)


def pair_count_at(world: World, at: Timestamp) -> int:
    """How many carry pairs the universe held at one rebalance."""
    return len(world.universe.bases.get(at, ()))


def carry_pairs_at(world: World, at: Timestamp) -> int:
    """How many pairs the strategy's own pairing rule finds. A cross-check.

    The universe intersects on base asset and the strategy re-derives the pairing
    from the candidate set. The two must agree; if they ever do not, one of them is
    wrong and the run should not be trusted.
    """
    candidates = [world.instruments[key] for key in world.universe.executable_at(at)]
    return len(pairs_from(candidates, perpetual_venue=PERP_VENUE.name))


def instants_in(instants: Iterable[Timestamp]) -> tuple[str, ...]:
    """Rebalance instants as dates, for a manifest."""
    return tuple(at.isoformat()[:10] for at in instants)


__all__ = [
    "LEVERAGED_SUFFIXES",
    "PERP_ROOT",
    "PERP_VENUE",
    "SCALED_UNIT_PREFIXES",
    "SPOT_ROOT",
    "SPOT_VENUE",
    "CalendarSeriesEnd",
    "CarryUniverse",
    "Census",
    "ResolvedWindow",
    "World",
    "WorldIncomplete",
    "build_world",
    "carry_pairs_at",
    "has_leveraged_stem",
    "is_scaled_unit",
    "month_at_or_after",
    "monthly_instants",
    "months_in_window",
    "pair_count_at",
]

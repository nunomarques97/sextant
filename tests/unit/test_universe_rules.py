"""Point-in-time universe construction.

These tests pin three things that are easy to get subtly wrong and impossible
to notice afterwards: that a rule cannot see the future, that "I do not know" is
kept separate from "no", and that the research and executable universes really
are two different objects rather than one with a different label.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.universe.policy import UniversePolicy, executable_from, power_band
from sextant.engine.universe.rules import (
    AccountParameters,
    ExcludedAssetClassRule,
    ListingAgeRule,
    LotSizeFeasibilityRule,
    MedianQuoteVolumeRule,
    MedianSpreadRule,
    MinNotionalFeasibilityRule,
    QuoteCurrencyRule,
    RuleOutcome,
)
from sextant.engine.universe.statistics import DailyObservation, InstrumentHistory

VENUE = Venue("testvenue")
DECISION = Timestamp(datetime(2024, 6, 1, tzinfo=UTC))


def account() -> AccountParameters:
    """PO decision D2, exactly."""
    return AccountParameters(equity_quote=Notional(Decimal(1500)), max_positions=8)


def instrument(
    symbol: str,
    *,
    base: str = "AAA",
    quote: str = "EUR",
    listed_at: Timestamp | None = None,
    min_notional: str = "10",
    lot_size: str = "0.0001",
    provenance: Provenance = Provenance.RECONSTRUCTED,
) -> Instrument:
    """A candidate instrument with explicit constraints and a sourced window."""
    return Instrument(
        venue=VENUE,
        symbol=symbol,
        base=base,
        quote=quote,
        listed_at=listed_at or Timestamp(datetime(2020, 1, 1, tzinfo=UTC)),
        tick_size=Price(Decimal("0.01")),
        lot_size=Quantity(Decimal(lot_size)),
        min_notional=Notional(Decimal(min_notional)),
        provenance=provenance,
    )


def history(
    symbol: str,
    *,
    days: int,
    quote_volume: str,
    close: str = "100",
    ending: Timestamp = DECISION,
) -> InstrumentHistory:
    """A daily history of ``days`` bars ending the day before ``ending``."""
    key = InstrumentKey(VENUE, symbol)
    observations = [
        DailyObservation(
            open_time=Timestamp(ending.value - timedelta(days=offset + 1)),
            close=Price(Decimal(close)),
            quote_volume=Notional(Decimal(quote_volume)),
        )
        for offset in range(days)
    ]
    return InstrumentHistory.of(key, observations)


# ---------------------------------------------------------------------------
# Account arithmetic
# ---------------------------------------------------------------------------


def test_the_account_thresholds_follow_from_the_decided_parameters() -> None:
    parameters = account()

    assert parameters.target_position == Notional(Decimal("187.5"))
    assert parameters.max_min_notional == Notional(Decimal("46.875"))
    assert parameters.max_rounding_loss == Notional(Decimal("1.875"))


# ---------------------------------------------------------------------------
# Look-ahead
# ---------------------------------------------------------------------------


def test_a_median_volume_never_reads_a_bar_that_had_not_closed() -> None:
    """The rule must not see the future, even when the future is in the file."""
    key = InstrumentKey(VENUE, "AAAEUR")
    past = [
        DailyObservation(
            open_time=Timestamp(DECISION.value - timedelta(days=offset + 1)),
            close=Price(Decimal(100)),
            quote_volume=Notional(Decimal(1_000)),
        )
        for offset in range(30)
    ]
    future = [
        DailyObservation(
            open_time=Timestamp(DECISION.value + timedelta(days=offset)),
            close=Price(Decimal(100)),
            quote_volume=Notional(Decimal(10_000_000)),
        )
        for offset in range(30)
    ]
    with_future = InstrumentHistory.of(key, past + future)

    assert with_future.median_quote_volume(DECISION, 30) == Notional(Decimal(1_000))


def test_the_listing_age_rule_rejects_an_instrument_that_is_too_young() -> None:
    rule = ListingAgeRule(minimum_days=180)
    young = instrument("AAAEUR", listed_at=Timestamp(DECISION.value - timedelta(days=90)))
    seasoned = instrument("BBBEUR", listed_at=Timestamp(DECISION.value - timedelta(days=400)))

    assert rule.evaluate(young, DECISION) is RuleOutcome.REJECT
    assert rule.evaluate(seasoned, DECISION) is RuleOutcome.ADMIT


def test_an_unsourced_listing_date_cannot_support_an_age_judgement() -> None:
    """A truncated history hands back the window edge, not a listing date.

    Ageing every instrument from that edge would give them all the same
    fabricated birthday and admit or reject them together, which looks like a
    universe and is an artefact of the endpoint.
    """
    rule = ListingAgeRule(minimum_days=180)
    unsourced = instrument(
        "AAAEUR",
        listed_at=Timestamp(DECISION.value - timedelta(days=400)),
        provenance=Provenance.UNVERIFIED,
    )

    assert rule.evaluate(unsourced, DECISION) is RuleOutcome.NOT_EVALUABLE
    assert rule.admits(unsourced, DECISION) is False


# ---------------------------------------------------------------------------
# Unknown is not the same as no
# ---------------------------------------------------------------------------


def test_a_missing_history_is_not_evaluable_rather_than_a_rejection() -> None:
    """Blaming liquidity for a missing dataset would make the report lie."""
    rule = MedianQuoteVolumeRule(minimum=Notional(Decimal(250_000)), histories={})

    assert rule.evaluate(instrument("AAAEUR"), DECISION) is RuleOutcome.NOT_EVALUABLE
    assert rule.admits(instrument("AAAEUR"), DECISION) is False


def test_a_thin_history_is_not_evaluable_rather_than_a_rejection() -> None:
    thin = history("AAAEUR", days=5, quote_volume="1000000")
    rule = MedianQuoteVolumeRule(
        minimum=Notional(Decimal(250_000)),
        histories={thin.key: thin},
    )

    assert rule.evaluate(instrument("AAAEUR"), DECISION) is RuleOutcome.NOT_EVALUABLE


def test_a_measured_volume_below_the_floor_is_a_rejection() -> None:
    thin = history("AAAEUR", days=30, quote_volume="1000")
    deep = history("BBBEUR", days=30, quote_volume="5000000")
    rule = MedianQuoteVolumeRule(
        minimum=Notional(Decimal(250_000)),
        histories={thin.key: thin, deep.key: deep},
    )

    assert rule.evaluate(instrument("AAAEUR"), DECISION) is RuleOutcome.REJECT
    assert rule.evaluate(instrument("BBBEUR"), DECISION) is RuleOutcome.ADMIT


def test_the_spread_rule_is_not_evaluable_at_an_instant_with_no_measurement() -> None:
    """Neither venue publishes historical quotes, so this is the normal case."""
    key = InstrumentKey(VENUE, "AAAEUR")
    rule = MedianSpreadRule(maximum_bps=Decimal(25), observed_bps={(key, DECISION): Decimal(10)})
    later = Timestamp(DECISION.value + timedelta(days=31))

    assert rule.evaluate(instrument("AAAEUR"), DECISION) is RuleOutcome.ADMIT
    assert rule.evaluate(instrument("AAAEUR"), later) is RuleOutcome.NOT_EVALUABLE


# ---------------------------------------------------------------------------
# The research / executable split
# ---------------------------------------------------------------------------


def test_an_instrument_we_cannot_size_stays_in_research_and_leaves_execution() -> None:
    """PO decision D1: the account filters what we trade, never what we measure."""
    deep = history("AAAEUR", days=30, quote_volume="5000000")
    histories = {deep.key: deep}
    unsizable = instrument("AAAEUR", min_notional="500")

    research = UniversePolicy.of(
        "research",
        (
            QuoteCurrencyRule(allowed=frozenset({"EUR"})),
            ListingAgeRule(),
            MedianQuoteVolumeRule(minimum=Notional(Decimal(250_000)), histories=histories),
            ExcludedAssetClassRule(excluded_bases=frozenset()),
        ),
    )
    executable = executable_from(
        research,
        (
            MinNotionalFeasibilityRule(account=account()),
            LotSizeFeasibilityRule(account=account(), histories=histories),
        ),
    )

    assert research.evaluate([unsizable], DECISION).size == 1
    assert executable.evaluate([unsizable], DECISION).size == 0


def test_a_coarse_lot_size_is_rejected_only_by_the_executable_policy() -> None:
    deep = history("AAAEUR", days=30, quote_volume="5000000", close="100")
    histories = {deep.key: deep}
    # one lot step is worth 100 * 1 = 100 EUR, far above the 1.875 allowance
    coarse = instrument("AAAEUR", lot_size="1")
    rule = LotSizeFeasibilityRule(account=account(), histories=histories)

    assert rule.evaluate(coarse, DECISION) is RuleOutcome.REJECT
    assert rule.evaluate(instrument("AAAEUR", lot_size="0.0001"), DECISION) is RuleOutcome.ADMIT


def test_excluded_asset_classes_are_rejected_by_construction() -> None:
    rule = ExcludedAssetClassRule(excluded_bases=frozenset({"USDT", "WBTC"}))

    assert rule.evaluate(instrument("USDTEUR", base="USDT"), DECISION) is RuleOutcome.REJECT
    assert rule.evaluate(instrument("WBTCEUR", base="WBTC"), DECISION) is RuleOutcome.REJECT
    assert rule.evaluate(instrument("BTCEUR", base="BTC"), DECISION) is RuleOutcome.ADMIT


def test_a_delisted_instrument_is_a_member_before_its_delisting_and_not_after() -> None:
    """Survivorship, in the one place it can still be designed out."""
    deep = history("AAAEUR", days=400, quote_volume="5000000")
    delisted = Instrument(
        venue=VENUE,
        symbol="AAAEUR",
        base="AAA",
        quote="EUR",
        listed_at=Timestamp(datetime(2020, 1, 1, tzinfo=UTC)),
        tick_size=Price(Decimal("0.01")),
        lot_size=Quantity(Decimal("0.0001")),
        min_notional=Notional(Decimal(10)),
        delisted_at=Timestamp(datetime(2024, 7, 1, tzinfo=UTC)),
        provenance=Provenance.VENUE_ANNOUNCEMENT,
    )
    policy = UniversePolicy.of(
        "research",
        (
            QuoteCurrencyRule(allowed=frozenset({"EUR"})),
            MedianQuoteVolumeRule(minimum=Notional(Decimal(250_000)), histories={deep.key: deep}),
        ),
    )

    assert policy.evaluate([delisted], DECISION).size == 1
    assert policy.evaluate([delisted], Timestamp(datetime(2024, 8, 1, tzinfo=UTC))).size == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_universe_computation_is_byte_identical_across_runs() -> None:
    """Same inputs, same membership, same serialised bytes.

    A universe that depends on dictionary ordering or set iteration produces a
    different backtest every run, and the difference is invisible in aggregate.
    """
    histories = {
        item.key: item
        for item in (
            history(f"SYM{index}EUR", days=60, quote_volume=str(100_000 * (index + 1)))
            for index in range(20)
        )
    }
    candidates = [instrument(f"SYM{index}EUR") for index in range(20)]
    policy = UniversePolicy.of(
        "research",
        (
            QuoteCurrencyRule(allowed=frozenset({"EUR"})),
            ListingAgeRule(),
            MedianQuoteVolumeRule(minimum=Notional(Decimal(250_000)), histories=histories),
            ExcludedAssetClassRule(excluded_bases=frozenset()),
        ),
    )

    first = policy.evaluate(candidates, DECISION)
    second = policy.evaluate(list(reversed(candidates)), DECISION)

    assert first.members == second.members
    assert json.dumps([str(key) for key in first.members]) == json.dumps(
        [str(key) for key in second.members]
    )
    assert first.rejection_counts() == second.rejection_counts()


@pytest.mark.parametrize(
    ("size", "band"),
    [(0, "too-thin"), (14, "too-thin"), (15, "low-power"), (25, "low-power"), (26, "ok")],
)
def test_the_power_bands_match_the_phase_0_thresholds(size: int, band: str) -> None:
    assert power_band(size) == band

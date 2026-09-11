"""Rules T1, T2, T3 and R1: the tick, the circularity, the collapse and the add-back.

Amendment 13, section 35. Every arithmetic claim is tested on inputs whose answer is
known without a dataset, because a registered procedure is only worth registering if it
gives the same answer to anybody who runs it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.exchanges.binance import futures_metadata
from sextant.adapters.exchanges.binance.futures_metadata import (
    PRICE_FILTER,
    TICK_LEVEL,
    TICK_SIZE_FIELD,
    SymbolTick,
    TickMetadataUnavailable,
    TickSnapshot,
)
from sextant.app.spike_006_f1 import (
    BAND_MINIMUM_SYMBOLS,
    TICK_BOUND_CEILING,
    TICK_BOUND_DOMINANCE,
    TICK_SUBSET_MINIMUM_SYMBOLS,
)
from sextant.app.spike_006_f1_analysis import (
    AddBackWouldClearACriterion,
    CurrencyCorrection,
    assert_the_add_back_may_substitute,
)
from sextant.app.spike_006_f1_thin import Selection, ThinBandStudy
from sextant.app.spike_006_f1_tick import (
    Correlation,
    SymbolTickReading,
    TickStudy,
    correlate,
    readings,
    residual_bands,
    write,
)
from sextant.domain.time import Timestamp

# ---------------------------------------------------------------------------
# Rule T1: what the venue publishes, parsed and snapshotted
# ---------------------------------------------------------------------------


def response(*rows: dict[str, object]) -> dict[str, object]:
    """One ``exchangeInfo`` body, in the shape the venue actually serves."""
    return {"symbols": list(rows)}


def symbol_row(symbol: str, tick: str | None, **extra: object) -> dict[str, object]:
    """One symbol entry, with or without a price filter."""
    filters: list[dict[str, object]] = [{"filterType": "LOT_SIZE", "stepSize": "1"}]
    if tick is not None:
        filters.append({"filterType": PRICE_FILTER, TICK_SIZE_FIELD: tick})
    return {"symbol": symbol, "filters": filters, **extra}


def test_the_increment_is_read_exactly_as_the_venue_published_it() -> None:
    parsed = futures_metadata.parse(response(symbol_row("BTCUSDT", "0.10")))
    assert parsed["BTCUSDT"].tick_size == Decimal("0.10")


def test_a_symbol_with_no_price_filter_is_skipped_rather_than_defaulted() -> None:
    parsed = futures_metadata.parse(
        response(symbol_row("AAAUSDT", "0.01"), symbol_row("BBBUSDT", None))
    )
    assert "BBBUSDT" not in parsed


def test_a_response_with_no_usable_increment_is_refused_rather_than_emptied() -> None:
    with pytest.raises(TickMetadataUnavailable):
        futures_metadata.parse(response(symbol_row("AAAUSDT", None)))


def test_a_non_positive_increment_cannot_be_constructed() -> None:
    with pytest.raises(TickMetadataUnavailable):
        SymbolTick(symbol="AAAUSDT", tick_size=Decimal(0), status="TRADING", contract_type="")


def test_the_snapshot_round_trips_through_the_file_it_commits(tmp_path: Path) -> None:
    snapshot = TickSnapshot(
        fetched_at="2026-09-11T00:00:00+00:00",
        digest="abc",
        symbols=futures_metadata.parse(
            response(symbol_row("BTCUSDT", "0.10", status="TRADING", contractType="PERPETUAL"))
        ),
    )
    written = futures_metadata.write(snapshot, tmp_path / "tick.json")
    back = futures_metadata.read(written)
    assert back.tick("BTCUSDT") == Decimal("0.10")
    assert back.digest == "abc"
    assert back.symbols["BTCUSDT"].contract_type == "PERPETUAL"


def test_the_snapshot_carries_the_label_that_says_it_is_not_a_measurement() -> None:
    snapshot = TickSnapshot(fetched_at="", digest="", symbols={})
    payload = snapshot.as_json()
    assert payload["what_it_is"] == TICK_LEVEL
    assert "TODAY'S tick" in str(payload["point_in_time_limitation"])


def test_an_unlisted_symbol_answers_none_rather_than_zero() -> None:
    snapshot = TickSnapshot(fetched_at="", digest="", symbols={})
    assert snapshot.tick("BTCUSDT") is None


# ---------------------------------------------------------------------------
# Rule T2: the spread in ticks, and the two thresholds
# ---------------------------------------------------------------------------


def reading(symbol: str, tick: str, price: str, half_spread_bps: float) -> SymbolTickReading:
    """One reading, built from figures whose ratio is arithmetic rather than data."""
    return SymbolTickReading(
        symbol=symbol,
        metadata_tick=Decimal(tick),
        derived_tick=None,
        reference_price=Decimal(price),
        measured_half_spread_bps=half_spread_bps,
        selected_at="2023-05-15",
    )


def test_a_spread_of_exactly_one_tick_reads_as_one_tick() -> None:
    # A tick of 0.01 on a price of 100 is one basis point, so a one basis point
    # quoted spread is exactly one tick. Half of it is the half-spread.
    item = reading("AAAUSDT", "0.01", "100", 0.5)
    assert item.relative_tick_bps == pytest.approx(1.0)
    assert item.spread_in_ticks == pytest.approx(1.0)


def test_the_tick_bound_test_is_the_registered_ceiling() -> None:
    at_the_ceiling = reading("AAAUSDT", "0.01", "100", float(TICK_BOUND_CEILING) / 2.0)
    just_above = reading("BBBUSDT", "0.01", "100", float(TICK_BOUND_CEILING) / 2.0 + 0.01)
    assert at_the_ceiling.is_tick_bound
    assert not just_above.is_tick_bound


def test_a_tick_bound_instrument_has_a_half_spread_of_exactly_half_a_tick() -> None:
    item = reading("AAAUSDT", "0.01", "100", 0.5)
    assert item.half_spread_from_the_tick_bps == pytest.approx(item.relative_tick_bps / 2.0)


def test_a_spread_below_one_tick_is_flagged_because_it_is_impossible() -> None:
    item = reading("AAAUSDT", "0.01", "100", 0.235)
    assert item.spread_in_ticks < 1.0
    assert item.spread_is_below_one_tick


def test_a_spread_of_one_tick_is_not_flagged_as_below_one() -> None:
    assert not reading("AAAUSDT", "0.01", "100", 0.5).spread_is_below_one_tick


def test_the_derivation_is_called_an_overestimate_only_against_the_measured_spread() -> None:
    wide = SymbolTickReading(
        symbol="AAAUSDT",
        metadata_tick=Decimal("0.01"),
        derived_tick=Decimal("0.10"),
        reference_price=Decimal("100"),
        measured_half_spread_bps=0.5,
        selected_at="",
    )
    assert wide.derivation_overestimated
    assert wide.derived_over_metadata == pytest.approx(10.0)


def test_a_reading_with_no_derived_tick_makes_no_claim_about_the_derivation() -> None:
    item = reading("AAAUSDT", "0.01", "100", 0.5)
    assert not item.derivation_overestimated
    assert item.derived_over_metadata is None


def test_a_symbol_the_venue_does_not_list_is_left_out_rather_than_given_a_stand_in() -> None:
    snapshot = TickSnapshot(
        fetched_at="",
        digest="",
        symbols={
            "AAAUSDT": SymbolTick(
                symbol="AAAUSDT",
                tick_size=Decimal("0.01"),
                status="TRADING",
                contract_type="PERPETUAL",
            )
        },
    )
    items = readings(
        snapshot,
        {"AAAUSDT": 0.5, "BBBUSDT": 0.5},
        {"AAAUSDT": Decimal(100), "BBBUSDT": Decimal(100)},
        {},
        {"AAAUSDT": Timestamp.parse("2023-05-15T00:00:00+00:00")},
    )
    assert [item.symbol for item in items] == ["AAAUSDT"]


def test_a_subset_below_the_registered_minimum_is_refused_rather_than_reported() -> None:
    items = [reading(f"{index}USDT", "0.01", "100", 0.5) for index in range(4)]
    result = correlate("four", items, minimum=TICK_SUBSET_MINIMUM_SYMBOLS)
    assert result.rho is None
    assert result.refused_because is not None
    assert "too small to say" in result.refused_because


def test_a_subset_at_the_registered_minimum_is_computed() -> None:
    items = [
        reading("AAAUSDT", "0.01", "100", 0.5),
        reading("BBBUSDT", "0.02", "100", 1.0),
        reading("CCCUSDT", "0.03", "100", 1.5),
        reading("DDDUSDT", "0.04", "100", 2.0),
        reading("EEEUSDT", "0.05", "100", 2.5),
    ]
    result = correlate("five", items, minimum=TICK_SUBSET_MINIMUM_SYMBOLS)
    assert result.rho == pytest.approx(1.0)
    assert result.refused_because is None


def test_a_refused_correlation_reports_no_verdict_on_the_registered_level() -> None:
    result = Correlation(label="x", symbols=(), candidate=None, refused_because="too few")
    assert result.as_json()["clears_the_registered_level"] is None


# ---------------------------------------------------------------------------
# Rule T3: the collapse
# ---------------------------------------------------------------------------


def study_of(*items: SymbolTickReading) -> TickStudy:
    """One study with the correlations already refused, so T3 is tested alone."""
    empty = Correlation(label="", symbols=(), candidate=None, refused_because="not under test")
    return TickStudy(
        snapshot_digest="",
        snapshot_fetched_at="",
        readings=items,
        whole_sample=empty,
        floating_subset=empty,
        residual_recut=residual_bands(items, ()),
        ratio_deep=Decimal("18.87"),
        ratio_mid=Decimal("20.64"),
    )


def test_tick_bound_dominates_only_above_the_registered_share() -> None:
    half = study_of(
        reading("AAAUSDT", "0.01", "100", 0.5),
        reading("BBBUSDT", "0.01", "100", 5.0),
    )
    assert half.tick_bound_share == Decimal("0.5")
    assert Decimal("0.5") == TICK_BOUND_DOMINANCE
    assert not half.tick_bound_dominates


def test_a_majority_at_the_floor_dominates() -> None:
    most = study_of(
        reading("AAAUSDT", "0.01", "100", 0.5),
        reading("BBBUSDT", "0.01", "100", 0.5),
        reading("CCCUSDT", "0.01", "100", 5.0),
    )
    assert most.tick_bound_dominates


def test_a_residual_below_the_band_minimum_evidences_no_band() -> None:
    most = study_of(
        *[reading(f"{index}USDT", "0.01", "100", 0.5) for index in range(5)],
        reading("XXXUSDT", "0.01", "100", 5.0),
        reading("YYYUSDT", "0.01", "100", 6.0),
    )
    assert len(most.floating) < BAND_MINIMUM_SYMBOLS
    assert most.residual_is_below_the_registered_minimum
    assert most.residual_recut is None


def test_a_sample_entirely_at_the_floor_needs_no_band_at_all() -> None:
    every = study_of(*[reading(f"{index}USDT", "0.01", "100", 0.5) for index in range(4)])
    assert every.floating == ()
    assert every.bands_the_evidence_supports == 0


def test_every_tick_bound_symbol_gets_a_per_symbol_half_spread(tmp_path: Path) -> None:
    result = study_of(
        reading("AAAUSDT", "0.01", "100", 0.5),
        reading("BBBUSDT", "0.02", "100", 1.0),
        reading("CCCUSDT", "0.01", "100", 5.0),
    )
    payload = json.loads(write(result, tmp_path / "tick.json").read_text(encoding="utf-8"))
    per_symbol = payload["rule_t3"]["half_spread_bps_by_symbol_from_the_tick"]
    assert sorted(per_symbol) == ["AAAUSDT", "BBBUSDT"]
    assert per_symbol["AAAUSDT"] == pytest.approx(0.5)


def test_the_hypothesis_records_how_far_apart_the_two_ratios_are() -> None:
    result = study_of(reading("AAAUSDT", "0.01", "100", 0.5))
    assert result.ratio_gap == pytest.approx(float(Decimal("20.64") / Decimal("18.87")))
    assert "hypothesis" in str(result.as_json()["rule_t3h"]["status"])


# ---------------------------------------------------------------------------
# Rule T4: whether the band is traded at all
# ---------------------------------------------------------------------------


def selection(variant: str, **by_band: tuple[str, ...]) -> Selection:
    return Selection(
        variant=variant,
        at=Timestamp.parse("2026-01-01T00:00:00+00:00"),
        by_band=dict(by_band),
        unbandable=(),
    )


def test_a_family_that_never_opens_a_thin_instrument_closes_the_question() -> None:
    result = ThinBandStudy(
        family="F1",
        instants=2,
        selections=(selection("a", deep=("AAAUSDT",)), selection("b", mid=("BBBUSDT",))),
    )
    assert not result.any_variant_selects_thin
    assert "EMPTY IN PRACTICE" in str(result.as_json()["outcome"])


def test_one_thin_selection_anywhere_is_enough_to_occupy_the_band() -> None:
    result = ThinBandStudy(
        family="F1",
        instants=2,
        selections=(selection("a", deep=("AAAUSDT",)), selection("b", thin=("CCCUSDT",))),
    )
    assert result.any_variant_selects_thin
    assert result.thin_symbols == ("CCCUSDT",)
    assert result.thin_selections_of("a") == ()
    assert len(result.thin_selections_of("b")) == 1


def test_the_outcome_says_a12_2_is_a_gate_rather_than_a_verdict() -> None:
    result = ThinBandStudy(family="F1", instants=1, selections=(selection("a", thin=("CCCUSDT",)),))
    outcome = str(result.as_json()["outcome"])
    assert "GATE" in outcome.upper()
    assert "FAILS at the headline" in outcome


def test_the_bands_are_counted_in_instrument_instants_not_instruments() -> None:
    result = ThinBandStudy(
        family="F1",
        instants=2,
        selections=(
            selection("a", deep=("AAAUSDT", "BBBUSDT")),
            selection("a", deep=("AAAUSDT",), thin=("CCCUSDT",)),
        ),
    )
    assert result.counts_by_band()["deep"] == 3
    assert result.total_selected == 4
    assert result.share("thin") == Decimal(1) / Decimal(4)


def test_a_study_with_no_selections_has_no_share_of_an_empty_total() -> None:
    result = ThinBandStudy(family="F1", instants=0, selections=())
    assert result.share("thin") is None


# ---------------------------------------------------------------------------
# Rule R1: when an add-back may stand in for a rerun
# ---------------------------------------------------------------------------


def correction(variant: str, net_pnl: str, charged: str) -> CurrencyCorrection:
    """One variant's currency line, with the double count large enough to matter."""
    return CurrencyCorrection(
        variant=variant,
        cell="vip0_even",
        turnover=Decimal("100000"),
        as_charged=Decimal(charged),
        initial_equity=Decimal("1500"),
        terminal_equity=Decimal("1400"),
        net_pnl=Decimal(net_pnl),
    )


def test_the_add_back_may_substitute_while_every_variant_still_loses() -> None:
    # The double count is 97.10 here, so a variant losing 200 still loses 102.90.
    verdict = assert_the_add_back_may_substitute(
        (correction("a", "-200", "100"), correction("b", "-150", "100"))
    )
    assert verdict.may_substitute_for_a_rerun
    assert verdict.variants_that_would_stop_failing == ()


def test_a_variant_that_would_stop_losing_forces_a_rerun() -> None:
    with pytest.raises(AddBackWouldClearACriterion) as raised:
        assert_the_add_back_may_substitute(
            (correction("a", "-200", "100"), correction("b", "-5", "100"))
        )
    assert "rerun without exception" in str(raised.value)
    assert "b" in str(raised.value)


def test_a_variant_landing_exactly_on_zero_forces_a_rerun_too() -> None:
    # Zero is not the failing side of "the return is positive": a figure that no
    # longer loses cannot be asserted to fail from an aggregate, whatever it does next.
    exact = correction("a", "-200", "100")
    at_zero = CurrencyCorrection(
        variant="b",
        cell="vip0_even",
        turnover=Decimal("100000"),
        as_charged=Decimal("100"),
        initial_equity=Decimal("1500"),
        terminal_equity=Decimal("1400"),
        net_pnl=-(Decimal("100") - exact.corrected),
    )
    with pytest.raises(AddBackWouldClearACriterion):
        assert_the_add_back_may_substitute((exact, at_zero))


def test_an_unexamined_add_back_may_not_substitute_for_anything() -> None:
    with pytest.raises(AddBackWouldClearACriterion) as raised:
        assert_the_add_back_may_substitute(())
    assert "no variant was examined" in str(raised.value).lower()


def test_rule_r1_holds_against_the_committed_result_file() -> None:
    # The claim section 17.2 rests on, checked against the bytes it was made from
    # rather than against a fixture that agrees with it by construction.
    from sextant.app.spike_006_f1 import headline_cell
    from sextant.app.spike_006_f1_analysis import currency_corrections

    payload = json.loads(Path("research/spike-006-f1.json").read_text(encoding="utf-8"))
    items = currency_corrections(payload, cell=headline_cell().label)
    verdict = assert_the_add_back_may_substitute(items)
    assert len(items) == 9
    assert verdict.may_substitute_for_a_rerun
    assert max(item.corrected_net_pnl for item in items) < 0

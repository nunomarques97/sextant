"""Rules B1 and M1: the cut quantity, the band count, and whether a band is used at all.

Amendment 12, sections 34.3 and 34.4. The arithmetic is tested on inputs whose answer is
known without a dataset, because the point of a registered procedure is that it produces
the same answer for anybody who runs it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sextant.app.spike_006_f1 import (
    BAND_MINIMUM_SYMBOLS,
    BAND_RECUT_ALPHA,
    BAND_SEPARATION_FACTOR,
)
from sextant.app.spike_006_f1_bands import (
    BANDS,
    BandStudy,
    BandStudyIncomplete,
    Candidate,
    Occupancy,
    band_of,
    measured_half_spreads,
    permutation_p_value,
    rebalance_instants,
    recut,
    scored,
)
from sextant.domain.money import Notional

MEASURED = {
    "AAAUSDT": 0.02,
    "BBBUSDT": 0.03,
    "CCCUSDT": 0.60,
    "DDDUSDT": 0.70,
    "EEEUSDT": 1.00,
    "FFFUSDT": 2.00,
}


def candidate(name: str, values: dict[str, float]) -> Candidate:
    """An unscored candidate, as the loader builds one."""
    return Candidate(name=name, values=values, rho=None, p_value=None)


def perfect() -> Candidate:
    """A quantity that orders the measured spread exactly."""
    return scored(candidate("perfect", dict(MEASURED)), MEASURED)


def noise() -> Candidate:
    """A quantity that orders nothing."""
    values = {
        "AAAUSDT": 5.0,
        "BBBUSDT": 1.0,
        "CCCUSDT": 6.0,
        "DDDUSDT": 2.0,
        "EEEUSDT": 4.0,
        "FFFUSDT": 3.0,
    }
    return scored(candidate("noise", values), MEASURED)


# ---------------------------------------------------------------------------
# The candidate test
# ---------------------------------------------------------------------------


def test_a_perfect_ordering_clears_the_registered_level() -> None:
    """Six symbols ranked exactly right is p = 2/720, which clears 0.05 comfortably."""
    item = perfect()
    assert item.rho == pytest.approx(1.0)
    assert item.p_value is not None
    assert item.p_value < float(BAND_RECUT_ALPHA)
    assert item.clears


def test_a_shuffled_ordering_does_not_clear_it() -> None:
    """A quantity that carries no ordering information may not cut a cost model."""
    item = noise()
    assert item.p_value is not None
    assert item.p_value > float(BAND_RECUT_ALPHA)
    assert not item.clears


def test_the_permutation_test_is_reproducible_from_the_registered_seed() -> None:
    """Same inputs, same p-value, every time. A seedless test is not a registered test."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ys = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]
    assert permutation_p_value(xs, ys) == permutation_p_value(xs, ys)


def test_a_reversed_ordering_clears_it_too() -> None:
    """The test is two-sided: a quantity that orders spread backwards still orders it."""
    values = {symbol: -value for symbol, value in MEASURED.items()}
    item = scored(candidate("reversed", values), MEASURED)
    assert item.rho == pytest.approx(-1.0)
    assert item.clears


def test_a_candidate_missing_a_symbol_answers_none_rather_than_correlating_on_five() -> None:
    """A correlation over a subset is a different statistic from the registered one."""
    partial = dict(MEASURED)
    partial.pop("FFFUSDT")
    item = scored(candidate("partial", partial), MEASURED)
    assert item.rho is None
    assert item.p_value is None
    assert not item.clears


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------


def test_the_strongest_clearing_candidate_takes_the_cut() -> None:
    """Ranked on absolute rho, among those that clear. Not on whichever is convenient."""
    result = recut(MEASURED, (noise(), perfect()))
    assert result.chosen == "perfect"


def test_no_clearing_candidate_means_nothing_is_re_cut() -> None:
    """One band, and the finding is that none of the four quantities earned a partition."""
    result = recut(MEASURED, (noise(),))
    assert result.chosen is None
    assert result.bands_supported == 1
    assert result.edges == ()


def test_six_symbols_cannot_support_three_bands_and_the_result_says_why() -> None:
    """A ceiling set by the sample, reported as such rather than as a claim about spread."""
    result = recut(MEASURED, (perfect(),))
    assert result.bands_supported == len(MEASURED) // BAND_MINIMUM_SYMBOLS
    assert result.bands_supported < len(BANDS)
    assert result.limited_by_the_sample


def test_nine_symbols_can_support_three_when_they_separate() -> None:
    """The ceiling lifts with the sample, so the rule is not secretly a two-band rule."""
    measured = {
        "A": 0.01,
        "B": 0.02,
        "C": 0.03,
        "D": 0.40,
        "E": 0.50,
        "F": 0.60,
        "G": 8.00,
        "H": 9.00,
        "I": 10.00,
    }
    item = scored(candidate("perfect", dict(measured)), measured)
    result = recut(measured, (item,))
    assert result.bands_supported == 3
    assert not result.limited_by_the_sample
    assert len(result.groups) == 3


def test_bands_that_do_not_separate_collapse_rather_than_being_kept() -> None:
    """A partition that does not partition carries authority it has not got."""
    measured = {
        "A": 1.00,
        "B": 1.01,
        "C": 1.02,
        "D": 1.03,
        "E": 1.04,
        "F": 1.05,
        "G": 1.06,
        "H": 1.07,
        "I": 1.08,
    }
    item = scored(candidate("perfect", dict(measured)), measured)
    result = recut(measured, (item,))
    assert result.bands_supported == 1


def test_adjacent_bands_must_differ_by_the_registered_factor() -> None:
    """Exactly at the factor is not above it, so the rule refuses a borderline split."""
    measured = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.5, "E": 1.5, "F": 1.5}
    item = scored(candidate("perfect", dict(measured)), measured)
    result = recut(measured, (item,))
    assert float(BAND_SEPARATION_FACTOR) > 1.5
    assert result.bands_supported == 1


def test_every_band_carries_its_members_and_its_median() -> None:
    """A band nobody can list the members of is not auditable."""
    result = recut(MEASURED, (perfect(),))
    assert sum(len(group) for group in result.groups) == len(MEASURED)
    assert len(result.group_medians) == result.bands_supported
    assert result.group_medians[0] < result.group_medians[-1]


# ---------------------------------------------------------------------------
# Occupancy, and what an empty band means
# ---------------------------------------------------------------------------


def occupancy_of(deep: int, mid: int, thin: int) -> Occupancy:
    """An occupancy count, without a dataset behind it."""
    return Occupancy(
        instants=69,
        instrument_instants={"deep": deep, "mid": mid, "thin": thin},
        distinct_symbols={
            "deep": ("A",),
            "mid": ("B",) if mid else (),
            "thin": ("C",) if thin else (),
        },
        instants_with_any={"deep": 69, "mid": 40 if mid else 0, "thin": 9 if thin else 0},
        unrankable=0,
    )


def test_an_occupied_band_needs_measuring_and_an_empty_one_does_not() -> None:
    """Rule M1's whole point: the cheap question decides whether the expensive one runs."""
    both = BandStudy(
        occupancy=occupancy_of(9300, 2297, 200), recut=recut(MEASURED, ()), selected_at="2023-05-15"
    )
    neither = BandStudy(
        occupancy=occupancy_of(9300, 0, 0), recut=recut(MEASURED, ()), selected_at="2023-05-15"
    )
    assert both.bands_needing_measurement == ("mid", "thin")
    assert neither.bands_needing_measurement == ()


def test_an_empty_band_reports_as_empty_in_practice() -> None:
    """Recorded as a finding rather than as a gap, which is what the rule registers."""
    payload = occupancy_of(9300, 0, 0).as_json()
    assert payload["empty_in_practice"] == ["mid", "thin"]
    assert "never charged" in str(payload["what_an_empty_band_means"])


def test_the_shares_add_to_one() -> None:
    """The three bands partition the instrument-instants, so their shares must."""
    found = occupancy_of(9300, 2297, 200)
    total = sum(Decimal(str(found.share(band))) for band in BANDS)
    assert abs(total - Decimal(1)) < Decimal("0.0000001")


def test_a_band_with_no_instants_has_no_share_of_an_empty_total() -> None:
    """Nothing observed is not a share of zero; it is no share at all."""
    assert occupancy_of(0, 0, 0).share("deep") is None


# ---------------------------------------------------------------------------
# The plumbing the rules stand on
# ---------------------------------------------------------------------------


def test_the_bands_are_the_cost_models_own_floors() -> None:
    """The occupancy count has to use the same partition the cost model charges on."""
    assert band_of(Notional(Decimal(10_000_000))) == "deep"
    assert band_of(Notional(Decimal(2_000_000))) == "mid"
    assert band_of(Notional(Decimal(500_000))) == "thin"


def test_the_rebalance_instants_are_every_month_of_the_registered_window() -> None:
    """Sixty-nine usable months, which is what the dataset resolved to."""
    instants = rebalance_instants()
    assert len(instants) == 69
    assert instants[0].isoformat().startswith("2020-11-01")
    assert instants[-1].isoformat().startswith("2026-07-01")
    assert all(item.value.day == 1 for item in instants)


def test_the_measured_side_is_a_half_spread() -> None:
    """The cost model charges a half-spread per leg, so the comparison must be halved."""
    payload = {
        "per_symbol_day": [
            {"symbol": "AAAUSDT", "day": "2023-06-01", "whole_day": {"median_bps": "2.0"}},
            {"symbol": "AAAUSDT", "day": "2023-08-01", "whole_day": {"median_bps": "4.0"}},
        ]
    }
    assert measured_half_spreads(payload) == {"AAAUSDT": 1.5}


def test_a_symbol_day_with_no_median_is_skipped_rather_than_counted_as_zero() -> None:
    """A day the venue did not cover is not a day of zero spread."""
    payload = {
        "per_symbol_day": [
            {"symbol": "AAAUSDT", "day": "2023-06-01", "whole_day": {"median_bps": "2.0"}},
            {"symbol": "AAAUSDT", "day": "2023-08-01", "whole_day": {"median_bps": None}},
        ]
    }
    assert measured_half_spreads(payload) == {"AAAUSDT": 1.0}


def test_a_file_with_no_symbol_days_is_refused_rather_than_averaged() -> None:
    """Nothing is re-cut on a measurement that is not there."""
    with pytest.raises(BandStudyIncomplete, match="nothing is re-cut"):
        measured_half_spreads({})

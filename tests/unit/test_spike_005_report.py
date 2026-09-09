"""The report renderer: what it says, and what it refuses to say.

The renderer does no arithmetic. That is the property under test here more than
any particular table: a renderer that could compute would be able to disagree
with the run it describes, and the two would then have to be reconciled by hand
every time either changed.

The payload is built here rather than read from ``research/spike-005.json``, so
the test is hermetic and does not depend on a git-ignored file that may or may
not exist on the machine running it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sextant.app.spike_005_report import render

COST_LINES = {
    "fees": "10.00",
    "spread": "20.00",
    "slippage": "5.00",
    "funding": "0.00",
    "fx_conversion": "1.00",
    "delisting": "4.00",
}


def _benchmark(construct: str, policy: str, cell: str, terminal: str) -> dict[str, object]:
    return {
        "construct": construct,
        "kind": "benchmark",
        "quote_policy": policy,
        "cell_id": cell,
        "fill_mix": "50/50 maker/taker (assumed)",
        "denomination": "EUR",
        "gross_pnl": "-100.00",
        "net_pnl": "-140.00",
        "terminal_return": terminal,
        "max_drawdown": "0.5000",
        "costs": dict(COST_LINES),
        "sharpe_annualised": -0.25,
        "monthly_returns": {},
    }


def _variant(
    name: str,
    *,
    policy: str = "USDT",
    cell: str = "binance_vip0",
    net: str = "-0.7889",
    sharpe: float = 0.036,
    p95: float = -0.029,
    beats: bool = True,
    dsr: float = 0.0,
) -> dict[str, object]:
    return {
        "variant": name,
        "quote_policy": policy,
        "cell_id": cell,
        "fill_mix": "50/50 maker/taker (assumed)",
        "denomination": "EUR",
        "net_return": net,
        "max_drawdown": "0.9000",
        "sharpe_annualised": sharpe,
        "turnover": "5000.00",
        "costs": dict(COST_LINES),
        "decomposition": {
            "combined_net_return": net,
            "selection_net_return": "-0.8000",
            "timing_net_return": "-0.8655",
            "combined_sharpe": sharpe,
            "selection_sharpe": -0.1,
            "timing_sharpe": -0.3,
        },
        "exposure_matched_null": {
            "seed_count": 500,
            "p50": -0.46,
            "p90": -0.2,
            "p95": p95,
            "p99": 0.05,
            "p95_ci": [p95 - 0.01, p95 + 0.01],
        },
        "deflated_sharpe": {
            "trials": 253,
            "trials_excluding_nulls": 80,
            "probabilistic_sharpe": 0.53,
            "deflated_sharpe": dsr,
            "expected_maximum": 0.4,
            "trial_sharpe_variance": 0.01,
            "autocorrelation_corrected": False,
        },
        "independence": {
            "observations": 52,
            "lag_one_autocorrelation": 0.06,
            "effective_observations": 46.0,
            "sharpe_standard_error": 0.147,
            "detectable_annualised_sharpe": 0.998,
            "shortfall_vs_floor": 0.0,
        },
        "breadth": {
            "median_positions": 10.0,
            "mean_pairwise_correlation": 0.364,
            "effective_positions": 2.33,
            "measured_pairs": 31550,
            "distinct_names_held": 257,
            "is_evaluable": True,
        },
        "regimes": {
            "bull": {"net_return": "0.1280", "months": 15},
            "bear": {"net_return": "-0.7030", "months": 27},
            "crash": {"net_return": "0.2140", "months": 1},
            "recovery": {"net_return": "-0.4810", "months": 8},
        },
        "equal_weight_regimes": {
            "bull": {"net_return": "-0.1640", "months": 15},
            "bear": {"net_return": "-0.7330", "months": 27},
        },
        "criteria": {
            "1_beats_exposure_matched_null": beats,
            "2_survives_deflation": False,
            "3_win_is_selection": False,
            "4_regime_stability": False,
            "4_regimes_positive": 1,
            "4_regimes_countable": ["bear", "bull", "recovery"],
            "effective_observations": 46.0,
            "effective_observations_floor": 24.0,
            "effective_observations_met": True,
        },
    }


def _payload(*, notes: list[str] | None = None) -> dict[str, object]:
    variants = [
        _variant("xs-momentum-360-5"),
        _variant("ts-trend-90-sma", net="-0.9712", sharpe=-0.655, beats=False),
        _variant("xs-momentum-360-5", cell="kraken_reality", net="-0.8150"),
        _variant("ts-trend-90-sma", cell="kraken_reality", net="-0.9771", sharpe=-0.7, beats=False),
        _variant("xs-momentum-360-5", policy="EUR", net="-0.5214", dsr=0.15),
    ]
    return {
        "engine_version": "sextant-005",
        "code_version": "abcdef123456",
        "registered_version": "v1",
        "denomination": "EUR",
        "window": {
            "first_usable_month": "2021-02",
            "last_usable_month_end": "2026-08",
            "usable_months": 66,
            "out_of_sample": {
                "start": "2022-02-01T00:00:00+00:00",
                "end": "2026-06-01T00:00:00+00:00",
            },
            "folds": {"folds": [{"index": index} for index in range(4)]},
            "scored_months": 52,
        },
        "universe": {"USDT": [114, 307, 372], "EUR": [4, 13, 32]},
        "regime_month_counts": {"bull": 18, "bear": 34, "crash": 2, "recovery": 13},
        "regime_month_counts_scored": {"bull": 15, "bear": 29, "crash": 1, "recovery": 8},
        "trials": {"including_nulls": 253, "excluding_nulls": 80},
        "seeds": {
            "fully_invested": 2000,
            "exposure_matched": 500,
            "registered_exposure_matched": 500,
            "measured_seconds_per_null_run": 0.125,
            "smoke_override": None,
        },
        "deterministic": [
            _benchmark("equal-weight-passive", "USDT", "binance_vip0", "-0.8660"),
            _benchmark("btc-buy-and-hold", "USDT", "binance_vip0", "0.8631"),
        ],
        "nulls": [],
        "variants": variants,
        "notes": notes or [],
        "seconds": 4908.0,
    }


def _render(payload: dict[str, object], tmp_path: Path) -> str:
    source = tmp_path / "spike-005.json"
    with source.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle)
    written = render(source, tmp_path / "SPIKE-005-RESULTS.md")
    return written.read_text(encoding="utf-8")


def test_every_section_the_brief_requires_is_present(tmp_path: Path) -> None:
    """Variants, trial count, out-of-sample, DSR, nulls, costs, drawdowns, regimes."""
    report = _render(_payload(), tmp_path)
    for heading in (
        "## The window and the universe",
        "## Regimes",
        "## The six benchmarks, in EUR",
        "## Every variant, out of sample",
        "## Selection, timing, and the two together",
        "## Every variant, per regime",
        "## How much evidence there actually is",
        "## The same variants at the other venue's schedule",
        "## The five pre-registered criteria",
    ):
        assert heading in report


def test_the_denomination_is_stated_and_no_table_mixes_currencies(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    assert "**Every figure below is in EUR.**" in report
    assert "USD" not in report.replace("USDT", "")


def test_an_assumption_is_labelled_where_it_appears(tmp_path: Path) -> None:
    """Invariant 12: a cost assumption is never reported as a measurement."""
    report = _render(_payload(), tmp_path)
    assert "assumptions" in report
    assert "(assumed)" in report
    assert "Nothing here presents one as a measurement." in report


def test_the_research_boundary_is_stated_before_any_number(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    boundary = report.index("research venue of this spike and nothing else")
    assert boundary < report.index("## The window and the universe")


def test_the_trial_count_including_nulls_is_the_one_reported(tmp_path: Path) -> None:
    """Leaving the nulls out is what makes a Deflated Sharpe count dishonest."""
    report = _render(_payload(), tmp_path)
    assert "| trials, including null constructs | 253 |" in report
    assert "| trials, strategies only | 80 |" in report


def test_a_regime_below_six_scored_months_is_marked_inconclusive(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    crash = next(line for line in report.splitlines() if line.startswith("| crash |"))
    assert crash.endswith("| no |")
    bull = next(line for line in report.splitlines() if line.startswith("| bull |"))
    assert bull.endswith("| yes |")


def test_eur_cash_appears_as_an_exact_row_rather_than_a_simulated_one(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    assert "| EUR cash | - | - | - | 0.00 |" in report
    assert "EUR cash is exact rather than simulated" in report


def test_the_decomposition_reports_three_numbers_and_never_one(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    section = report[report.index("## Selection, timing") : report.index("## Every variant, per")]
    assert "| combined | selection | timing |" in section
    row = next(line for line in section.splitlines() if line.startswith("| xs-momentum-360-5 |"))
    assert row.count("%") >= 3


def test_a_variant_that_lost_money_is_never_read_as_a_win(tmp_path: Path) -> None:
    """A win caused only by lower exposure is not a win and must not be reported as one."""
    report = _render(_payload(), tmp_path)
    section = report[report.index("## Selection, timing") : report.index("## Every variant, per")]
    assert "lost money" in section
    assert "selection carried at least half the gain" not in section


def test_a_timing_win_is_named_as_one(tmp_path: Path) -> None:
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    winner = _variant("xs-momentum-360-5", net="0.2000")
    decomposition = winner["decomposition"]
    assert isinstance(decomposition, dict)
    decomposition["selection_net_return"] = "-0.0500"
    decomposition["timing_net_return"] = "0.1800"
    variants[0] = winner
    report = _render(payload, tmp_path)
    assert "**timed, did not select**" in report


def test_the_criteria_table_counts_how_many_passed_all_five(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    assert "**0 of 2 variants satisfy all five criteria.**" in report


def test_sign_stability_is_computed_across_the_cost_cells(tmp_path: Path) -> None:
    report = _render(_payload(), tmp_path)
    section = report[report.index("## The same variants at the other venue") :]
    row = next(line for line in section.splitlines() if line.startswith("| xs-momentum-360-5 |"))
    assert row.strip().endswith("| yes |")


def test_a_note_recorded_during_the_run_reaches_the_report(tmp_path: Path) -> None:
    """A seed-count reduction must never be invisible in the published result."""
    note = "the exposure-matched count stepped down its registered ladder to 250"
    report = _render(_payload(notes=[note]), tmp_path)
    assert "**Notes recorded during the run.**" in report
    assert note in report


def test_an_unmeasurable_breadth_is_reported_as_not_available(tmp_path: Path) -> None:
    """Zero correlation is the flattering answer, so it is never printed by accident."""
    payload = _payload()
    variants = payload["variants"]
    assert isinstance(variants, list)
    first = variants[0]
    assert isinstance(first, dict)
    first["breadth"] = {
        "median_positions": 0.0,
        "mean_pairwise_correlation": 0.0,
        "effective_positions": 0.0,
        "measured_pairs": 0,
        "distinct_names_held": 0,
        "is_evaluable": False,
    }
    report = _render(payload, tmp_path)
    section = report[report.index("## How much evidence") : report.index("## The same variants")]
    row = next(line for line in section.splitlines() if line.startswith("| xs-momentum-360-5 |"))
    assert "n/a" in row


def test_a_payload_that_is_not_a_block_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "spike-005.json"
    source.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(TypeError):
        render(source, tmp_path / "out.md")

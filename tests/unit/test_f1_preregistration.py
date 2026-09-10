"""The guard that makes F1's pre-registration a pre-registration.

A specification the code can silently disagree with is not a specification. The
runner compares every registered number against the constant it will actually use
and refuses to start on any difference. These tests prove the refusal happens
rather than trusting that it would, and the parameters are deliberately the
changes somebody would actually make to flatter a result: a cheaper fee, a
narrower spread, a lighter haircut, a shorter recent window, a bigger budget.

Nothing here writes to real project data. The committed specification is read,
never modified, and every altered copy is written under ``tmp_path``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from sextant.app.spike_006_f1 import (
    ACCOUNT_EQUITY,
    CONFIG_PATH,
    ENGINE_VERSION,
    FAMILY,
    MAINTENANCE_MARGIN_STRESS,
    MAINTENANCE_MARGINS,
    MARGIN_BUFFER_SWEEP,
    MARGIN_FRACTION,
    MINIMUM_DEPTH_MONTHS,
    REGISTERED_CELLS,
    REGISTERED_VARIANTS,
    DriftedFromPreRegistration,
    assert_no_drift,
    budget,
    cell_labels,
    headline_cell,
    registered_grid,
    variant_labels,
)
from sextant.engine.backtest.budget import BudgetLedger, UnregisteredTrial
from sextant.engine.statistics.persistence import (
    BOOTSTRAP_SEED,
    MINIMUM_MONTHS_FOR_A_YEAR,
    RECENT_WINDOW_MONTHS,
)


def _registered() -> dict[str, object]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return {str(key): value for key, value in loaded.items()}


def _write(payload: dict[str, object], path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return path


def _alter(payload: dict[str, object], path: tuple[str | int, ...], value: object) -> None:
    """Set one nested value, walking mappings and lists alike."""
    block: object = payload
    for key in path[:-1]:
        if isinstance(key, int):
            assert isinstance(block, list)
            block = block[key]
        else:
            assert isinstance(block, dict)
            block = block[key]
    last = path[-1]
    if isinstance(last, int):
        assert isinstance(block, list)
        block[last] = value
    else:
        assert isinstance(block, dict)
        block[last] = value


# ---------------------------------------------------------------------------
# The one that matters
# ---------------------------------------------------------------------------


def test_the_committed_specification_agrees_with_the_code() -> None:
    """If this fails, no F1 result may be produced."""
    registered = assert_no_drift()
    assert str(registered["version"]) == "v1.6"
    assert str(registered["family"]) == FAMILY


@pytest.mark.parametrize(
    "path",
    [
        ("account", "equity"),
        ("account", "margin_fraction"),
        ("costs", "spread_bps", "thin"),
        ("costs", "slippage_bps", "deep"),
        ("costs", "delisting_haircut_fraction"),
        ("window", "plans", "walk_forward", "fold_count"),
        ("window", "minimum_months_to_proceed"),
        ("null_experiment", "seed_counts", "exposure_matched_per_cell"),
        ("decay", "recent_window", "months"),
        ("decay", "trend", "seed"),
        ("statistics", "bootstrap", "resamples"),
    ],
)
def test_a_drifted_scalar_refuses_the_run(tmp_path: Path, path: tuple[str, ...]) -> None:
    """A cheaper cost, fewer folds, a shorter recent window: all refused."""
    payload = _registered()
    _alter(payload, path, "1")
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=path[-1]):
        assert_no_drift(altered)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("costs", "cells", 1, "futures_maker_bps"), "0"),
        (("costs", "cells", 1, "maker_fraction"), "1.00"),
        (("costs", "cells", 0, "is_headline"), True),
        (("costs", "cells", 3, "spread_and_slippage_multiplier"), "1"),
        (("variants", "registered", 0, "positions"), 3),
        (("variants", "registered", 7, "require_positive"), False),
        (("variants", "registered", 2, "signal"), "premium"),
        (("capacity", "rules", 2, "minimum_depth_months"), 1),
        (("maintenance_margin", "settings", 2, "status"), "candidate"),
    ],
)
def test_a_drifted_list_entry_refuses_the_run(
    tmp_path: Path, path: tuple[str | int, ...], value: object
) -> None:
    """The grid, the variants, the capacity floor and the stress label.

    A headline moved to the cheapest cell, a fill mix moved to all-maker, a stress
    level relabelled as a candidate rate: each is a change to what a published
    number means, and each is refused by name.
    """
    payload = _registered()
    _alter(payload, path, value)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration):
        assert_no_drift(altered)


def test_moving_the_headline_to_the_cheapest_cell_is_refused(tmp_path: Path) -> None:
    """Explicit because it is the single most flattering edit available here.

    Section 8 puts the headline at the 50/50 fill mix rather than at all-maker,
    precisely because the two legs' fees differ and the mix is a live assumption.
    """
    payload = _registered()
    _alter(payload, ("costs", "cells", 1, "is_headline"), False)
    _alter(payload, ("costs", "cells", 0, "is_headline"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="is_headline"):
        assert_no_drift(altered)


def test_a_specification_missing_a_block_refuses_rather_than_skipping_it() -> None:
    """A renamed key must not pass as silently as a matching one."""
    with pytest.raises((DriftedFromPreRegistration, KeyError)):
        assert_no_drift(Path("config") / "benchmarks.yaml")


def test_a_universe_rule_renamed_out_of_existence_is_named(tmp_path: Path) -> None:
    """The failure an explicit guard exists for, rather than a reflective one."""
    payload = _registered()
    _alter(payload, ("universe", "rules_in_order", 3, "name"), "funding_maybe")
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="funding_evaluability"):
        assert_no_drift(altered)


def test_capacity_rule_c3_removed_is_refused(tmp_path: Path) -> None:
    """Rule C3 is what turns "the edge sits outside the window" into a computation."""
    payload = _registered()
    capacity = payload["capacity"]
    assert isinstance(capacity, dict)
    rules = capacity["rules"]
    assert isinstance(rules, list)
    capacity["rules"] = [rule for rule in rules if str(dict(rule)["id"]) != "C3"]
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="C3"):
        assert_no_drift(altered)


# ---------------------------------------------------------------------------
# The budget, which is the block that decides how many numbers may exist
# ---------------------------------------------------------------------------


def test_the_registered_budget_is_the_budget_the_runner_enforces() -> None:
    """The declaration and the enforced grid are the same 32 trials."""
    declared = budget()
    assert declared.family == FAMILY
    assert declared.engine_version == ENGINE_VERSION
    assert declared.maximum_trials == 32
    assert declared.variants == variant_labels()
    assert declared.cost_cells == cell_labels()
    assert len(list(registered_grid())) == declared.maximum_trials


def test_a_widened_budget_in_the_configuration_refuses_the_run(tmp_path: Path) -> None:
    """A bigger allowance in the file than in the code is refused.

    This is the edit that would let the search widen without touching a single
    variant definition, so it has to be caught by the guard and not only by the
    ledger.
    """
    payload = _registered()
    _alter(payload, ("trial_budget", "maximum_trials"), 40)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="maximum_trials"):
        assert_no_drift(altered)


def test_a_ninth_variant_in_the_budget_block_refuses_the_run(tmp_path: Path) -> None:
    """The variant list is compared as a whole, in order, not by membership."""
    payload = _registered()
    block = payload["trial_budget"]
    assert isinstance(block, dict)
    variants = block["variants"]
    assert isinstance(variants, list)
    block["variants"] = [*variants, "carry-something-else"]
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=r"trial_budget\.variants"):
        assert_no_drift(altered)


def test_declaring_that_nulls_are_charged_refuses_the_run(tmp_path: Path) -> None:
    """Charging nulls would make running more of them the expensive choice."""
    payload = _registered()
    _alter(payload, ("trial_budget", "nulls_are_charged"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="nulls_are_charged"):
        assert_no_drift(altered)


def test_the_whole_registered_grid_fits_the_budget_exactly() -> None:
    """Charging every trial the runner will run spends the allowance to zero."""
    ledger = BudgetLedger(budget=budget())
    for variant, cell in registered_grid():
        ledger.charge(variant, cell)
    assert ledger.spent == 32
    assert ledger.remaining == 0


def test_the_budget_refuses_a_variant_f1_did_not_register() -> None:
    """End to end: the specification's list is the list that can run."""
    ledger = BudgetLedger(budget=budget())
    with pytest.raises(UnregisteredTrial, match="carry-rank180-10"):
        ledger.charge("carry-rank180-10", "vip0_even")


# ---------------------------------------------------------------------------
# The constants the amendments fixed
# ---------------------------------------------------------------------------


def test_exactly_one_cell_is_the_headline() -> None:
    """Criteria are judged in one cell, and the guard checks there is one."""
    assert headline_cell().label == "vip0_even"
    assert sum(1 for cell in REGISTERED_CELLS if cell.is_headline) == 1


def test_the_headline_cell_is_not_the_cheapest_fill_mix() -> None:
    """Section 8's central choice, asserted rather than trusted to prose."""
    cheapest = max(REGISTERED_CELLS, key=lambda cell: cell.maker_fraction)
    assert headline_cell().label != cheapest.label
    assert headline_cell().maker_fraction == Decimal("0.50")


def test_the_registered_account_is_the_account_the_code_will_trade() -> None:
    registered = _registered()
    account = registered["account"]
    assert isinstance(account, dict)
    assert str(account["equity"]) == str(ACCOUNT_EQUITY.amount)
    assert Decimal(str(account["margin_fraction"])) == MARGIN_FRACTION


def test_the_stress_maintenance_margin_is_labelled_a_stress_level() -> None:
    """Amendment 17.2: a variant failing only at 0.025 is not rejected for it."""
    registered = _registered()
    margin = registered["maintenance_margin"]
    assert isinstance(margin, dict)
    settings = margin["settings"]
    assert isinstance(settings, list)
    stress = [dict(entry) for entry in settings if str(dict(entry)["rate"]) == "0.025"]
    assert len(stress) == 1
    assert str(stress[0]["status"]) == "stress"
    assert Decimal("0.025") == MAINTENANCE_MARGIN_STRESS
    assert MAINTENANCE_MARGINS[-1] == MAINTENANCE_MARGIN_STRESS
    assert "NOT A CANDIDATE RATE" in str(stress[0]["caveat"])


def test_the_maintenance_margin_is_registered_as_an_assumption_not_a_datum() -> None:
    """Amendment 16.3, including the direction of the bias."""
    registered = _registered()
    margin = registered["maintenance_margin"]
    assert isinstance(margin, dict)
    assert str(margin["status"]).startswith("assumption")
    assert margin["point_in_time_available"] is False
    assert "Optimistic" in str(margin["bias_direction"])


def test_the_depth_window_and_the_capacity_floor_are_the_registered_ones() -> None:
    """Rule C3's floor decides when capacity is reported as unestablished."""
    registered = _registered()
    capacity = registered["capacity"]
    assert isinstance(capacity, dict)
    window = capacity["depth_window"]
    assert isinstance(window, dict)
    assert str(window["starts"]) == "2023-01-01"
    assert str(window["ends"]) == "2024-05-17"
    rules = capacity["rules"]
    assert isinstance(rules, list)
    c3 = [dict(rule) for rule in rules if str(dict(rule)["id"]) == "C3"]
    assert int(str(c3[0]["minimum_depth_months"])) == MINIMUM_DEPTH_MONTHS


def test_the_recent_window_and_the_year_floor_come_from_one_place() -> None:
    """The config, the guard and the statistics module must agree on all three."""
    registered = _registered()
    decay = registered["decay"]
    assert isinstance(decay, dict)
    recent = decay["recent_window"]
    per_year = decay["per_calendar_year_table"]
    trend = decay["trend"]
    assert isinstance(recent, dict)
    assert isinstance(per_year, dict)
    assert isinstance(trend, dict)
    assert int(str(recent["months"])) == RECENT_WINDOW_MONTHS
    assert int(str(per_year["minimum_months_for_a_year"])) == MINIMUM_MONTHS_FOR_A_YEAR
    assert int(str(trend["seed"])) == BOOTSTRAP_SEED


def test_the_margin_buffer_sweep_is_the_approved_parameterisation() -> None:
    """Approved as the better parameterisation of stage 3, not as a fallback."""
    registered = _registered()
    leverage = registered["leverage"]
    assert isinstance(leverage, dict)
    sweep = leverage["margin_buffer_sweep"]
    assert isinstance(sweep, list)
    assert tuple(Decimal(str(item)) for item in sweep) == MARGIN_BUFFER_SWEEP
    assert str(leverage["spot_leg_sweep"]) == "not run"


def test_criterion_one_carries_the_sign_condition_spike_005_lacked() -> None:
    """The one substantive change to the criteria, and it is stricter."""
    registered = _registered()
    criteria = registered["criteria"]
    assert isinstance(criteria, dict)
    assert int(str(criteria["count"])) == 6
    entries = [dict(entry) for entry in list(criteria["registered"])]
    first = [entry for entry in entries if int(str(entry["id"])) == 1]
    assert first[0]["sign_condition_added"] is True
    assert "STRICTLY GREATER than zero" in str(first[0]["test"])
    assert criteria["no_criterion_is_looser_than_spike_005"] is True


def test_every_registered_variant_has_a_distinct_label() -> None:
    """The trial registry keys on it, so a duplicate would merge two trials."""
    labels = variant_labels()
    assert len(set(labels)) == len(labels) == len(REGISTERED_VARIANTS)


def test_a_drifted_execution_sensitivity_refuses_the_run(tmp_path: Path) -> None:
    """The sensitivity is checked as strictly as a grid cell, though nothing reads it.

    A sensitivity nobody verified is one that can quietly become the flattering number
    instead of the honest one, and this is the number that says whether a research
    finding could ever be harvested.
    """
    payload = _registered()
    _alter(payload, ("execution_sensitivity", "spot_taker_bps"), "10")
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="spot_taker_bps"):
        assert_no_drift(altered)


def test_declaring_the_sensitivity_a_grid_cell_refuses_the_run(tmp_path: Path) -> None:
    """It is outside the grid, and criterion 5 stays a four-cell test."""
    payload = _registered()
    _alter(payload, ("execution_sensitivity", "is_a_grid_cell"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="is_a_grid_cell"):
        assert_no_drift(altered)


def test_declaring_that_the_sensitivity_consumes_budget_refuses_the_run(tmp_path: Path) -> None:
    """The exemption rests on the re-cost being one-directional, not on convenience."""
    payload = _registered()
    _alter(payload, ("execution_sensitivity", "consumes_variant_budget"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="consumes_variant_budget"):
        assert_no_drift(altered)


def test_hiding_the_sensitivity_from_the_registry_refuses_the_run(tmp_path: Path) -> None:
    """It counts in the DSR trial count, where it can only raise the bar."""
    payload = _registered()
    _alter(payload, ("execution_sensitivity", "recorded_in_the_registry"), False)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="recorded_in_the_registry"):
        assert_no_drift(altered)


def test_letting_a_criterion_read_the_sensitivity_refuses_the_run(tmp_path: Path) -> None:
    """No Binance result becomes evidence about Kraken execution, by construction."""
    payload = _registered()
    _alter(payload, ("execution_sensitivity", "read_by_any_criterion"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="read_by_any_criterion"):
        assert_no_drift(altered)


def test_a_drifted_contraction_seed_refuses_the_run(tmp_path: Path) -> None:
    """One seed for every interval in this task, and the guard holds it."""
    payload = _registered()
    _alter(payload, ("contraction_check", "seed"), 1)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=r"contraction_check\.seed"):
        assert_no_drift(altered)


def test_a_fourth_contraction_attribute_refuses_the_run(tmp_path: Path) -> None:
    """Three registered attributes carry an interval. A fourth would be post-hoc."""
    payload = _registered()
    block = payload["contraction_check"]
    assert isinstance(block, dict)
    attributes = block["attributes"]
    assert isinstance(attributes, list)
    block["attributes"] = [*attributes, {"name": "something_noticed_later"}]
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=r"contraction_check\.attributes"):
        assert_no_drift(altered)

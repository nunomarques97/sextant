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
    CADENCE_PAIR,
    CONFIG_PATH,
    BAND_MINIMUM_SYMBOLS,
    BAND_RECUT_ALPHA,
    BAND_RECUT_PERMUTATIONS,
    BAND_RECUT_RULE,
    BAND_RECUT_SEED,
    BAND_SEPARATION_FACTOR,
    CRITERION_ONE_STRENGTHENED_FROM,
    EXTENDED_SAMPLE_DAYS,
    EXTENDED_SAMPLE_PER_BAND,
    EXTENDED_SAMPLE_RULE,
    FX_CROSSINGS_PER_RUN,
    HISTORICAL_QUOTES_RULE,
    SPREAD_BOUND_BPS,
    SPREAD_BOUND_RATIO,
    SPREAD_CONTINGENT,
    SPREAD_HEADLINE_BPS,
    SPREAD_LEVEL_BOUND,
    SPREAD_LEVEL_HEADLINE,
    ENGINE_VERSION,
    ESTIMATOR_COMPARISON_ID,
    ESTIMATOR_FACTOR_LOG2,
    ESTIMATOR_ID,
    ESTIMATOR_MINIMUM_PAIRS,
    ESTIMATOR_POSITIVE_SHARE,
    ESTIMATOR_RANK_FLOOR,
    ESTIMATOR_RULE,
    ESTIMATOR_TRAILING_DAYS,
    EXACT_RESCUE_TEST_FROM,
    FAMILY,
    FLOOR_ANCHOR,
    FLOOR_FROM,
    MAINTENANCE_MARGIN_STRESS,
    MAINTENANCE_MARGINS,
    MARGIN_BUFFER_SWEEP,
    MARGIN_FRACTION,
    MAXIMUM_RE_EXECUTIONS,
    MINIMUM_DEPTH_MONTHS,
    PAIRING_RULE,
    REGISTERED_CELLS,
    REGISTERED_VARIANTS,
    REGISTERED_VERSION,
    SPREAD_SAMPLE_SYMBOL_DAYS,
    SPREAD_TRIGGER_BAR,
    SPREAD_TRIGGER_RULE,
    SPREAD_TRIGGER_THRESHOLD,
    THINNER_EVIDENCE_VARIANT,
    VOID_DECLARED_BY_ROLE,
    VOID_NEVER_DECLARED_BY_ROLE,
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
    assert str(registered["version"]) == REGISTERED_VERSION
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
    """The declaration and the enforced grid are the same 36 trials."""
    declared = budget()
    assert declared.family == FAMILY
    assert declared.engine_version == ENGINE_VERSION
    assert declared.maximum_trials == 36
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
    assert ledger.spent == 36
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


# ---------------------------------------------------------------------------
# Amendment 6: the ninth variant, its cadence, and the pair it belongs to
# ---------------------------------------------------------------------------


def test_the_grid_has_a_point_slower_than_monthly() -> None:
    """The gap amendment 6 exists to close, asserted rather than described.

    Without a variant at a cadence slower than monthly the family would have been
    tested only in the turnover region the execution fee schedule already rules out,
    and a failure there would have been about the grid's design rather than the market.
    """
    cadences = {variant.rebalance_months for variant in REGISTERED_VARIANTS}
    assert cadences == {1, 3}
    slower = [v.label for v in REGISTERED_VARIANTS if v.rebalance_months > 1]
    assert slower == [THINNER_EVIDENCE_VARIANT]


def test_the_cadence_pair_differs_in_cadence_and_in_nothing_else() -> None:
    """A one-factor comparison is only one-factor if exactly one field differs."""
    by_label = {variant.label: variant for variant in REGISTERED_VARIANTS}
    monthly, quarterly = (by_label[label] for label in CADENCE_PAIR)
    assert monthly.signal == quarterly.signal
    assert monthly.positions == quarterly.positions
    assert monthly.require_positive == quarterly.require_positive
    assert (monthly.rebalance_months, quarterly.rebalance_months) == (1, 3)


def test_the_pair_is_named_in_the_registration_rather_than_inferred() -> None:
    """The reporter reads the pair from here, so it cannot pair the wrong two rows."""
    variants = _registered()["variants"]
    assert isinstance(variants, dict)
    pair = variants["one_factor_comparison"]
    assert isinstance(pair, dict)
    assert tuple(str(item) for item in pair["pair"]) == CADENCE_PAIR


def test_repairing_the_comparison_against_a_different_sibling_refuses_the_run(
    tmp_path: Path,
) -> None:
    """Swapping in a flattering sibling would silently make it a two-factor comparison."""
    payload = _registered()
    _alter(
        payload,
        ("variants", "one_factor_comparison", "pair"),
        ["carry-rank30-10", *CADENCE_PAIR[1:]],
    )
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="one_factor_comparison"):
        assert_no_drift(altered)


def test_claiming_a_second_difference_refuses_the_run(tmp_path: Path) -> None:
    """ "Cadence and lookback" is not the comparison that was registered."""
    payload = _registered()
    _alter(
        payload,
        ("variants", "one_factor_comparison", "differs_in"),
        ["rebalance_months", "signal"],
    )
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="differs_in"):
        assert_no_drift(altered)


def test_moving_the_rebalance_count_requirement_to_another_variant_refuses_the_run(
    tmp_path: Path,
) -> None:
    """The thin-evidence warning is worthless if the name it applies to can drift."""
    payload = _registered()
    _alter(payload, ("variants", "rebalance_count_reporting", "applies_to"), "carry-basket-5")
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="rebalance_count_reporting"):
        assert_no_drift(altered)


def test_quietly_speeding_the_quarterly_variant_up_refuses_the_run(tmp_path: Path) -> None:
    """The one number the whole amendment turns on."""
    payload = _registered()
    variants = payload["variants"]
    assert isinstance(variants, dict)
    registered = variants["registered"]
    assert isinstance(registered, list)
    ninth = registered[-1]
    assert isinstance(ninth, dict)
    assert ninth["id"] == THINNER_EVIDENCE_VARIANT
    ninth["rebalance_months"] = 1
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="rebalance_months"):
        assert_no_drift(altered)


def test_the_ninth_variant_costs_four_more_trials_and_no_more() -> None:
    """One variant, the same four cells, and no reserve behind the allowance."""
    declared = budget()
    assert declared.maximum_trials == len(REGISTERED_VARIANTS) * len(REGISTERED_CELLS) == 36
    assert THINNER_EVIDENCE_VARIANT in declared.variants


# ---------------------------------------------------------------------------
# Amendment 7: how a void execution is counted
# ---------------------------------------------------------------------------


def test_a_void_run_never_removes_rows_from_the_registry(tmp_path: Path) -> None:
    """The chain would not survive it, and the file's value is that it cannot be revised."""
    payload = _registered()
    _alter(payload, ("void_runs", "rows_are_deleted"), True)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="rows_are_deleted"):
        assert_no_drift(altered)


def test_the_deflation_never_reads_a_count_that_excludes_void_runs(tmp_path: Path) -> None:
    """The one edit that would turn an engineering repeat into a looser bar."""
    payload = _registered()
    _alter(payload, ("void_runs", "rows_are_counted_by_the_dsr"), False)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="rows_are_counted_by_the_dsr"):
        assert_no_drift(altered)


def test_raising_the_re_execution_ceiling_refuses_the_run(tmp_path: Path) -> None:
    """Two repeats, then the family is suspended and reported as (C)."""
    payload = _registered()
    _alter(payload, ("void_runs", "maximum_re_executions"), 5)
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="maximum_re_executions"):
        assert_no_drift(altered)


def test_the_developer_cannot_declare_a_run_void(tmp_path: Path) -> None:
    """It is the Product Owner's call, in writing, and the guard holds the role."""
    payload = _registered()
    _alter(payload, ("void_runs", "declared_by"), "the developer, at their discretion")
    altered = _write(payload, tmp_path / "spike-006-f1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="declared_by"):
        assert_no_drift(altered)


def test_the_void_rule_grants_no_trial_and_relaxes_no_threshold(tmp_path: Path) -> None:
    """A counting rule that could hand out a trial would be a budget with a back door."""
    for field in ("grants_no_trial", "relaxes_no_threshold"):
        payload = _registered()
        _alter(payload, ("void_runs", field), False)
        altered = _write(payload, tmp_path / f"{field}.yaml")
        with pytest.raises(DriftedFromPreRegistration, match=field):
            assert_no_drift(altered)


def test_the_ceiling_is_two_and_lives_in_one_place() -> None:
    assert MAXIMUM_RE_EXECUTIONS == 2


# ---------------------------------------------------------------------------
# Amendment 8: the declaring role, and rule S1 on the spread sample
# ---------------------------------------------------------------------------


def test_the_declaring_role_is_the_product_owner_and_never_the_developer() -> None:
    """Section 30.1, as two constants rather than one sentence of prose.

    A sentence outlives the conversation it was written in and resolves to whoever is
    reading it. Two guarded roles cannot.
    """
    assert VOID_DECLARED_BY_ROLE == "product-owner"
    assert VOID_NEVER_DECLARED_BY_ROLE == "developer"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("declared_by_role", "developer"),
        ("never_declared_by_role", "product-owner"),
    ],
)
def test_moving_the_declaring_role_to_the_developer_refuses_the_run(
    tmp_path: Path, field: str, value: str
) -> None:
    """Both directions of the one edit that would let a run be voided by its author."""
    payload = _registered()
    _alter(payload, ("void_runs", field), value)
    altered = _write(payload, tmp_path / f"{field}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=field):
        assert_no_drift(altered)


def test_the_declaring_prose_still_names_the_role_and_still_excludes_the_developer(
    tmp_path: Path,
) -> None:
    """The prose and the fields must agree; a reader reads the prose."""
    payload = _registered()
    _alter(payload, ("void_runs", "declared_by"), "whoever is holding the keyboard")
    altered = _write(payload, tmp_path / "prose.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="declared_by"):
        assert_no_drift(altered)


def test_rule_s1_is_registered_with_a_hard_zero_threshold() -> None:
    """Section 30.2. No margin around zero, because a margin is a chosen number."""
    assert SPREAD_TRIGGER_RULE == "S1"
    assert Decimal(0) == SPREAD_TRIGGER_THRESHOLD
    assert SPREAD_SAMPLE_SYMBOL_DAYS == 36


def test_softening_rule_s1_threshold_refuses_the_run(tmp_path: Path) -> None:
    """A trigger with a movable threshold is a decision deferred, not a decision made."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", "threshold"), "-0.05")
    altered = _write(payload, tmp_path / "threshold.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="threshold"):
        assert_no_drift(altered)


@pytest.mark.parametrize(
    ("field", "value"),
    [("acquire_if_true", False), ("acquire_if_false", True)],
)
def test_inverting_rule_s1_refuses_the_run(tmp_path: Path, field: str, value: bool) -> None:
    """Either inversion would decouple the download from the condition that justifies it."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", field), value)
    altered = _write(payload, tmp_path / f"{field}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=field):
        assert_no_drift(altered)


def test_rule_s1_excludes_the_execution_venue_cell(tmp_path: Path) -> None:
    """S1 is a condition at research fees, and Kraken's schedule is a different one."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", "cells_excluded"), [])
    altered = _write(payload, tmp_path / "cells.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="cells_excluded"):
        assert_no_drift(altered)


def test_growing_the_spread_sample_refuses_the_run(tmp_path: Path) -> None:
    """The size the rule implies is the size section 12 registered, and no larger."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", "size_if_acquired", "symbol_days"), 120)
    altered = _write(payload, tmp_path / "size.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="symbol_days"):
        assert_no_drift(altered)


# ---------------------------------------------------------------------------
# Amendment 9: rule S1 generalised, and the two findings that travel
# ---------------------------------------------------------------------------


def test_rule_s1_is_registered_against_criterion_one_and_not_against_a_sign() -> None:
    """Section 31.2. A sign change is not a rescue, and the bar says so."""
    assert SPREAD_TRIGGER_BAR == "criterion-1"
    assert EXACT_RESCUE_TEST_FROM == "F2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generalised_by", "amendment-8"),
        ("per_month_assumed_costs_required_from", "F4"),
    ],
)
def test_moving_amendment_nines_own_fields_refuses_the_run(
    tmp_path: Path, field: str, value: str
) -> None:
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", field), value)
    altered = _write(payload, tmp_path / f"{field}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=field):
        assert_no_drift(altered)


def test_softening_rule_s1_to_a_sign_change_refuses_the_run(tmp_path: Path) -> None:
    """The one edit that would turn the rule back into the version it replaced."""
    payload = _registered()
    _alter(
        payload,
        ("spread_sample", "acquisition", "condition"),
        "some variant earns a positive net return",
    )
    altered = _write(payload, tmp_path / "condition.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="criterion 1"):
        assert_no_drift(altered)


def test_the_currency_leg_may_never_be_folded_into_fees(tmp_path: Path) -> None:
    """Section 31.5's first finding, guarded so a later family cannot quietly drop it."""
    payload = _registered()
    _alter(
        payload,
        ("every_family_reports", "currency_leg_as_its_own_line", "rule"),
        "The FX conversion charge is reported with the other fees.",
    )
    altered = _write(payload, tmp_path / "currency.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="currency_leg"):
        assert_no_drift(altered)


def test_the_family_level_result_stays_the_headline(tmp_path: Path) -> None:
    """Section 31.5's second finding. A best-variant headline reads the other way."""
    payload = _registered()
    _alter(
        payload,
        ("every_family_reports", "family_level_result_is_the_headline", "rule"),
        "The headline figure for a family is its best variant's.",
    )
    altered = _write(payload, tmp_path / "headline.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="family_level"):
        assert_no_drift(altered)


# ---------------------------------------------------------------------------
# Amendment 10: the floor and the measured default, both prospective
# ---------------------------------------------------------------------------


def test_the_floor_and_the_measured_default_take_effect_at_f2() -> None:
    """Prospective on purpose: F1 is judged by the bar registered when it ran."""
    assert FLOOR_FROM == "F2"


@pytest.mark.parametrize("field", ["floor_from"])
def test_backdating_the_floor_refuses_the_run(tmp_path: Path, field: str) -> None:
    """Applying a bar retrospectively is amendment 9's defect in the other direction."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", field), "F1")
    altered = _write(payload, tmp_path / f"{field}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=field):
        assert_no_drift(altered)


def test_the_floor_must_stay_stated_in_standard_errors(tmp_path: Path) -> None:
    """A euro figure here would be a number chosen after seeing the euro figures."""
    payload = _registered()
    _alter(
        payload,
        ("spread_sample", "acquisition", "floor"),
        "the counterfactual must earn at least 500 EUR.",
    )
    altered = _write(payload, tmp_path / "floor.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="standard error"):
        assert_no_drift(altered)


def test_the_floor_must_stay_skew_and_kurtosis_corrected(tmp_path: Path) -> None:
    """The normal approximation is a floor on the uncertainty, not a measurement of it."""
    payload = _registered()
    _alter(
        payload,
        ("spread_sample", "acquisition", "floor"),
        "clear the null by one standard error of the Sharpe estimate.",
    )
    altered = _write(payload, tmp_path / "corrected.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="corrected"):
        assert_no_drift(altered)


def test_the_measured_spread_becomes_the_default_only_from_f2(tmp_path: Path) -> None:
    """F1 stays costed at the assumption in every cell, whatever was measured."""
    payload = _registered()
    _alter(
        payload,
        ("every_family_reports", "measured_spread_is_the_default_from_f2", "rule"),
        "The measured spread replaces the assumption everywhere, including F1.",
    )
    altered = _write(payload, tmp_path / "default.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="measured_spread"):
        assert_no_drift(altered)


# ---------------------------------------------------------------------------
# Amendment 11: the floor anchored to zero, rule P1, rule E1
# ---------------------------------------------------------------------------


def test_the_floor_is_anchored_to_zero_and_not_to_the_null() -> None:
    """The null loses over this window, so a bar anchored to it is a bar below zero."""
    assert FLOOR_ANCHOR == "zero"
    registered = _registered()
    acquisition = registered["spread_sample"]["acquisition"]  # type: ignore[index]
    settled = str(acquisition["floor_as_settled"])
    assert "exceeding ZERO by at least one standard error" in settled
    assert "BOTH clauses" in settled


def test_reanchoring_the_floor_to_the_null_refuses_the_run(tmp_path: Path) -> None:
    """The settled anchor is a registered value, not a preference restatable later."""
    payload = _registered()
    _alter(payload, ("spread_sample", "acquisition", "floor_anchor"), "the null")
    altered = _write(payload, tmp_path / "anchor.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="floor_anchor"):
        assert_no_drift(altered)


def test_dropping_one_of_the_floors_two_clauses_refuses_the_run(tmp_path: Path) -> None:
    """Both clauses, never either. An "or" here is a bar that passes twice as much."""
    payload = _registered()
    _alter(
        payload,
        ("spread_sample", "acquisition", "floor_as_settled"),
        "the counterfactual Sharpe must exceed ZERO by at least one standard error.",
    )
    altered = _write(payload, tmp_path / "clauses.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="both clauses"):
        assert_no_drift(altered)


def test_amendment_ten_is_superseded_and_still_in_the_record() -> None:
    """Both forms of the floor stay registered: one as written, one as it applies."""
    registered = _registered()
    acquisition = registered["spread_sample"]["acquisition"]  # type: ignore[index]
    assert "floor" in acquisition
    assert "amendment 10" in str(acquisition["floor_supersedes"])
    assert "would have fired" in str(acquisition["floor_supersedes"])


def test_rule_p1_pairs_every_null_comparison_with_an_absolute_test() -> None:
    """Registered at the level of the shape, because the defect recurred in three places."""
    assert PAIRING_RULE == "P1"
    registered = _registered()
    block = registered["absolute_pairing"]
    assert isinstance(block, dict)
    assert str(block["applies_from"]) == "F2"
    assert block["is_a_trial"] is False
    assert "ABSOLUTE test" in str(block["rule"])


def test_criterion_one_is_strengthened_from_f2_and_not_before() -> None:
    """F1 was judged on the weaker form and keeps it."""
    assert CRITERION_ONE_STRENGTHENED_FROM == "F2"


@pytest.mark.parametrize("value", ["F1", "F3"])
def test_moving_the_strengthened_criterion_off_f2_refuses_the_run(
    tmp_path: Path, value: str
) -> None:
    """Backdating it re-scores F1; postdating it lets a family through on the weak form."""
    payload = _registered()
    _alter(payload, ("absolute_pairing", "criterion_1_strengthened", "applies_from"), value)
    altered = _write(payload, tmp_path / f"strengthened-{value}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="criterion_1_strengthened"):
        assert_no_drift(altered)


def test_the_strengthened_clause_must_stay_stated_in_standard_errors(tmp_path: Path) -> None:
    """A euro figure, or a Sharpe figure, would be a number chosen after the fact."""
    payload = _registered()
    _alter(
        payload,
        ("absolute_pairing", "criterion_1_strengthened", "as_strengthened"),
        "a net return of at least 500 EUR over the scored window.",
    )
    altered = _write(payload, tmp_path / "strengthened.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="one standard error"):
        assert_no_drift(altered)


def test_the_narrowing_justification_cannot_leave_the_registration(tmp_path: Path) -> None:
    """ "Can only ever remove a pass" IS the licence to register this after F1's figures."""
    payload = _registered()
    _alter(
        payload,
        ("absolute_pairing", "criterion_1_strengthened", "can_only_ever_remove_a_pass"),
        "The strengthened clause changes which variants pass.",
    )
    altered = _write(payload, tmp_path / "narrowing.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="remove a pass"):
        assert_no_drift(altered)


def test_f1_is_re_reported_and_never_re_scored(tmp_path: Path) -> None:
    """A supplementary reading is not a re-scoring, and the word is load-bearing."""
    payload = _registered()
    _alter(
        payload,
        ("absolute_pairing", "criterion_1_strengthened", "not_applied_to_f1"),
        "F1 is re-scored against the strengthened criterion.",
    )
    altered = _write(payload, tmp_path / "supplementary.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="not_applied_to_f1"):
        assert_no_drift(altered)


def test_rule_e1_names_one_estimator_and_one_comparison() -> None:
    """The registered estimator is fixed before the calibration, and so is its rival's role."""
    assert ESTIMATOR_RULE == "E1"
    assert ESTIMATOR_ID == "abdi-ranaldo-2017"
    assert ESTIMATOR_COMPARISON_ID == "corwin-schultz-2012"
    registered = _registered()
    block = registered["spread_estimator"]
    assert isinstance(block, dict)
    assert str(block["applies_from"]) == "F2"
    assert block["is_a_trial"] is False
    comparison = block["comparison_estimator"]
    assert isinstance(comparison, dict)
    assert str(comparison["computed_for"]) == "comparison only"


def test_promoting_the_comparison_estimator_refuses_the_run(tmp_path: Path) -> None:
    """Adopting whichever passes, after seeing which passed, is selection."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "comparison_estimator", "never_substituted"),
        "Corwin-Schultz is adopted if Abdi-Ranaldo fails.",
    )
    altered = _write(payload, tmp_path / "promote.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="never_substituted"):
        assert_no_drift(altered)


def test_swapping_the_registered_estimator_refuses_the_run(tmp_path: Path) -> None:
    """Which estimator was chosen is part of what was pre-registered."""
    payload = _registered()
    _alter(payload, ("spread_estimator", "registered_estimator", "id"), "corwin-schultz-2012")
    altered = _write(payload, tmp_path / "swap.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="registered_estimator"):
        assert_no_drift(altered)


def test_rule_e1_thresholds_are_the_registered_ones() -> None:
    """Three clauses, three numbers, each read out of the prose that justifies it."""
    assert Decimal("0.771") == ESTIMATOR_RANK_FLOOR
    assert Decimal(1) == ESTIMATOR_FACTOR_LOG2
    assert Decimal("0.90") == ESTIMATOR_POSITIVE_SHARE
    assert ESTIMATOR_TRAILING_DAYS == 30
    assert ESTIMATOR_MINIMUM_PAIRS == 20


@pytest.mark.parametrize(
    ("clause", "softened"),
    [
        (
            "ordering",
            "the Spearman rank correlation across the six symbols is at least 0.2, which is "
            "a low bar and is meant to be.",
        ),
        (
            "magnitude",
            "the median across the six symbols of the absolute base-2 logarithm of estimated "
            "over measured is at most 4: within a factor of sixteen.",
        ),
        (
            "positivity",
            "the estimator returns a strictly positive figure for at least 10 per cent of the "
            "instrument-periods asked of it.",
        ),
    ],
)
def test_softening_any_of_rule_e1s_clauses_refuses_the_run(
    tmp_path: Path, clause: str, softened: str
) -> None:
    """Every threshold was committed before the calibration that reads it was run."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "acceptance", "clauses_all_of_which_must_hold", clause),
        softened,
    )
    altered = _write(payload, tmp_path / f"{clause}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=clause):
        assert_no_drift(altered)


def test_a_clause_that_no_longer_states_its_threshold_refuses_the_run(tmp_path: Path) -> None:
    """A threshold read out of its own justification cannot drift away from it silently."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "acceptance", "clauses_all_of_which_must_hold", "ordering"),
        "the estimator must rank the symbols acceptably well.",
    )
    altered = _write(payload, tmp_path / "unstated.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="at least"):
        assert_no_drift(altered)


def test_failing_rule_e1_keeps_the_assumption_rather_than_a_worse_number(tmp_path: Path) -> None:
    """A guess labelled a guess beats a worse number that looks like a measurement."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "acceptance", "if_it_fails"),
        "the closest available estimate is adopted anyway, so that a figure exists.",
    )
    altered = _write(payload, tmp_path / "fails.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="if_it_fails"):
        assert_no_drift(altered)


def test_the_estimate_is_charged_point_in_time_from_a_trailing_window(tmp_path: Path) -> None:
    """Estimating from the period a trade falls in prices the trade with its own month."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "application_from_f2", "point_in_time"),
        "The figure charged is estimated from the 30 daily bars of the calendar month the "
        "decision falls in.",
    )
    altered = _write(payload, tmp_path / "pit.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="point_in_time"):
        assert_no_drift(altered)


def test_shortening_the_trailing_window_refuses_the_run(tmp_path: Path) -> None:
    """Thirty days is the window the liquidity bands are already cut on, not a taste."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "application_from_f2", "point_in_time"),
        "The figure charged at a decision instant is estimated from the 5 stored daily bars "
        "ending STRICTLY BEFORE that instant.",
    )
    altered = _write(payload, tmp_path / "shorter.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="trailing days"):
        assert_no_drift(altered)


def test_an_instrument_with_too_little_history_is_never_charged_zero(tmp_path: Path) -> None:
    """It falls back to the labelled assumption, and the count of fallbacks is reported."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "application_from_f2", "insufficient_history"),
        "an instrument with fewer than 2 usable two-day pairs is charged at the assumption.",
    )
    altered = _write(payload, tmp_path / "history.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="minimum pairs"):
        assert_no_drift(altered)


def test_slippage_is_not_estimated_by_anything_here(tmp_path: Path) -> None:
    """Daily bars cannot calibrate an intraday quantity, and nothing pretends otherwise."""
    payload = _registered()
    _alter(
        payload,
        ("spread_estimator", "application_from_f2", "slippage_is_untouched"),
        "Slippage is estimated by the same estimator, at half the spread.",
    )
    altered = _write(payload, tmp_path / "slippage.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="slippage_is_untouched"):
        assert_no_drift(altered)


# ---------------------------------------------------------------------------
# Amendment 12: the settlement of section 33.7
# ---------------------------------------------------------------------------


def test_two_spread_levels_and_only_one_of_them_decides_anything() -> None:
    """The assumption is kept as the criterion; the measurement is reported beside it."""
    assert SPREAD_HEADLINE_BPS == Decimal(10)
    assert SPREAD_BOUND_BPS == Decimal("0.53")
    assert SPREAD_LEVEL_HEADLINE != SPREAD_LEVEL_BOUND
    registered = _registered()
    levels = registered["spread_levels"]
    assert isinstance(levels, dict)
    assert str(levels["applies_from"]) == "F2"
    assert levels["is_a_trial"] is False


def test_the_ratio_the_upper_bound_must_be_labelled_with_is_registered() -> None:
    """A label that cannot carry the multiple lets a bound read as a measurement."""
    assert SPREAD_BOUND_RATIO == Decimal("18.87")
    computed = SPREAD_HEADLINE_BPS / SPREAD_BOUND_BPS
    assert abs(computed - SPREAD_BOUND_RATIO) < Decimal("0.01")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("spread_levels", "headline", "deep_bps"), "0.53"),
        (("spread_levels", "bound", "deep_bps"), "10"),
    ],
)
def test_swapping_the_two_spread_levels_refuses_the_run(
    tmp_path: Path, path: tuple[str, ...], value: str
) -> None:
    """The flattering swap: the measurement as the criterion, the assumption as colour."""
    payload = _registered()
    _alter(payload, path, value)
    altered = _write(payload, tmp_path / "levels.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="deep_bps"):
        assert_no_drift(altered)


def test_letting_a_criterion_read_the_bound_refuses_the_run(tmp_path: Path) -> None:
    """A measurement this project cannot date must never become a verdict."""
    payload = _registered()
    _alter(
        payload,
        ("spread_levels", "bound", "never_a_criterion"),
        "The bound is used as the criterion where it is the more realistic figure.",
    )
    altered = _write(payload, tmp_path / "bound.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="never_a_criterion"):
        assert_no_drift(altered)


def test_showing_one_level_without_the_other_refuses_the_run(tmp_path: Path) -> None:
    """The whole point of two levels is that a reader sees both of them."""
    payload = _registered()
    _alter(
        payload,
        ("spread_levels", "both_or_neither"),
        "The bound is reported where it is informative.",
    )
    altered = _write(payload, tmp_path / "both.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="both_or_neither"):
        assert_no_drift(altered)


def test_the_spread_contingent_outcome_is_registered_before_any_family_can_produce_it() -> None:
    """Deferred, not failed and not promoted. Registered now so it cannot be invented."""
    assert SPREAD_CONTINGENT == "spread-contingent"
    registered = _registered()
    block = registered["spread_contingent_verdict"]
    assert isinstance(block, dict)
    assert str(block["applies_from"]) == "F2"
    assert str(block["fails_at_both"]) == "closed normally"


def test_promoting_a_spread_contingent_family_refuses_the_run(tmp_path: Path) -> None:
    """It is a deferral. A family that clears only at the bound has not cleared."""
    payload = _registered()
    _alter(
        payload,
        ("spread_contingent_verdict", "definition"),
        "A family that FAILS its criteria at the headline and clears them at the bound is "
        "recorded as a pass.",
    )
    altered = _write(payload, tmp_path / "contingent.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="deferral"):
        assert_no_drift(altered)


def test_the_contingent_rule_must_cut_in_both_directions(tmp_path: Path) -> None:
    """It stops the assumption manufacturing a failure AND the measurement a success."""
    payload = _registered()
    _alter(
        payload,
        ("spread_contingent_verdict", "what_it_prevents"),
        "It stops a conservative assumption from killing a family that would work.",
    )
    altered = _write(payload, tmp_path / "directions.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="both directions"):
        assert_no_drift(altered)


def test_rule_b1s_thresholds_are_the_registered_ones() -> None:
    """A procedure whose thresholds move after the data is seen is not a procedure."""
    assert BAND_RECUT_RULE == "B1"
    assert BAND_RECUT_PERMUTATIONS == 10_000
    assert BAND_RECUT_SEED == 20260911
    assert BAND_RECUT_ALPHA == Decimal("0.05")
    assert BAND_MINIMUM_SYMBOLS == 3
    assert BAND_SEPARATION_FACTOR == Decimal(2)


@pytest.mark.parametrize(
    ("field", "softened", "match"),
    [
        (
            "procedure",
            "compute Spearman's rank correlation against the measured median quoted "
            "half-spread, and its two-sided permutation p-value from 50 permutations at "
            "seed 20260911. The cut quantity is the candidate with the largest absolute "
            "rank correlation among those whose p-value is below 0.05.",
            "permutations",
        ),
        (
            "procedure",
            "compute Spearman's rank correlation against the measured median quoted "
            "half-spread, and its two-sided permutation p-value from 10000 permutations at "
            "seed 20260911. The cut quantity is the candidate with the largest absolute "
            "rank correlation among those whose p-value is below 0.5.",
            "alpha",
        ),
        (
            "how_many_bands",
            "adopt the LARGEST number for which every band holds at least 1 sampled symbol "
            "and adjacent bands' measured median half-spreads differ by at least a factor "
            "of 2.",
            "minimum_symbols",
        ),
        (
            "how_many_bands",
            "adopt the LARGEST number for which every band holds at least 3 sampled symbols "
            "and adjacent bands' measured median half-spreads differ by at least a factor "
            "of 1.05.",
            "separation",
        ),
    ],
)
def test_softening_rule_b1_refuses_the_run(
    tmp_path: Path, field: str, softened: str, match: str
) -> None:
    """Fewer permutations, a looser alpha, a band of one, a separation of nothing."""
    payload = _registered()
    _alter(payload, ("band_recut", field), softened)
    altered = _write(payload, tmp_path / f"b1-{match}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=match):
        assert_no_drift(altered)


def test_keeping_three_bands_because_there_are_three_refuses_the_run(tmp_path: Path) -> None:
    """A partition that does not partition carries authority it has not got."""
    payload = _registered()
    _alter(
        payload,
        ("band_recut", "collapse_is_an_allowed_answer"),
        "Three bands are retained so that the cost model keeps its existing shape.",
    )
    altered = _write(payload, tmp_path / "collapse.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="collapse"):
        assert_no_drift(altered)


def test_dropping_a_candidate_quantity_refuses_the_run(tmp_path: Path) -> None:
    """Which quantities were considered is part of what was registered."""
    payload = _registered()
    candidates = payload["band_recut"]["candidates"]  # type: ignore[index]
    assert isinstance(candidates, list)
    _alter(payload, ("band_recut", "candidates"), candidates[:2])
    altered = _write(payload, tmp_path / "candidates.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="candidates"):
        assert_no_drift(altered)


def test_rule_m1_measures_four_symbols_a_band_over_six_days() -> None:
    """Rule S1's own protocol, in the two bands rule S1 never reached."""
    assert EXTENDED_SAMPLE_RULE == "M1"
    assert EXTENDED_SAMPLE_PER_BAND == 4
    assert EXTENDED_SAMPLE_DAYS == 6


@pytest.mark.parametrize(
    ("softened", "match"),
    [
        (
            "At least 1 symbol in each of the mid and thin bands, over the same protocol as "
            "rule S1, and over at least the same 6 days.",
            "per_band",
        ),
        (
            "At least 4 symbols in each of the mid and thin bands, over the same protocol as "
            "rule S1, and over at least the same 1 days.",
            "days",
        ),
    ],
)
def test_shrinking_rule_m1_refuses_the_run(tmp_path: Path, softened: str, match: str) -> None:
    """One symbol is not a band and one day is not a protocol."""
    payload = _registered()
    _alter(payload, ("extended_spread_sample", "requirement"), softened)
    altered = _write(payload, tmp_path / f"m1-{match}.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=match):
        assert_no_drift(altered)


def test_occupancy_is_decided_before_anything_is_downloaded(tmp_path: Path) -> None:
    """A band nobody trades needs no measurement, and that is computable in advance."""
    payload = _registered()
    _alter(
        payload,
        ("extended_spread_sample", "occupancy_is_computed_first"),
        "Band membership is decided once the sample has been acquired.",
    )
    altered = _write(payload, tmp_path / "occupancy.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="occupancy"):
        assert_no_drift(altered)


def test_an_empty_band_stays_an_answer_rather_than_a_gap(tmp_path: Path) -> None:
    """Measuring instruments the strategy would never touch is not a better answer."""
    payload = _registered()
    _alter(
        payload,
        ("extended_spread_sample", "an_empty_band_is_the_answer"),
        "If the universe contains no symbols in a band, sample the nearest ones outside it.",
    )
    altered = _write(payload, tmp_path / "empty.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="empty_band"):
        assert_no_drift(altered)


def test_rule_h1_fires_only_on_a_spread_contingent_family() -> None:
    """Three working days of acquisition, on a condition and never speculatively."""
    assert HISTORICAL_QUOTES_RULE == "H1"
    registered = _registered()
    block = registered["historical_quoted_spread"]
    assert isinstance(block, dict)
    assert SPREAD_CONTINGENT in str(block["fires_only_if"])
    assert block["is_a_trial"] is False


def test_making_rule_h1_unconditional_refuses_the_run(tmp_path: Path) -> None:
    """An acquisition with no condition on it is an acquisition nobody decided."""
    payload = _registered()
    _alter(
        payload,
        ("historical_quoted_spread", "fires_only_if"),
        "always, so that the data is there when it is wanted.",
    )
    altered = _write(payload, tmp_path / "h1.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="fires_only_if"):
        assert_no_drift(altered)


def test_a_missing_datum_is_named_rather_than_substituted(tmp_path: Path) -> None:
    """Undetermined with the gap named beats a number standing in for the gap."""
    payload = _registered()
    _alter(
        payload,
        ("historical_quoted_spread", "if_it_does_not_exist_for_the_window"),
        "Use the measured present-day spread for the missing window.",
    )
    altered = _write(payload, tmp_path / "substitute.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="does_not_exist"):
        assert_no_drift(altered)


def test_the_bound_is_a_sensitivity_and_not_a_trial() -> None:
    """A second level that counted as a trial would make honest reporting expensive."""
    registered = _registered()
    block = registered["bound_is_not_a_trial"]
    assert isinstance(block, dict)
    assert str(block["applies_from"]) == "F2"
    conditions = block["conditions_under_which_that_holds"]
    assert isinstance(conditions, list)
    assert len(conditions) == 3


def test_selecting_a_variant_on_the_bound_refuses_the_run(tmp_path: Path) -> None:
    """The back door the trial-accounting rule exists to close."""
    payload = _registered()
    conditions = payload["bound_is_not_a_trial"]["conditions_under_which_that_holds"]  # type: ignore[index]
    assert isinstance(conditions, list)
    _alter(
        payload,
        ("bound_is_not_a_trial", "conditions_under_which_that_holds"),
        ["variants may be retained on whichever level is kinder", *conditions[1:]],
    )
    altered = _write(payload, tmp_path / "select.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="conditions"):
        assert_no_drift(altered)


def test_the_fx_invariant_is_registered_with_its_crossing_count() -> None:
    """Once in and once out. Everything between is one currency."""
    assert FX_CROSSINGS_PER_RUN == 2
    registered = _registered()
    block = registered["fx_crossing_invariant"]
    assert isinstance(block, dict)
    assert block["is_a_trial"] is False
    assert "must not scale with turnover" in str(block["invariant"])


def test_letting_the_fx_charge_scale_with_turnover_refuses_the_run(tmp_path: Path) -> None:
    """The defect the invariant exists to catch cannot be registered as the rule."""
    payload = _registered()
    _alter(
        payload,
        ("fx_crossing_invariant", "invariant"),
        "The FX conversion charge is applied to every trade, in proportion to turnover.",
    )
    altered = _write(payload, tmp_path / "fx.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="turnover"):
        assert_no_drift(altered)


def test_raising_the_crossing_count_refuses_the_run(tmp_path: Path) -> None:
    """Two is not a tuning parameter; it is how many times capital changes currency."""
    payload = _registered()
    _alter(payload, ("fx_crossing_invariant", "crossings_per_run"), 8)
    altered = _write(payload, tmp_path / "crossings.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="crossings_per_run"):
        assert_no_drift(altered)


def test_calling_the_upper_bound_an_estimate_refuses_the_run(tmp_path: Path) -> None:
    """A downstream reader must not be able to mistake it for something measured."""
    payload = _registered()
    _alter(
        payload,
        ("relabel_the_assumption", "rule"),
        "The 10 bps figure is described as a conservative estimate of the half-spread.",
    )
    altered = _write(payload, tmp_path / "relabel.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="approximately"):
        assert_no_drift(altered)


def test_dropping_the_ratio_from_the_headline_label_refuses_the_run(tmp_path: Path) -> None:
    """The label is what stops an upper bound reading as a measurement downstream."""
    payload = _registered()
    _alter(
        payload,
        ("spread_levels", "headline", "label_required_everywhere"),
        "A conservative half-spread for the deep band.",
    )
    altered = _write(payload, tmp_path / "label.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="UPPER BOUND|approximately"):
        assert_no_drift(altered)


def test_amending_f1s_verdict_may_not_edit_its_original_text(tmp_path: Path) -> None:
    """A correction that rewrites the thing it corrects leaves no record of either."""
    payload = _registered()
    _alter(
        payload,
        ("relabel_the_assumption", "f1_verdict_amended_not_edited"),
        "F1's results document is updated in place with the corrected figures.",
    )
    altered = _write(payload, tmp_path / "amend.yaml")
    with pytest.raises(DriftedFromPreRegistration, match="f1_verdict"):
        assert_no_drift(altered)

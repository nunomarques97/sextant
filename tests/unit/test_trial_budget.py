"""The budget that says no, and the three ways a search widens after a result.

A pre-registration that declares eight variants and relies on discipline to stop
at eight has registered nothing enforceable. These tests are about the refusals:
that a ninth variant cannot run, that an unregistered cost cell cannot run, that
the allowance cannot be exceeded, and that none of it can be worked around by
rerunning, reordering or reaching for an override that does not exist.

The test that matters most is the one about the *cheap* violation:
``test_a_cheaper_cell_invented_after_a_failure_is_refused``. Widening the grid by
one cheap cell after the headline cell disappointed is the single most likely way
this project could fool itself, and it costs nothing to do. It has to raise.
"""

from __future__ import annotations

import pytest

from sextant.domain.time import Timestamp
from sextant.engine.backtest.budget import (
    BudgetExhausted,
    BudgetLedger,
    BudgetViolation,
    TrialBudget,
    UnregisteredTrial,
    budget_from,
    consumption_from,
    ledger_for,
)
from sextant.engine.backtest.trials import GENESIS, TrialRecord, build_record

VARIANTS = ("alpha", "beta", "gamma")
CELLS = ("headline", "stress")
WHEN = Timestamp.parse("2026-09-10T00:00:00+00:00")


def a_budget(
    *,
    variants: tuple[str, ...] = VARIANTS,
    cells: tuple[str, ...] = CELLS,
    maximum: int | None = None,
) -> TrialBudget:
    """A budget whose maximum defaults to the exact grid, as a real one must."""
    return budget_from(
        family="FX",
        engine_version="test-family",
        variants=variants,
        cost_cells=cells,
        maximum_trials=len(variants) * len(cells) if maximum is None else maximum,
    )


def a_record(
    *,
    strategy: str,
    parameters: str,
    null: bool = False,
    engine_version: str = "test-family",
) -> TrialRecord:
    """One registry record, enough of one to be counted."""
    return build_record(
        recorded_at=WHEN,
        code_version="abc123",
        engine_version=engine_version,
        strategy_id=strategy,
        parameter_set_id=parameters,
        dataset_checksum="dataset",
        evaluation_window="2020-01-01/2026-01-01",
        quote_policy="USDT",
        is_null_construct=null,
        seeds=1,
        note="test",
        previous_hash=GENESIS,
    )


# ---------------------------------------------------------------------------
# The declaration itself
# ---------------------------------------------------------------------------


def test_the_maximum_must_be_the_exact_grid_and_not_a_ceiling() -> None:
    """The rule that stops a reserve existing at all.

    Section 3 of the F1 registration declares the exact grid rather than a
    ceiling, because a reserve is what gets spent after a result is seen. The type
    enforces it, so no family can register four variants and quietly hold four
    back.
    """
    with pytest.raises(BudgetViolation, match="reserve"):
        a_budget(maximum=12)


def test_a_maximum_below_the_grid_is_also_refused() -> None:
    """Under-declaring is a mismatch too: the runner would stop mid-grid."""
    with pytest.raises(BudgetViolation, match="does not equal"):
        a_budget(maximum=4)


def test_a_budget_with_no_variants_registers_nothing() -> None:
    with pytest.raises(BudgetViolation, match="no variants"):
        budget_from(
            family="FX", engine_version="v", variants=(), cost_cells=CELLS, maximum_trials=0
        )


def test_a_duplicated_variant_is_refused() -> None:
    """Otherwise the product overstates the grid and the allowance inflates."""
    with pytest.raises(BudgetViolation, match="registered twice"):
        a_budget(variants=("alpha", "alpha", "beta"), maximum=6)


def test_the_registered_trial_count_is_the_product() -> None:
    budget = a_budget()
    assert budget.registered_trials == len(VARIANTS) * len(CELLS)
    assert budget.maximum_trials == budget.registered_trials


# ---------------------------------------------------------------------------
# The three refusals
# ---------------------------------------------------------------------------


def test_a_ninth_variant_is_refused_even_with_the_allowance_untouched() -> None:
    """Budget remaining is not permission. The variant was never registered."""
    ledger = BudgetLedger(budget=a_budget())
    with pytest.raises(UnregisteredTrial, match="delta"):
        ledger.charge("delta", "headline")
    assert ledger.spent == 0


def test_a_cheaper_cell_invented_after_a_failure_is_refused() -> None:
    """The cheap violation, and the one most likely to be attempted.

    The headline cell disappoints, so somebody adds a cell with lower fees and
    reports that instead. It costs nothing to try and it must not be possible.
    """
    ledger = BudgetLedger(budget=a_budget())
    with pytest.raises(UnregisteredTrial, match="vip0_free"):
        ledger.charge("alpha", "vip0_free")


def test_the_whole_registered_grid_fits_exactly_and_leaves_nothing() -> None:
    """Spend the exact grid and the allowance is gone, to the trial."""
    budget = a_budget()
    ledger = BudgetLedger(budget=budget)
    for cell in CELLS:
        for variant in VARIANTS:
            ledger.charge(variant, cell)
    assert ledger.spent == budget.maximum_trials
    assert ledger.remaining == 0
    assert ledger.is_exhausted is True


def test_prior_post_hoc_trials_reduce_the_allowance_and_can_close_the_family() -> None:
    """The one path by which the allowance genuinely runs out.

    A registry that already holds trials this family never registered means the
    search was already wider than the registration says. Those trials reduce what
    is left rather than being ignored, because they inflate the multiplicity the
    Deflated Sharpe Ratio has to deflate for, and a family does not get to spend
    its whole declared budget on top of whatever it already tried.
    """
    budget = a_budget(variants=("alpha",), cells=("headline",))
    ledger = BudgetLedger(budget=budget, already_spent=1)
    assert ledger.is_exhausted is True
    with pytest.raises(BudgetExhausted, match="closed"):
        ledger.charge("alpha", "headline")


def test_a_ledger_opened_from_the_registry_seeds_only_the_post_hoc_count() -> None:
    """A rerun of the registered grid must stay free, or nothing is reproducible."""
    budget = a_budget()
    already_run = [a_record(strategy=variant, parameters="cell=headline") for variant in VARIANTS]
    ledger = ledger_for(budget, consumption_from(budget, already_run))
    assert ledger.already_spent == 0
    for cell in CELLS:
        for variant in VARIANTS:
            ledger.charge(variant, cell)
    assert ledger.spent == budget.maximum_trials


def test_a_ledger_opened_over_a_polluted_registry_starts_with_less() -> None:
    """One unregistered trial in the file, one fewer registered trial available."""
    budget = a_budget()
    records = [
        a_record(strategy="alpha", parameters="cell=headline"),
        a_record(strategy="delta-invented-later", parameters="cell=headline"),
    ]
    ledger = ledger_for(budget, consumption_from(budget, records))
    assert ledger.already_spent == 1
    assert ledger.remaining == budget.maximum_trials - 1


def test_a_ledger_refuses_a_consumption_measured_against_another_budget() -> None:
    """Otherwise the seeded number would describe a different family."""
    mine = a_budget()
    theirs = budget_from(
        family="FY",
        engine_version="other-family",
        variants=("one",),
        cost_cells=("only",),
        maximum_trials=1,
    )
    with pytest.raises(BudgetViolation, match="different budget"):
        ledger_for(mine, consumption_from(theirs, []))


# ---------------------------------------------------------------------------
# Rerunning, which must not spend twice
# ---------------------------------------------------------------------------


def test_charging_the_same_pair_twice_spends_once() -> None:
    """A rerun reproduces a result. It must not cost budget to reproduce one."""
    ledger = BudgetLedger(budget=a_budget())
    ledger.charge("alpha", "headline")
    ledger.charge("alpha", "headline")
    ledger.charge("alpha", "headline")
    assert ledger.spent == 1


def test_the_whole_grid_rerun_spends_the_allowance_once() -> None:
    budget = a_budget()
    ledger = BudgetLedger(budget=budget)
    for _ in range(3):
        for cell in CELLS:
            for variant in VARIANTS:
                ledger.charge(variant, cell)
    assert ledger.spent == budget.maximum_trials


def test_the_charged_pairs_are_reported_in_a_stable_order() -> None:
    """So a report's budget table does not reorder between runs."""
    ledger = BudgetLedger(budget=a_budget())
    ledger.charge("gamma", "stress")
    ledger.charge("alpha", "headline")
    assert ledger.charged == (("alpha", "headline"), ("gamma", "stress"))


# ---------------------------------------------------------------------------
# What the committed registry says was spent
# ---------------------------------------------------------------------------


def test_consumption_is_recomputed_from_the_registry_not_from_the_run() -> None:
    """So the figure in the report is one a reader can reproduce from the file."""
    budget = a_budget()
    records = [
        a_record(strategy="alpha", parameters="cell=headline"),
        a_record(strategy="beta", parameters="cell=headline"),
        a_record(strategy="alpha", parameters="cell=stress"),
    ]
    consumption = consumption_from(budget, records)
    assert consumption.spent == 3
    assert consumption.remaining == budget.maximum_trials - 3
    assert consumption.post_hoc == ()
    assert consumption.is_overspent is False


def test_nulls_are_counted_but_never_charged() -> None:
    """Charging them would make running more nulls the expensive choice."""
    budget = a_budget()
    records = [
        a_record(strategy="alpha", parameters="cell=headline"),
        a_record(strategy="alpha/timing-null", parameters="cell=headline", null=True),
        a_record(strategy="random-selection", parameters="cell=headline", null=True),
    ]
    consumption = consumption_from(budget, records)
    assert consumption.spent == 1
    assert consumption.nulls == 2
    assert consumption.post_hoc == ()


def test_a_trial_outside_the_registration_is_reported_as_post_hoc() -> None:
    """It counts in the registry either way; what changes is that it is named.

    This is the state the ledger's refusal is supposed to prevent, so if it ever
    appears in the registry the report has to say so rather than fold it into the
    variant count.
    """
    budget = a_budget()
    records = [
        a_record(strategy="alpha", parameters="cell=headline"),
        a_record(strategy="delta-invented-later", parameters="cell=headline"),
    ]
    consumption = consumption_from(budget, records)
    assert consumption.spent == 1
    assert consumption.post_hoc == (("delta-invented-later", "cell=headline"),)
    assert consumption.is_overspent is True
    assert "POST-HOC" in consumption.summary()


def test_another_family_s_trials_are_not_charged_to_this_one() -> None:
    """The engine version is the discriminator, and it is stated in the config."""
    budget = a_budget()
    records = [
        a_record(strategy="alpha", parameters="cell=headline"),
        a_record(strategy="alpha", parameters="cell=headline", engine_version="other-family"),
    ]
    consumption = consumption_from(budget, records)
    assert consumption.spent == 1


def test_a_rerun_in_the_registry_reads_as_one_trial() -> None:
    """Same identity, one row's worth of consumption."""
    budget = a_budget()
    record = a_record(strategy="alpha", parameters="cell=headline")
    consumption = consumption_from(budget, [record, record, record])
    assert consumption.spent == 1


def test_the_summary_states_the_declared_budget_and_what_is_left() -> None:
    """The brief requires each family's budget consumption in the report."""
    budget = a_budget()
    consumption = consumption_from(budget, [a_record(strategy="alpha", parameters="cell=headline")])
    summary = consumption.summary()
    assert "declared budget: 6 strategy trials" in summary
    assert "charged: 1" in summary
    assert "remaining: 5" in summary

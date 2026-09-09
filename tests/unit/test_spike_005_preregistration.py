"""The guard that makes the pre-registration a pre-registration.

A specification the code can silently disagree with is not a specification. The
runner compares every registered number against the constant it will actually
use and refuses to start on any difference, and these tests prove the refusal
happens rather than trusting that it would.

Nothing here writes to real project data: the committed specification is read,
never modified, and every altered copy is written under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sextant.app.spike_005 import (
    ACCOUNT_EQUITY,
    CONFIG_PATH,
    FOLD_COUNT,
    HAIRCUT_FRACTION,
    IN_SAMPLE_MONTHS,
    MAX_POSITIONS,
    SEED_START,
    SEEDS_EXPOSURE_MATCHED,
    SEEDS_FULLY_INVESTED,
    DriftedFromPreRegistration,
    assert_no_drift,
    cost_cells,
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


def test_the_committed_specification_agrees_with_the_code() -> None:
    """The one that matters. If this fails, no result may be produced."""
    registered = assert_no_drift()
    assert str(registered["version"]) == "v1"
    assert int(str(registered["part"])) == 1


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("account", "equity"), "2000"),
        (("account", "max_positions"), 8),
        (("costs", "delisting_haircut_fraction"), "0.10"),
        (("costs", "spread_bps", "thin"), "5"),
        (("costs", "slippage_bps", "deep"), "1"),
        (("costs", "regimes", "headline", "maker_bps"), "1"),
        (("null_experiment", "seed_start"), 7),
        (("null_experiment", "seed_counts", "exposure_matched_per_cell"), 50),
        (("window", "plans", "walk_forward", "fold_count"), 9),
    ],
)
def test_any_drifted_value_refuses_the_run(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    """A cheaper cost, a smaller haircut, a different fold count: all refused.

    The parameters are chosen to be the changes somebody would actually make to
    flatter a result - a narrower spread, a lighter haircut, fewer folds - so a
    passing test says the guard covers the ones that matter.
    """
    payload = _registered()
    block: object = payload
    for key in path[:-1]:
        assert isinstance(block, dict)
        block = block[key]
    assert isinstance(block, dict)
    block[path[-1]] = value
    altered = _write(payload, tmp_path / "spike-005.yaml")
    with pytest.raises(DriftedFromPreRegistration, match=".".join(path)):
        assert_no_drift(altered)


def test_a_specification_missing_a_block_refuses_rather_than_skipping_it() -> None:
    """A renamed key must not pass as silently as a matching one."""
    with pytest.raises((DriftedFromPreRegistration, KeyError)):
        assert_no_drift(Path("config") / "benchmarks.yaml")


def test_the_registered_account_is_the_account_the_code_will_trade() -> None:
    registered = _registered()
    account = registered["account"]
    assert isinstance(account, dict)
    assert str(account["equity"]) == str(ACCOUNT_EQUITY.amount)
    assert int(str(account["max_positions"])) == MAX_POSITIONS


def test_the_four_cost_cells_are_one_venue_schedule_and_three_fill_mixes() -> None:
    """Maker and taker are equal at the research venue, so its mix cannot move fees."""
    cells = cost_cells()
    assert len(cells) == 4
    headline = [cell for cell in cells if cell.is_headline]
    assert len(headline) == 1
    assert headline[0].schedule.maker_bps == headline[0].schedule.taker_bps
    sensitivity = [cell for cell in cells if not cell.is_headline]
    assert len(sensitivity) == 3
    assert {cell.fill_mix.maker_fraction for cell in sensitivity} == {
        cell.fill_mix.maker_fraction for cell in sensitivity
    }
    assert len({cell.fill_mix.label for cell in sensitivity}) == 3
    assert sum(cell.runs_nulls for cell in cells) == 2


def test_the_registered_seed_counts_are_the_ones_the_code_holds() -> None:
    registered = _registered()
    nulls = registered["null_experiment"]
    assert isinstance(nulls, dict)
    counts = nulls["seed_counts"]
    assert isinstance(counts, dict)
    assert int(str(counts["exposure_matched_per_cell"])) == SEEDS_EXPOSURE_MATCHED
    assert int(str(counts["fully_invested_per_cell"])) == SEEDS_FULLY_INVESTED
    assert int(str(nulls["seed_start"])) == SEED_START


def test_the_registered_walk_forward_shape_is_the_one_the_code_builds() -> None:
    registered = _registered()
    window = registered["window"]
    assert isinstance(window, dict)
    plans = window["plans"]
    assert isinstance(plans, dict)
    walk = plans["walk_forward"]
    assert isinstance(walk, dict)
    assert int(str(walk["in_sample_months"])) == IN_SAMPLE_MONTHS
    assert int(str(walk["fold_count"])) == FOLD_COUNT
    costs = registered["costs"]
    assert isinstance(costs, dict)
    assert str(costs["delisting_haircut_fraction"]) == str(HAIRCUT_FRACTION)

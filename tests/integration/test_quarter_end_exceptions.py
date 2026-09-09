"""The two Q3-to-Q4 2025 exceptions, and what settles them.

SEXTANT-003 checked the quarter-end rule across all thirteen archives and found
163 of 165 priced deaths had a bar within seven days of the boundary. The two
that did not were left unexplained. Two out of 165 is small; unexplained, in the
mechanism the whole dataset rests on, is not.

The values below were established by reading the archive and are written in as
literals, exactly as the rest of the archive-fact tests are: a test that
recomputes its expected value from the code under test proves only
self-consistency.

These tests skip when the archive is absent. A skip is not a pass, and the
message names what would have to be present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sextant.adapters.exchanges.kraken.archive import Quarter
from sextant.adapters.storage.bars import ParquetBarStore
from sextant.app.quarter_end_audit import (
    EXPLAINED_BY_CADENCE,
    EXPLAINED_BY_SIBLING,
    THRESHOLD_DAYS,
    TransitionAudit,
    audit,
)

STORE_ROOT = Path(__file__).resolve().parents[2] / "data" / "kraken-archive"
CALENDAR = STORE_ROOT / "listing_calendar.json"

TRANSITION = Quarter(2025, 4)

#: Measured by hand from the archive before this module existed.
PRICED_DEATHS = 48
FLAGGED = ("MCUSD", "TUSDEUR")
FINAL_GAP_DAYS = 8
LAST_BAR = "2025-09-22"

#: The sibling legs that settle the two cases.
MC_SIBLING_LAST_BAR = "2025-09-25"
TUSD_SIBLING_LAST_BAR = "2025-09-30"


@pytest.fixture(scope="module")
def result() -> TransitionAudit:
    """The audit over the real archive, or a skip naming what is missing."""
    if not CALENDAR.is_file():
        pytest.skip(f"{CALENDAR} is absent; run `sextant archive calendar` first")
    from sextant.app.archive_ingest import load_calendar

    return audit(load_calendar(STORE_ROOT), ParquetBarStore(STORE_ROOT), TRANSITION)


def test_the_transition_still_flags_exactly_two_pairs(result: TransitionAudit) -> None:
    """The finding being explained is the one SEXTANT-003 recorded."""
    assert len(result.measured) == PRICED_DEATHS
    assert tuple(sorted(item.symbol for item in result.flagged)) == tuple(sorted(FLAGGED))
    for item in result.flagged:
        assert item.final_gap_days == FINAL_GAP_DAYS
        assert item.last_bar_date == LAST_BAR


def test_both_pairs_are_explained_and_neither_implicates_the_rule(
    result: TransitionAudit,
) -> None:
    """The verdict this task exists to reach, or to fail to reach."""
    assert result.all_flagged_are_explained


def test_the_sparse_pair_is_explained_by_its_own_trading_cadence(
    result: TransitionAudit,
) -> None:
    """TUSDEUR trades intermittently; an eight-day gap is ordinary for it.

    The archive writes no row for a day with no trades, so a pair that trades
    twice a week has multi-day holes throughout its series. Its ninetieth-
    percentile interval is four days and its longest is seventy; eight is well
    inside that.
    """
    tusd = next(item for item in result.flagged if item.symbol == "TUSDEUR")
    assert tusd.final_gap_is_ordinary
    assert tusd.max_gap_days > FINAL_GAP_DAYS
    assert tusd.sparse_share > 0.25
    assert result.verdict_for("TUSDEUR") == EXPLAINED_BY_CADENCE


def test_the_denser_pair_is_explained_by_its_sibling_leg(result: TransitionAudit) -> None:
    """MCUSD's own cadence does not cover eight days. Its EUR leg settles it.

    MC on the EUR leg traded to 2025-09-25, five days from the boundary and
    inside the threshold, and it died in the same transition. So the asset was
    listed at the boundary exactly as the rule says; what stopped early was
    trading in one quote leg of a pair whose final bars carry one to five trades
    a day.
    """
    mc = next(item for item in result.flagged if item.symbol == "MCUSD")
    assert not mc.final_gap_is_ordinary
    assert result.sibling_last_bars["MCUSD"]["MCEUR"] == MC_SIBLING_LAST_BAR
    assert result.sibling_gaps["MCUSD"]["MCEUR"] <= THRESHOLD_DAYS
    assert result.verdict_for("MCUSD") == EXPLAINED_BY_SIBLING


def test_the_other_pairs_sibling_also_traded_to_the_boundary(
    result: TransitionAudit,
) -> None:
    """Corroboration, not the verdict: TUSD traded on its USD leg to 2025-09-30."""
    assert result.sibling_last_bars["TUSDEUR"]["TUSDUSD"] == TUSD_SIBLING_LAST_BAR
    assert result.sibling_gaps["TUSDEUR"]["TUSDUSD"] == 0


def test_the_audit_refuses_a_symbol_it_did_not_flag(result: TransitionAudit) -> None:
    """A verdict is only ever offered for a pair the check actually flagged."""
    with pytest.raises(KeyError):
        result.verdict_for("XBTEUR")


def test_the_audit_serialises_every_verdict(result: TransitionAudit) -> None:
    """The evidence travels into the report rather than living in a conversation."""
    payload = result.as_json()
    assert payload["priced_deaths"] == PRICED_DEATHS
    verdicts = payload["verdicts"]
    assert isinstance(verdicts, dict)
    assert set(verdicts) == set(FLAGGED)
    assert payload["all_flagged_are_explained"] is True
    assert payload["all_flagged_are_ordinary"] is False

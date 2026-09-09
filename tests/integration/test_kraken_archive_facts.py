"""The facts the Product Owner verified by hand, asserted against the real files.

Every number in this module was established by opening the actual quarterly
archives and counting, before any of this code existed. They are written in as
literals on purpose: a test that recomputes the expected value from the same
code it is testing proves only that the code is self-consistent.

The archives are hundreds of megabytes and git-ignored, so these tests skip when
the files are absent. A skip is not a pass, and the skip message names exactly
which file would have to be present, so a green run with these skipped is
visibly a partial one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sextant.adapters.exchanges.kraken.archive import (
    ArchiveFile,
    PairPresence,
    Quarter,
    QuarterlyArchive,
    sha256_of,
)
from sextant.adapters.exchanges.kraken.listing_calendar import (
    KrakenListingCalendar,
    QuarterlyMembership,
)
from sextant.domain.listing import MembershipState
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

ARCHIVE_ROOT = Path(__file__).resolve().parents[2] / "data"
VENUE = Venue("kraken")

Q2 = Quarter(2024, 2)
Q3 = Quarter(2024, 3)

# -- measured by hand, before this code existed ------------------------------

PAIRS_AT_END_OF_Q2 = 700
PAIRS_AT_END_OF_Q3 = 762

#: The 13 pairs present at the end of Q2 2024 and gone by the end of Q3.
#: Kraken's own announcements put WAVES at 2024-07-08 and ANT at 2024-09-25,
#: both inside Q3, which is what makes the diff a listing calendar.
DIED_DURING_Q3 = frozenset(
    {
        "ANTETH",
        "ANTEUR",
        "ANTUSD",
        "ANTXBT",
        "ETHAED",
        "RNDREUR",
        "RNDRUSD",
        "USDAED",
        "WAVESETH",
        "WAVESEUR",
        "WAVESUSD",
        "WAVESXBT",
        "XBTAED",
    }
)

LISTED_DURING_Q3 = 75

#: ANTEUR in Q2 2024: 91 daily bars, 2024-04-01 to 2024-06-30, hourly to 23:00.
ANTEUR_Q2_DAILY_BARS = 91

#: Present in Q2 2024 with zero rows at every granularity. Listed, and untraded.
UNTRADED_IN_Q2 = frozenset(
    {
        "ETHAED",
        "USDAED",
        "WAVESETH",
        "WAVESEUR",
        "WAVESUSD",
        "WAVESXBT",
        "XBTAED",
        "ZECXBT",
    }
)


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def archive_for(quarter: Quarter) -> ArchiveFile:
    """Open one real quarterly archive, or skip naming the file that is absent."""
    path = ARCHIVE_ROOT / quarter.archive_name
    if not path.is_file():
        pytest.skip(
            f"{path} is absent. This test asserts hand-verified facts about the real "
            "archive and cannot run without it; the file is git-ignored and arrives "
            "by hand from the Drive folder in Kraken's downloadable-OHLCVT article."
        )
    return ArchiveFile(
        quarter=quarter, path=path, sha256=sha256_of(path), size_bytes=path.stat().st_size
    )


@pytest.fixture(scope="module")
def real_calendar() -> KrakenListingCalendar:
    """A calendar built from the two real archives we hold."""
    snapshots = []
    for quarter in (Q2, Q3):
        with QuarterlyArchive(archive_for(quarter)) as archive:
            snapshots.append(QuarterlyMembership(quarter=quarter, presence=archive.presence()))
    return KrakenListingCalendar.from_membership(VENUE, snapshots)


# -- the membership snapshots ------------------------------------------------


def test_each_quarterly_file_holds_the_pairs_listed_at_that_quarters_end() -> None:
    """The rule the whole calendar rests on, counted against the real files."""
    with QuarterlyArchive(archive_for(Q2)) as second:
        assert len(second.symbols()) == PAIRS_AT_END_OF_Q2
    with QuarterlyArchive(archive_for(Q3)) as third:
        assert len(third.symbols()) == PAIRS_AT_END_OF_Q3


def test_ant_is_present_at_the_end_of_q2_and_absent_at_the_end_of_q3() -> None:
    """The first fact the PO verified by hand.

    ANT stopped trading 2024-09-25, inside Q3, so Q3's file drops it while Q2's
    keeps it. That is the venue telling us a delisting happened, from file
    presence alone.
    """
    with QuarterlyArchive(archive_for(Q2)) as second:
        assert "ANTEUR" in second.symbols()
    with QuarterlyArchive(archive_for(Q3)) as third:
        assert "ANTEUR" not in third.symbols()


def test_thirteen_pairs_die_during_q3_2024_against_seventy_five_listing(
    real_calendar: KrakenListingCalendar,
) -> None:
    """The second fact the PO verified by hand, reproduced by the calendar."""
    assert real_calendar.delisted_during(Q3) == DIED_DURING_Q3
    assert len(real_calendar.delisted_during(Q3)) == 13
    assert len(real_calendar.listed_during(Q3)) == LISTED_DURING_Q3
    assert PAIRS_AT_END_OF_Q2 - 13 + LISTED_DURING_Q3 == PAIRS_AT_END_OF_Q3


def test_the_ant_delisting_bracket_contains_the_announced_instant(
    real_calendar: KrakenListingCalendar,
) -> None:
    """2024-09-25 14:00 UTC, from Kraken's own notice, falls inside the interval.

    The calendar never sees that announcement. That the announced instant lands
    inside a bracket derived purely from file presence is the cross-check.
    """
    entry = real_calendar.entry_for("ANTEUR")

    assert entry is not None
    assert entry.delisted_during.after == at("2024-07-01")
    assert entry.delisted_during.until == at("2024-10-01")
    announced = at("2024-09-25T14:00:00")
    assert entry.delisted_during.straddles(announced)
    assert real_calendar.membership_at("ANTEUR", announced) is MembershipState.UNDETERMINED


# -- the two dataset defects -------------------------------------------------


def test_the_final_partial_quarter_of_a_delisted_pair_is_lost() -> None:
    """Defect 1, measured rather than assumed.

    ANT traded until 2024-09-25. Its last surviving bar is 2024-06-30, because
    Q3 dropped the pair and Q2 is the wrong quarter for July onwards. The series
    ends a full quarter before the instrument did, and that is what the
    delisting haircut exists to price.
    """
    with QuarterlyArchive(archive_for(Q2)) as second:
        daily = second.read_series("ANTEUR", Timeframe.D1)
        hourly = second.read_series("ANTEUR", Timeframe.H1)

    assert len(daily) == ANTEUR_Q2_DAILY_BARS
    assert daily[0].open_time == at("2024-04-01")
    assert daily[-1].open_time == at("2024-06-30")
    assert hourly[-1].open_time == at("2024-06-30T23:00:00")

    with QuarterlyArchive(archive_for(Q3)) as third:
        assert not third.has_series("ANTEUR", Timeframe.D1)


def test_waveseur_is_present_and_empty_which_is_listed_and_untraded() -> None:
    """Defect 2, measured rather than assumed.

    The announcement says WAVES stopped trading 2024-07-08. The Q2 archive ships
    its files with zero rows at every granularity, which says it was listed
    through the end of Q2 and nothing changed hands. Presence and tradability
    are different, and only presence establishes membership.
    """
    with QuarterlyArchive(archive_for(Q2)) as second:
        presence = second.presence()
        daily = second.read_series("WAVESEUR", Timeframe.D1)
        hourly = second.read_series("WAVESEUR", Timeframe.H1)

    assert presence["WAVESEUR"] is PairPresence.LISTED_UNTRADED
    assert presence["WAVESEUR"].is_listed
    assert daily == ()
    assert hourly == ()


def test_every_untraded_pair_in_q2_is_the_set_counted_by_hand() -> None:
    with QuarterlyArchive(archive_for(Q2)) as second:
        presence = second.presence()

    untraded = {
        symbol for symbol, state in presence.items() if state is PairPresence.LISTED_UNTRADED
    }
    assert untraded == UNTRADED_IN_Q2


def test_untraded_pairs_stay_members_of_the_calendar(
    real_calendar: KrakenListingCalendar,
) -> None:
    """They must not be compacted into "missing" anywhere in the pipeline."""
    assert real_calendar.untraded_symbols() | {"ZECXBT"} >= UNTRADED_IN_Q2
    assert "WAVESEUR" in real_calendar.symbols_at(at("2024-07-01"))
    entry = real_calendar.entry_for("WAVESEUR")
    assert entry is not None
    assert entry.untraded_quarters == ("Q2_2024",)


def test_the_union_of_both_quarters_is_the_calendars_population(
    real_calendar: KrakenListingCalendar,
) -> None:
    """700 in Q2 plus 75 that listed during Q3 is 775 distinct pairs."""
    assert len(real_calendar.entries) == PAIRS_AT_END_OF_Q2 + LISTED_DURING_Q3
    assert len(real_calendar.entries) == 775

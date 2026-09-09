"""Reading quarterly OHLCVT archives, and diffing them into a listing calendar.

The archives themselves are hundreds of megabytes and git-ignored, so the tests
build miniature ones with the same structure: flat ``<PAIR>_<MINUTES>.csv``
entries, eight granularities per pair, no header row. Structure is what is
under test here, not size.

The facts the Product Owner verified by hand against the real Q2 and Q3 2024
archives are asserted separately, in
``tests/integration/test_kraken_archive_facts.py``, against the real files when
they are present.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sextant.adapters.exchanges.kraken.archive import (
    ARCHIVE_INTERVAL_MINUTES,
    ArchiveError,
    ArchiveFile,
    ArchiveManifest,
    ChecksumMismatch,
    PairPresence,
    Quarter,
    QuarterlyArchive,
    quarters_between,
    scan_archives,
    sha256_of,
)
from sextant.adapters.exchanges.kraken.listing_calendar import (
    KrakenListingCalendar,
    QuarterlyMembership,
)
from sextant.domain.listing import MembershipState
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("kraken")
DAY_SECONDS = 86_400


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def rows(start: str, count: int, close: str = "8.1883") -> str:
    """A CSV body in the venue's exact wire shape, with no header."""
    first = int(at(start).epoch_millis // 1000)
    return "".join(
        f"{first + index * DAY_SECONDS},8.466,8.5063,7.936,{close},1823.26632851,101\n"
        for index in range(count)
    )


def build_archive(
    directory: Path,
    quarter: Quarter,
    pairs: dict[str, str],
) -> ArchiveFile:
    """Write a miniature quarterly ZIP. ``pairs`` maps symbol to its CSV body."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / quarter.archive_name
    with zipfile.ZipFile(path, "w") as archive:
        for symbol, body in pairs.items():
            for minutes in ARCHIVE_INTERVAL_MINUTES:
                archive.writestr(f"{symbol}_{minutes}.csv", body)
    return ArchiveFile(
        quarter=quarter, path=path, sha256=sha256_of(path), size_bytes=path.stat().st_size
    )


# -- quarters ----------------------------------------------------------------


def test_a_quarter_ends_on_the_exclusive_boundary_of_the_next_one() -> None:
    """Consecutive brackets must abut exactly, with no gap and no overlap."""
    third = Quarter(2024, 3)

    assert third.starts_at == at("2024-07-01")
    assert third.ends_at == at("2024-10-01")
    assert Quarter(2024, 2).ends_at == third.starts_at
    assert Quarter(2024, 4).next() == Quarter(2025, 1)


def test_a_quarter_parses_both_label_spellings() -> None:
    assert Quarter.parse("Q3_2024") == Quarter(2024, 3)
    assert Quarter.parse("2024Q3") == Quarter(2024, 3)
    assert Quarter.parse("q3-2024") == Quarter(2024, 3)


def test_an_unparseable_or_impossible_quarter_is_refused() -> None:
    with pytest.raises(ArchiveError):
        Quarter.parse("last summer")
    with pytest.raises(ArchiveError):
        Quarter(2024, 5)
    with pytest.raises(ArchiveError):
        quarters_between(Quarter(2024, 3), Quarter(2024, 1))


def test_the_thirteen_quarters_the_venue_publishes_enumerate_in_order() -> None:
    walked = quarters_between(Quarter(2023, 1), Quarter(2026, 1))

    assert len(walked) == 13
    assert walked[0].label == "Q1_2023"
    assert walked[-1].label == "Q1_2026"
    assert walked[-1].archive_name == "Kraken_OHLCVT_Q1_2026.zip"


# -- manifest and checksums --------------------------------------------------


def test_a_scan_names_what_is_missing_as_precisely_as_what_is_held(tmp_path: Path) -> None:
    """A gapped download is usable; a gapped download nobody wrote down is not."""
    build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 3)})

    manifest = scan_archives(tmp_path, quarters_between(Quarter(2024, 1), Quarter(2024, 3)))

    assert [quarter.label for quarter in manifest.quarters] == ["Q2_2024"]
    assert [quarter.label for quarter in manifest.missing] == ["Q1_2024", "Q3_2024"]
    assert not manifest.is_complete
    manifest.verify_all()


def test_a_changed_file_is_refused_rather_than_read(tmp_path: Path) -> None:
    """These arrive by hand through a browser; a truncated one looks like a small quarter."""
    archive = build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 3)})
    build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 9)})

    with pytest.raises(ChecksumMismatch, match="does not match its recorded checksum"):
        archive.verify()
    with pytest.raises(ChecksumMismatch):
        QuarterlyArchive(archive)


def test_a_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 3)})
    manifest = scan_archives(tmp_path, quarters_between(Quarter(2024, 2), Quarter(2024, 3)))
    target = tmp_path / "manifest.json"

    written = manifest.write_json(target)
    restored = ArchiveManifest.read_json(target, tmp_path)

    assert written == 1
    assert restored.quarters == manifest.quarters
    assert restored.missing == manifest.missing
    assert restored.files["Q2_2024"].sha256 == manifest.files["Q2_2024"].sha256


# -- reading one archive -----------------------------------------------------


def test_presence_distinguishes_listed_and_untraded_from_absent(tmp_path: Path) -> None:
    """The state the whole pipeline has to keep intact.

    WAVESEUR is present in the real Q2 2024 archive with zero rows at every
    granularity. That is the venue saying the pair was listed and nothing
    changed hands, which is information rather than an absence of it.
    """
    archive = build_archive(
        tmp_path,
        Quarter(2024, 2),
        {"ANTEUR": rows("2024-04-01", 3), "WAVESEUR": ""},
    )

    with QuarterlyArchive(archive) as subject:
        presence = subject.presence()

        assert presence["ANTEUR"] is PairPresence.LISTED_TRADED
        assert presence["WAVESEUR"] is PairPresence.LISTED_UNTRADED
        assert "NEVERLISTED" not in presence
        assert presence["WAVESEUR"].is_listed
        assert not PairPresence.ABSENT.is_listed
        assert subject.symbols() == frozenset({"ANTEUR", "WAVESEUR"})


def test_an_untraded_pair_reads_back_as_an_empty_series_not_an_error(
    tmp_path: Path,
) -> None:
    archive = build_archive(tmp_path, Quarter(2024, 2), {"WAVESEUR": ""})

    with QuarterlyArchive(archive) as subject:
        assert subject.has_series("WAVESEUR", Timeframe.D1)
        assert subject.read_series("WAVESEUR", Timeframe.D1) == ()


def test_a_pair_the_archive_never_carried_raises_rather_than_reading_empty(
    tmp_path: Path,
) -> None:
    """ "We never had this" and "this had no trades" are the two facts to keep apart."""
    archive = build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 3)})

    with QuarterlyArchive(archive) as subject:
        assert not subject.has_series("NEVERLISTED", Timeframe.D1)
        with pytest.raises(ArchiveError, match="not the same as untraded"):
            subject.read_series("NEVERLISTED", Timeframe.D1)


def test_only_the_ingested_granularities_are_addressable(tmp_path: Path) -> None:
    archive = build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 3)})

    with QuarterlyArchive(archive) as subject:
        assert subject.read_series("ANTEUR", Timeframe.H1) != ()
        with pytest.raises(ArchiveError, match="not ingested"):
            subject.read_series("ANTEUR", Timeframe.M5)


def test_a_malformed_row_raises_rather_than_being_skipped(tmp_path: Path) -> None:
    """A silently dropped row is a hole in a price series wearing a clean face."""
    short = build_archive(tmp_path / "short", Quarter(2024, 2), {"ANTEUR": "1711929600,1,2\n"})
    bad = build_archive(
        tmp_path / "bad", Quarter(2024, 2), {"ANTEUR": "1711929600,1,2,3,not-a-price,5,6\n"}
    )

    with QuarterlyArchive(short) as subject, pytest.raises(ArchiveError, match="expected 7 fields"):
        subject.read_series("ANTEUR", Timeframe.D1)
    with QuarterlyArchive(bad) as subject, pytest.raises(ArchiveError, match="unparseable row"):
        subject.read_series("ANTEUR", Timeframe.D1)


def test_rows_carry_exact_decimal_text_and_an_aware_open_time(tmp_path: Path) -> None:
    archive = build_archive(tmp_path, Quarter(2024, 2), {"ANTEUR": rows("2024-04-01", 2)})

    with QuarterlyArchive(archive) as subject:
        parsed = subject.read_series("ANTEUR", Timeframe.D1)

    assert len(parsed) == 2
    assert parsed[0].open_time == at("2024-04-01")
    assert parsed[0].close == "8.1883"
    assert parsed[0].trades == 101


# -- the calendar ------------------------------------------------------------


def membership(quarter: Quarter, presence: dict[str, PairPresence]) -> QuarterlyMembership:
    """One quarter's membership snapshot."""
    return QuarterlyMembership(quarter=quarter, presence=presence)


def two_quarter_calendar() -> KrakenListingCalendar:
    """A survivor, a pair that dies in Q3, a pair that lists in Q3, an untraded one."""
    return KrakenListingCalendar.from_membership(
        VENUE,
        [
            membership(
                Quarter(2024, 2),
                {
                    "XBTEUR": PairPresence.LISTED_TRADED,
                    "ANTEUR": PairPresence.LISTED_TRADED,
                    "WAVESEUR": PairPresence.LISTED_UNTRADED,
                },
            ),
            membership(
                Quarter(2024, 3),
                {
                    "XBTEUR": PairPresence.LISTED_TRADED,
                    "EIGENEUR": PairPresence.LISTED_TRADED,
                },
            ),
        ],
    )


def test_a_delisting_is_an_interval_and_never_a_date() -> None:
    """The invariant the whole task exists to honour."""
    entry = two_quarter_calendar().entry_for("ANTEUR")

    assert entry is not None
    assert entry.delisted_during.after == at("2024-07-01")
    assert entry.delisted_during.until == at("2024-10-01")
    assert str(entry.delisted_during) == "(2024-07-01T00:00:00+00:00, 2024-10-01T00:00:00+00:00]"


def test_every_entry_rests_on_the_venues_own_archive() -> None:
    """Nothing here is RECONSTRUCTED: no instant is inferred from a price series."""
    counts = two_quarter_calendar().provenance_counts()

    assert counts[Provenance.VENUE_ARCHIVE] == 4
    assert counts[Provenance.RECONSTRUCTED] == 0
    assert counts[Provenance.UNVERIFIED] == 0
    assert Provenance.VENUE_ARCHIVE.is_directly_verified


def test_the_archive_edges_are_unbounded_rather_than_pinned() -> None:
    """The edge of a download is a fact about the download."""
    calendar = two_quarter_calendar()
    earliest = calendar.entry_for("ANTEUR")
    latest = calendar.entry_for("XBTEUR")

    assert earliest is not None and earliest.listed_during.is_unbounded_before
    assert latest is not None and latest.delisted_during.is_unbounded_after
    assert latest.delisted_during.after == at("2024-10-01")


def test_listings_and_delistings_are_attributed_to_the_right_quarter() -> None:
    calendar = two_quarter_calendar()

    assert calendar.delisted_during(Quarter(2024, 3)) == frozenset({"ANTEUR", "WAVESEUR"})
    assert calendar.listed_during(Quarter(2024, 3)) == frozenset({"EIGENEUR"})
    # Pairs present in the earliest held quarter say nothing about when they listed.
    assert calendar.listed_during(Quarter(2024, 2)) == frozenset()


def test_membership_is_three_valued_across_the_bracket() -> None:
    calendar = two_quarter_calendar()

    assert calendar.membership_at("ANTEUR", at("2024-07-01")) is MembershipState.LISTED
    assert calendar.membership_at("ANTEUR", at("2024-08-15")) is MembershipState.UNDETERMINED
    assert calendar.membership_at("ANTEUR", at("2024-10-01")) is MembershipState.NOT_LISTED
    assert calendar.symbols_at(at("2024-07-01")) == frozenset({"XBTEUR", "ANTEUR", "WAVESEUR"})
    assert calendar.undetermined_at(at("2024-08-15")) == frozenset(
        {"ANTEUR", "WAVESEUR", "EIGENEUR"}
    )


def test_a_pair_the_archives_never_mention_is_undetermined_not_absent() -> None:
    """We hold two quarters of thirteen. Absence from what we hold is our problem."""
    calendar = two_quarter_calendar()

    assert calendar.entry_for("NEVERSEEN") is None
    assert calendar.membership_at("NEVERSEEN", at("2024-07-01")) is MembershipState.UNDETERMINED


def test_listed_and_untraded_survives_into_the_calendar() -> None:
    calendar = two_quarter_calendar()
    entry = calendar.entry_for("WAVESEUR")

    assert entry is not None
    assert entry.was_ever_untraded
    assert entry.untraded_quarters == ("Q2_2024",)
    assert calendar.untraded_symbols() == frozenset({"WAVESEUR"})
    # It is still a member: untraded is not absent.
    assert "WAVESEUR" in calendar.symbols_at(at("2024-07-01"))


def test_a_pair_that_leaves_and_returns_is_two_spells_not_one_long_life() -> None:
    """One window would report the pair tradable through a period it was gone."""
    calendar = KrakenListingCalendar.from_membership(
        VENUE,
        [
            membership(Quarter(2024, 1), {"REPEUR": PairPresence.LISTED_TRADED}),
            membership(Quarter(2024, 2), {"XBTEUR": PairPresence.LISTED_TRADED}),
            membership(Quarter(2024, 3), {"REPEUR": PairPresence.LISTED_TRADED}),
        ],
    )
    entry = calendar.entry_for("REPEUR")

    assert entry is not None
    assert entry.was_relisted
    assert len(entry.spells) == 2
    assert calendar.relisted_symbols() == frozenset({"REPEUR"})
    assert calendar.membership_at("REPEUR", at("2024-04-01")) is MembershipState.LISTED
    assert calendar.membership_at("REPEUR", at("2024-07-01")) is MembershipState.NOT_LISTED
    assert calendar.membership_at("REPEUR", at("2024-10-01")) is MembershipState.LISTED


def test_snapshots_out_of_order_still_produce_forward_intervals() -> None:
    """A backwards bracket is rejected by the domain type, so ordering is not optional."""
    reversed_order = KrakenListingCalendar.from_membership(
        VENUE,
        [
            membership(Quarter(2024, 3), {"XBTEUR": PairPresence.LISTED_TRADED}),
            membership(Quarter(2024, 2), {"XBTEUR": PairPresence.LISTED_TRADED}),
        ],
    )

    assert [quarter.label for quarter in reversed_order.quarters] == ["Q2_2024", "Q3_2024"]


def test_an_empty_set_of_snapshots_yields_an_empty_calendar() -> None:
    calendar = KrakenListingCalendar.from_membership(VENUE, [])

    assert calendar.entries == {}
    assert calendar.quarters == ()


def test_missing_quarters_travel_into_the_calendar(tmp_path: Path) -> None:
    calendar = KrakenListingCalendar.from_membership(
        VENUE,
        [membership(Quarter(2024, 2), {"XBTEUR": PairPresence.LISTED_TRADED})],
        missing_quarters=[Quarter(2024, 1), Quarter(2024, 3)],
    )

    assert [quarter.label for quarter in calendar.missing_quarters] == ["Q1_2024", "Q3_2024"]


def test_a_calendar_round_trips_through_disk(tmp_path: Path) -> None:
    subject = two_quarter_calendar()
    target = tmp_path / "calendar.json"

    written = subject.write_json(target)
    restored = KrakenListingCalendar.read_json(target)

    assert written == 4
    assert restored.venue == subject.venue
    assert restored.quarters == subject.quarters
    assert restored.methodology == subject.methodology
    assert restored.entries == subject.entries

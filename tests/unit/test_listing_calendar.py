"""The listing calendar.

The calendar is the only place that answers "which symbols existed then", and
the venues do not answer it themselves. Two behaviours matter more than the
rest: a symbol we cannot rebuild is *reported as missing* rather than dropped,
and a round trip through disk changes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sextant.adapters.exchanges.listing_calendar import CalendarEntry, ListingCalendar
from sextant.domain.provenance import ListingWindow, Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("testvenue")


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def entry(
    symbol: str,
    listed: str,
    delisted: str | None = None,
    *,
    metadata_available: bool = True,
    provenance: Provenance = Provenance.RECONSTRUCTED,
) -> CalendarEntry:
    """One calendar entry with a stated source."""
    return CalendarEntry(
        symbol=symbol,
        window=ListingWindow(
            listed_at=at(listed),
            delisted_at=None if delisted is None else at(delisted),
            provenance=provenance,
            note="test",
        ),
        metadata_available=metadata_available,
    )


def calendar() -> ListingCalendar:
    """A calendar holding a survivor, a delisted symbol and an unrebuildable one."""
    return ListingCalendar.of(
        venue=VENUE,
        entries=[
            entry("ALIVE", "2020-01-01"),
            entry("DEAD", "2020-01-01", "2022-06-01"),
            entry(
                "GONEWITHOUTTRACE",
                "2019-01-01",
                "2022-06-01",
                metadata_available=False,
                provenance=Provenance.VENUE_ANNOUNCEMENT,
            ),
        ],
        methodology="test",
    )


def test_membership_resolves_in_both_directions_at_a_point_in_time() -> None:
    subject = calendar()

    assert subject.symbols_at(at("2019-06-01")) == frozenset()
    assert subject.symbols_at(at("2021-01-01")) == frozenset({"ALIVE", "DEAD"})
    assert subject.symbols_at(at("2023-01-01")) == frozenset({"ALIVE"})


def test_a_symbol_we_cannot_rebuild_is_reported_missing_rather_than_dropped() -> None:
    """Dropping it is what turns a universe into a list of survivors.

    A caller that silently receives two symbols has no way to know a third was
    trading. A caller told "two members, one unrebuildable" knows its universe
    is incomplete and by how much.
    """
    subject = calendar()

    assert "GONEWITHOUTTRACE" not in subject.symbols_at(at("2021-01-01"))
    assert subject.missing_at(at("2021-01-01")) == frozenset({"GONEWITHOUTTRACE"})
    assert subject.missing_at(at("2023-01-01")) == frozenset()


def test_provenance_is_counted_so_a_report_can_state_what_it_rests_on() -> None:
    counts = calendar().provenance_counts()

    assert counts[Provenance.RECONSTRUCTED] == 2
    assert counts[Provenance.VENUE_ANNOUNCEMENT] == 1
    assert counts[Provenance.UNVERIFIED] == 0


def test_a_round_trip_through_disk_changes_nothing(tmp_path: Path) -> None:
    subject = calendar()
    target = tmp_path / "calendar.json"

    written = subject.write_json(target)
    restored = ListingCalendar.read_json(target)

    assert written == 3
    assert restored.venue == subject.venue
    assert restored.entries == subject.entries
    assert restored.methodology == subject.methodology

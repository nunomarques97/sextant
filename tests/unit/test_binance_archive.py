"""The Binance archive adapter: months, presence, and two timestamp units.

Nothing here reaches the network. The venue's own file shapes are reconstructed
as fixtures, because the point of these tests is what the parser does with a
shape, not whether the venue is up.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest

from sextant.adapters.exchanges.binance.archive import (
    ArchiveError,
    Month,
    MonthlyFile,
    months_between,
    parse_klines,
    presence_by_month,
)
from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.exchanges.membership_intervals import spells_from_presence
from sextant.domain.listing import MembershipState
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("binance")

#: One day of the venue's kline CSV: open time, OHLC, volume, close time, quote
#: volume, trades, taker buy base, taker buy quote, ignore.
_ROW = "{open_time},1.5,2.0,1.0,1.75,100.0,{close_time},175.0,7,50.0,87.5,0"


def _csv(rows: list[str], *, header: bool = False) -> bytes:
    lines = list(rows)
    if header:
        lines.insert(
            0,
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore",
        )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as bundle:
        bundle.writestr("BTCUSDT-1d-2024-01.csv", "\n".join(lines) + "\n")
    return payload.getvalue()


def _millis(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Months
# ---------------------------------------------------------------------------


def test_a_month_ends_at_the_first_instant_of_the_next_one() -> None:
    """Consecutive months abut exactly, so a bracket has no gap and no overlap."""
    january = Month(year=2024, month=1)
    assert january.ends_at == Month(year=2024, month=2).starts_at
    assert Month(year=2024, month=12).next() == Month(year=2025, month=1)
    assert Month(year=2024, month=1).previous() == Month(year=2023, month=12)


def test_a_month_round_trips_through_the_label_the_venue_uses() -> None:
    assert Month.parse("2024-01") == Month(year=2024, month=1)
    assert Month(year=2024, month=1).label == "2024-01"
    with pytest.raises(ArchiveError):
        Month.parse("January 2024")
    with pytest.raises(ArchiveError):
        Month(year=2024, month=13)


def test_months_between_is_inclusive_and_refuses_to_run_backwards() -> None:
    span = months_between(Month(2023, 11), Month(2024, 2))
    assert [item.label for item in span] == ["2023-11", "2023-12", "2024-01", "2024-02"]
    with pytest.raises(ArchiveError):
        months_between(Month(2024, 2), Month(2023, 11))


# ---------------------------------------------------------------------------
# The two timestamp units
# ---------------------------------------------------------------------------


def test_millisecond_and_microsecond_open_times_parse_to_the_same_day() -> None:
    """Objects published from 2025 carry microseconds; both appear in one history.

    The unit is decided by magnitude per row rather than by the object's
    publication date, because a rule keyed on the date would be a rule about
    when we happened to download.
    """
    milliseconds = _millis(2024, 1, 2)
    in_millis = parse_klines(
        _csv([_ROW.format(open_time=milliseconds, close_time=milliseconds + 86_399_999)])
    )
    in_micros = parse_klines(
        _csv(
            [
                _ROW.format(
                    open_time=milliseconds * 1000,
                    close_time=(milliseconds + 86_399_999) * 1000,
                )
            ]
        )
    )
    assert in_millis[0].open_time == in_micros[0].open_time
    assert in_millis[0].open_time == Timestamp(datetime(2024, 1, 2, tzinfo=UTC))


def test_a_header_row_is_skipped_rather_than_parsed_as_a_bar() -> None:
    rows = [_ROW.format(open_time=_millis(2024, 1, day), close_time=0) for day in (1, 2, 3)]
    assert len(parse_klines(_csv(rows, header=True))) == 3
    assert len(parse_klines(_csv(rows, header=False))) == 3


def test_prices_survive_as_exact_text_and_never_become_floats() -> None:
    """ADR 0005: a fixed precision chosen today truncates a value not yet met."""
    row = "1704153600000,0.000000012345678,1E+1,1.0,0.000000012345678,100,0,0,7,0,0,0"
    parsed = parse_klines(_csv([row]))
    assert parsed[0].close == "0.000000012345678"
    assert parsed[0].high == "1E+1"


def test_rows_come_back_sorted_by_open_time_whatever_order_they_were_written() -> None:
    rows = [_ROW.format(open_time=_millis(2024, 1, day), close_time=0) for day in (3, 1, 2)]
    parsed = parse_klines(_csv(rows))
    assert [bar.open_time for bar in parsed] == sorted(bar.open_time for bar in parsed)


def test_a_microsecond_time_that_is_not_a_whole_millisecond_is_refused() -> None:
    """Better to stop than to round a timestamp nobody expected."""
    with pytest.raises(ArchiveError):
        parse_klines(_csv(["1704153600000123,1,1,1,1,1,0,0,1,0,0,0"]))


# ---------------------------------------------------------------------------
# Presence is the membership evidence
# ---------------------------------------------------------------------------


def _file(symbol: str, month: Month) -> MonthlyFile:
    return MonthlyFile(
        symbol=symbol,
        interval="1d",
        month=month,
        key=f"data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month.label}.zip",
        size_bytes=1,
    )


def test_presence_is_a_flag_per_held_month() -> None:
    held = months_between(Month(2024, 1), Month(2024, 4))
    files = [_file("AAAUSDT", Month(2024, 1)), _file("AAAUSDT", Month(2024, 3))]
    assert presence_by_month(files, held) == (True, False, True, False)


def test_a_symbol_present_then_absent_then_present_is_two_spells() -> None:
    """Collapsing them would report it tradable through a gap the archive denies."""
    held = months_between(Month(2024, 1), Month(2024, 5))
    calendar = BinanceListingCalendar.from_presence(
        VENUE,
        {"AAAUSDT": (True, False, False, True, True)},
        held,
    )
    entry = calendar.entry_for("AAAUSDT")
    assert entry is not None
    assert entry.was_relisted
    assert len(entry.spells) == 2


def test_the_bracket_around_a_delisting_is_a_month_and_never_a_date() -> None:
    held = months_between(Month(2024, 1), Month(2024, 4))
    calendar = BinanceListingCalendar.from_presence(
        VENUE, {"AAAUSDT": (True, True, False, False)}, held
    )
    entry = calendar.entry_for("AAAUSDT")
    assert entry is not None
    interval = entry.delisted_during
    assert interval.after == Month(2024, 2).ends_at
    assert interval.until == Month(2024, 3).ends_at


def test_an_instant_inside_the_bracket_is_undetermined_not_delisted() -> None:
    """A rule that cannot verify its input never admits and never rejects."""
    held = months_between(Month(2024, 1), Month(2024, 4))
    calendar = BinanceListingCalendar.from_presence(
        VENUE, {"AAAUSDT": (True, True, False, False)}, held
    )
    inside = Timestamp(datetime(2024, 3, 15, tzinfo=UTC))
    assert calendar.membership_at("AAAUSDT", inside) is MembershipState.UNDETERMINED
    before = Timestamp(datetime(2024, 2, 15, tzinfo=UTC))
    assert calendar.membership_at("AAAUSDT", before) is MembershipState.LISTED
    after = Timestamp(datetime(2024, 4, 15, tzinfo=UTC))
    assert calendar.membership_at("AAAUSDT", after) is MembershipState.NOT_LISTED


def test_a_symbol_the_archive_never_mentions_is_undetermined_not_absent() -> None:
    """Absence from what we hold is a fact about the download, not about the venue."""
    held = months_between(Month(2024, 1), Month(2024, 2))
    calendar = BinanceListingCalendar.from_presence(VENUE, {"AAAUSDT": (True, True)}, held)
    at = Timestamp(datetime(2024, 1, 15, tzinfo=UTC))
    assert calendar.membership_at("NEVERHEARDOFIT", at) is MembershipState.UNDETERMINED


def test_the_archive_edges_are_unbounded_rather_than_pinned() -> None:
    """Pinning either would turn a fact about the download into one about the venue."""
    held = months_between(Month(2024, 1), Month(2024, 3))
    calendar = BinanceListingCalendar.from_presence(VENUE, {"AAAUSDT": (True, True, True)}, held)
    entry = calendar.entry_for("AAAUSDT")
    assert entry is not None
    assert entry.listed_during.is_unbounded_before
    assert entry.delisted_during.is_unbounded_after


def test_the_calendar_round_trips_through_json(tmp_path: object) -> None:
    """Written and read back must be the same calendar, brackets included."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    held = months_between(Month(2024, 1), Month(2024, 4))
    calendar = BinanceListingCalendar.from_presence(
        VENUE, {"AAAUSDT": (True, False, True, True), "BBBUSDT": (True, True, True, True)}, held
    )
    path = tmp_path / "listing_calendar.json"
    assert calendar.write_json(path) == 2
    reloaded = BinanceListingCalendar.read_json(path)
    assert reloaded.entries.keys() == calendar.entries.keys()
    for symbol, entry in calendar.entries.items():
        assert reloaded.entries[symbol].spells == entry.spells
        assert reloaded.entries[symbol].provenance is entry.provenance


# ---------------------------------------------------------------------------
# The shared spell rule
# ---------------------------------------------------------------------------


def test_the_spell_rule_refuses_a_presence_list_that_does_not_match_the_periods() -> None:
    """A flag per period is what makes the brackets mean anything."""
    held = months_between(Month(2024, 1), Month(2024, 3))
    with pytest.raises(ValueError, match="flags"):
        spells_from_presence((True, False), held)


def test_both_venues_cut_the_same_spells_from_the_same_presence() -> None:
    """The rule is venue-neutral, and this is what keeps it that way.

    The Kraken calendar was the original implementation; the shared module was
    extracted from it. If the two ever disagree, one of them has grown a special
    case and the extraction has stopped being an extraction.
    """
    from sextant.adapters.exchanges.kraken.archive import PairPresence, Quarter
    from sextant.adapters.exchanges.kraken.listing_calendar import (
        KrakenListingCalendar,
        QuarterlyMembership,
    )

    flags = (True, True, False, True)
    quarters = [Quarter(year=2024, quarter=index) for index in (1, 2, 3, 4)]
    snapshots = [
        QuarterlyMembership(
            quarter=quarter,
            presence={"AAAEUR": PairPresence.LISTED_TRADED if seen else PairPresence.ABSENT},
        )
        for quarter, seen in zip(quarters, flags, strict=True)
    ]
    kraken = KrakenListingCalendar.from_membership(Venue("kraken"), snapshots)
    shared = spells_from_presence(flags, quarters)
    entry = kraken.entry_for("AAAEUR")
    assert entry is not None
    assert entry.spells == shared

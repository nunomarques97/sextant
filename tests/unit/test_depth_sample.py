"""The depth sample: how a published day is read, and what it refuses to invent.

Two halves, matching the two layers. The adapter half asserts that the published CSV
shape is read exactly, including the one thing this tree does differently from every
other - a naive clock string rather than an epoch, attached to UTC at the boundary.
The reduction half asserts the three decisions that make a median a measurement rather
than an average of whatever survived publication: both sides summed, a minute as the
unit, and an empty window answering nothing rather than zero.

The fixtures are small and hand-computed on purpose. A reduction checked against its
own output is checked against nothing.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

from sextant.adapters.exchanges.binance.archive import ArchiveError
from sextant.adapters.exchanges.binance.futures_archive import (
    FuturesDailyObject,
    FuturesDailyTree,
    parse_book_depth,
)
from sextant.app.spike_006_f1 import DEPTH_WINDOW_ENDS, DEPTH_WINDOW_STARTS
from sextant.app.spike_006_f1_depth import depth_days
from sextant.app.spike_006_f1_report import _depth_section
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp
from sextant.engine.execution.depth import (
    FAR_PERCENT,
    NEAR_PERCENT,
    DepthBand,
    DepthUnreadable,
    _by_minute,
    across_days,
    summarise_day,
)
from sextant.engine.execution.spread import (
    OPENING_MILLIS,
    Quote,
    QuoteUnreadable,
    SpreadAccumulator,
)

DAY = "2023-01-01"

#: The ten bands the venue publishes per snapshot, in publication order.
BANDS = (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5)


def zipped(rows: list[str], *, header: bool = True) -> bytes:
    """One published object, in the shape the venue publishes it."""
    lines = list(rows)
    if header:
        lines.insert(0, "timestamp,percentage,depth,notional")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as bundle:
        bundle.writestr(f"BTCUSDT-bookDepth-{DAY}.csv", "\n".join(lines) + "\n")
    return payload.getvalue()


def snapshot(clock: str, *, near: str, far: str) -> list[str]:
    """One snapshot's ten rows, with the near and far bands set on both sides.

    The bands between are filled with a value neither statistic reads, so a reduction
    that silently summed everything would be caught.
    """
    rows = []
    for band in BANDS:
        if abs(band) == NEAR_PERCENT:
            notional = near
        elif abs(band) == FAR_PERCENT:
            notional = far
        else:
            notional = "999999"
        rows.append(f"{DAY} {clock},{band},1.5,{notional}")
    return rows


def bands_of(payload: bytes) -> tuple[DepthBand, ...]:
    """The adapter's rows as the engine's bands, the way the app layer maps them."""
    return tuple(
        DepthBand(at=row.at, percentage=row.percentage, notional=Notional(Decimal(row.notional)))
        for row in parse_book_depth(payload)
    )


# ---------------------------------------------------------------------------
# The adapter: reading what the venue published
# ---------------------------------------------------------------------------


def test_a_published_day_is_read_with_its_instants_attached_to_utc() -> None:
    """This tree publishes a naive clock string and no epoch column at all."""
    rows = parse_book_depth(zipped(snapshot("00:00:07", near="100", far="500")))
    assert len(rows) == len(BANDS)
    assert rows[0].at == Timestamp.parse(f"{DAY}T00:00:07+00:00")
    assert rows[0].at.value.tzinfo is not None


def test_the_signed_distance_survives_the_parse() -> None:
    """The bid side is negative and is never abs()-ed on the way in."""
    rows = parse_book_depth(zipped(snapshot("00:00:07", near="100", far="500")))
    assert sorted({row.percentage for row in rows}) == sorted(BANDS)


def test_the_notional_stays_exact_text() -> None:
    """Parsed at a width chosen here, a venue's published precision is truncated."""
    rows = parse_book_depth(zipped([f"{DAY} 00:00:07,1,1.5,123456789.123456780000"]))
    assert rows[0].notional == "123456789.123456780000"
    assert Decimal(rows[0].notional) == Decimal("123456789.12345678")


def test_an_unreadable_instant_is_an_error_and_not_a_skipped_row() -> None:
    """A row silently dropped is a shorter day nobody reported."""
    with pytest.raises(ArchiveError, match="unreadable instant"):
        parse_book_depth(zipped([f"{DAY} 25:00:00,1,1.5,100"]), source="BTCUSDT")


def test_a_row_of_the_wrong_width_is_an_error() -> None:
    with pytest.raises(ArchiveError, match="columns"):
        parse_book_depth(zipped([f"{DAY} 00:00:07,1,1.5"]), source="BTCUSDT")


def test_a_daily_object_names_its_own_day_and_its_publisher_digest() -> None:
    item = FuturesDailyObject(
        tree=FuturesDailyTree.BOOK_DEPTH,
        symbol="BTCUSDT",
        day=DAY,
        key=f"data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-{DAY}.zip",
        size_bytes=463182,
    )
    assert item.name == f"BTCUSDT-bookDepth-{DAY}.zip"
    assert item.checksum_url == f"{item.url}.CHECKSUM"
    assert item.url.endswith(item.key)


# ---------------------------------------------------------------------------
# The reduction: both sides, a minute at a time, and nothing invented
# ---------------------------------------------------------------------------


def test_both_sides_are_summed_and_neither_is_halved() -> None:
    """A pair trade lifts one side and hits the other, so it meets both."""
    summary = summarise_day(DAY, bands_of(zipped(snapshot("12:00:07", near="100", far="500"))))
    assert summary.near == Notional(Decimal(200))
    assert summary.far == Notional(Decimal(1000))


def test_the_bands_between_near_and_far_are_not_read() -> None:
    """Cumulative notional at 2, 3 and 4 per cent is neither statistic."""
    summary = summarise_day(DAY, bands_of(zipped(snapshot("12:00:07", near="100", far="500"))))
    assert summary.near is not None
    assert summary.near.amount < Decimal(999999)


def test_a_minute_with_two_snapshots_contributes_their_mean() -> None:
    """The publisher takes two a minute, and a minute is the registered unit.

    Weighting a minute by how many of its snapshots survived publication would make
    the median a fact about the publisher's uptime rather than about the book.
    """
    rows = snapshot("12:00:07", near="100", far="500") + snapshot("12:00:37", near="300", far="700")
    summary = summarise_day(DAY, bands_of(zipped(rows)))
    assert summary.minutes == 1
    assert summary.near == Notional(Decimal(400))


def test_the_median_is_taken_across_minutes() -> None:
    """Three minutes, and the middle one is the answer rather than the mean."""
    rows: list[str] = []
    for minute, near in ((0, "100"), (1, "1000"), (2, "150")):
        rows.extend(snapshot(f"12:{minute:02d}:07", near=near, far="500"))
    summary = summarise_day(DAY, bands_of(zipped(rows)))
    assert summary.minutes == 3
    assert summary.near == Notional(Decimal(300))


def test_an_even_number_of_minutes_averages_the_two_in_the_middle() -> None:
    rows: list[str] = []
    for minute, near in ((0, "100"), (1, "200"), (2, "300"), (3, "400")):
        rows.extend(snapshot(f"12:{minute:02d}:07", near=near, far="500"))
    summary = summarise_day(DAY, bands_of(zipped(rows)))
    assert summary.near == Notional(Decimal(500))


def test_the_opening_window_is_the_five_minutes_after_midnight() -> None:
    """A monthly rebalance decides at 00:00 and would execute inside this window."""
    rows = snapshot("00:02:07", near="100", far="500") + snapshot("13:00:07", near="900", far="500")
    summary = summarise_day(DAY, bands_of(zipped(rows)))
    assert summary.opening_minutes == 1
    assert summary.opening_near == Notional(Decimal(200))
    assert summary.near == Notional(Decimal(1000))


def test_a_snapshot_at_five_minutes_exactly_is_outside_the_opening_window() -> None:
    """00:00-00:05 is five minutes of coverage, not five and a bit."""
    summary = summarise_day(DAY, bands_of(zipped(snapshot("00:05:00", near="100", far="500"))))
    assert summary.opening_minutes == 0


def test_an_empty_opening_window_answers_nothing_rather_than_zero() -> None:
    """The published coverage starts hours into most days, and zero is a claim.

    Zero would read as *no depth rested there*, which is a statement about the
    market. None reads as *this dataset does not say*, which is invariant 9's
    not evaluable and is the truth.
    """
    summary = summarise_day(DAY, bands_of(zipped(snapshot("07:03:02", near="100", far="500"))))
    assert summary.opening_near is None
    assert summary.opening_far is None
    assert summary.opening_is_evaluable is False
    assert summary.is_evaluable is True


def test_a_day_with_no_snapshot_at_all_is_not_evaluable() -> None:
    summary = summarise_day(DAY, ())
    assert summary.minutes == 0
    assert summary.near is None
    assert summary.is_evaluable is False


def test_the_minute_count_travels_with_every_figure() -> None:
    """A median over 3 minutes and one over 1,440 are not the same measurement."""
    rows: list[str] = []
    for minute in range(3):
        rows.extend(snapshot(f"12:{minute:02d}:07", near="100", far="500"))
    payload = summarise_day(DAY, bands_of(zipped(rows))).as_json()
    assert payload["minutes_with_a_snapshot"] == 3
    assert payload["minutes_in_a_complete_day"] == 1440


def test_a_distance_of_zero_is_refused_rather_than_answered() -> None:
    """The private reducer, reached directly because no public caller can pass zero.

    ``summarise_day`` only ever passes the two registered distances, so the guard is
    unreachable from outside. It is asserted anyway: a band at zero per cent is the
    mid itself, a median over it would be a number with no meaning, and a future
    caller adding a third distance should meet an error rather than that number.
    """
    band = DepthBand(
        at=Timestamp.parse(f"{DAY}T00:00:00+00:00"), percentage=0, notional=Notional(Decimal(1))
    )
    with pytest.raises(DepthUnreadable, match="positive"):
        _by_minute([band], 0)


def test_days_that_carried_nothing_are_skipped_rather_than_counted_as_zero() -> None:
    """A median pulled towards zero by unpublished days would understate the book."""
    measured = summarise_day(DAY, bands_of(zipped(snapshot("12:00:07", near="100", far="500"))))
    absent = summarise_day("2023-02-01", ())
    near, far = across_days([measured, absent])
    assert near == Notional(Decimal(200))
    assert far == Notional(Decimal(1000))


def test_across_days_answers_nothing_when_no_day_was_measured() -> None:
    near, far = across_days([summarise_day(DAY, ()), summarise_day("2023-02-01", ())])
    assert near is None
    assert far is None


# ---------------------------------------------------------------------------
# The registered sample's own shape
# ---------------------------------------------------------------------------


def test_the_sample_is_the_first_day_of_each_month_in_the_depth_window() -> None:
    """Section 12: seventeen days, the first of each month, and no holiday calendar."""
    days = depth_days()
    assert len(days) == 17
    assert days[0] == "2023-01-01"
    assert days[-1] == "2024-05-01"
    assert all(day.endswith("-01") for day in days)
    assert days[0] >= DEPTH_WINDOW_STARTS
    assert days[-1] <= DEPTH_WINDOW_ENDS
    assert list(days) == sorted(days)


# ---------------------------------------------------------------------------
# How the sample reaches the page
# ---------------------------------------------------------------------------


def _depth_payload() -> dict[str, object]:
    """A reduced sample in the shape the acquisition writes it."""
    return {
        "symbol_days_fetched": 337,
        "symbol_days_requested": 340,
        "megabytes_fetched": "139.2",
        "depth_window": "2023-01-01/2024-05-17",
        "symbol_count": 20,
        "symbols_selected_at": "2022-12-31",
        "days_requested": ["2023-01-01", "2023-02-01"],
        "median_of_every_measured_day_within_1pct": "3727759.52",
        "median_of_every_measured_day_within_5pct": "11082780.04",
        "days_not_published": {"XRPUSDT": ["2023-11-01"]},
        "per_symbol": [
            {
                "symbol": "WAVESUSDT",
                "days_measured": 17,
                "days_with_an_opening_window": 10,
                "median_across_days_within_1pct": "771613.25",
                "median_across_days_within_5pct": "2102039.71",
            }
        ],
    }


def test_the_capacity_section_prints_the_window_with_every_figure() -> None:
    """Rule C1, asserted on the page rather than trusted to the prose."""
    section = _depth_section(_depth_payload())
    assert "2023-01-01/2024-05-17" in section
    assert "771,613.25" in section
    assert "perpetual leg only" in section


def test_the_unpublished_days_are_named_and_not_merely_counted() -> None:
    """A gap described only by its size is a gap a reader cannot check."""
    section = _depth_section(_depth_payload())
    assert "`XRPUSDT` on 2023-11-01" in section


def test_the_capacity_section_says_so_when_no_sample_was_acquired() -> None:
    """The ordinary case: rule C3 did not ask, so nothing is reported as measured."""
    section = _depth_section(None)
    assert "No depth sample exists" in section
    assert "did not ask" in section


# ---------------------------------------------------------------------------
# The spread reduction: fed, never materialised
# ---------------------------------------------------------------------------


def _quote(seconds: float, *, bid: str, ask: str) -> Quote:
    """One quote at a clock offset, from decimal text at the published scale."""
    scale = 10**8
    return Quote(
        at_millis=int(seconds * 1000),
        bid=int(Decimal(bid) * scale),
        ask=int(Decimal(ask) * scale),
    )


def test_the_spread_is_the_quoted_distance_over_the_midpoint() -> None:
    """100.00 by 100.01 is one basis point of a midpoint of 100.005, less a hair."""
    acc = SpreadAccumulator(window="whole day")
    acc.add(_quote(0, bid="100.00", ask="100.01"))
    summary = acc.summary()
    assert summary.median_bps is not None
    assert Decimal("0.9999") < summary.median_bps < Decimal("1.0")


def test_a_quote_weighs_the_time_until_the_next_one() -> None:
    """Time-weighted means what it says: a quote that rested a second counts a second."""
    acc = SpreadAccumulator(window="whole day", ends_millis=2000)
    acc.add(_quote(0, bid="100.00", ask="100.02"))
    acc.add(_quote(1, bid="100.00", ask="100.04"))
    summary = acc.summary()
    assert summary.covered_millis == 2000
    assert summary.time_weighted_mean_bps is not None
    # One second at about 2 bps and one at about 4, so the mean sits in between.
    assert Decimal(2) < summary.time_weighted_mean_bps < Decimal(4)


def test_a_quote_superseded_in_the_same_millisecond_weighs_nothing() -> None:
    """Two quotes at one instant: the first never rested, so it cannot be weighted."""
    acc = SpreadAccumulator(window="whole day", ends_millis=1000)
    acc.add(_quote(0, bid="100.00", ask="110.00"))
    acc.add(_quote(0, bid="100.00", ask="100.02"))
    summary = acc.summary()
    assert summary.time_weighted_mean_bps is not None
    assert summary.time_weighted_mean_bps < Decimal(10)


def test_the_opening_window_takes_only_its_own_quotes() -> None:
    acc = SpreadAccumulator(window="opening", ends_millis=OPENING_MILLIS)
    acc.add(_quote(60, bid="100.00", ask="100.02"))
    acc.add(_quote(600, bid="100.00", ask="100.50"))
    summary = acc.summary()
    assert summary.quotes == 1
    assert summary.median_bps is not None
    assert summary.median_bps < Decimal(10)


def test_a_window_with_no_quote_answers_nothing_rather_than_zero() -> None:
    """The tree's first published day starts at midday, and zero is a claim."""
    summary = SpreadAccumulator(window="opening", ends_millis=OPENING_MILLIS).summary()
    assert summary.quotes == 0
    assert summary.median_bps is None
    assert summary.time_weighted_mean_bps is None
    assert summary.is_evaluable is False


def test_a_crossed_quote_is_counted_and_not_averaged_in() -> None:
    """A locked or crossed book is a fact about the feed, not a negative spread."""
    acc = SpreadAccumulator(window="whole day")
    acc.add(_quote(0, bid="100.01", ask="100.00"))
    acc.add(_quote(1, bid="100.00", ask="100.02"))
    summary = acc.summary()
    assert summary.crossed_quotes == 1
    assert summary.quotes == 1


def test_a_non_positive_price_is_refused_rather_than_reduced() -> None:
    acc = SpreadAccumulator(window="whole day")
    with pytest.raises(QuoteUnreadable, match="non-positive"):
        acc.add(Quote(at_millis=0, bid=0, ask=100))


def test_the_median_is_exact_over_a_counted_distribution() -> None:
    """Seven million quotes a day, so the median comes from counts rather than a list."""
    acc = SpreadAccumulator(window="whole day")
    for index, ask in enumerate(("100.01", "100.02", "100.03")):
        acc.add(_quote(index, bid="100.00", ask=ask))
    summary = acc.summary()
    assert summary.quotes == 3
    assert summary.median_bps is not None
    assert Decimal("1.99") < summary.median_bps < Decimal("2.0")

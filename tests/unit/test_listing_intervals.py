"""Listing facts stated as brackets rather than as instants.

The type exists to make one mistake impossible: writing down a date when the
evidence only supports a range. Everything asserted here is about the third
answer - ``UNDETERMINED`` - surviving to the caller instead of being rounded to
whichever of the other two is convenient.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sextant.domain.listing import (
    EventInterval,
    IntervalListingWindow,
    InvalidInterval,
    MembershipState,
)
from sextant.domain.time import Timestamp


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def window(
    listed_after: str | None,
    listed_until: str | None,
    delisted_after: str | None,
    delisted_until: str | None,
) -> IntervalListingWindow:
    """A trading life with both ends stated as brackets."""
    return IntervalListingWindow(
        listed_during=EventInterval(
            after=None if listed_after is None else at(listed_after),
            until=None if listed_until is None else at(listed_until),
        ),
        delisted_during=EventInterval(
            after=None if delisted_after is None else at(delisted_after),
            until=None if delisted_until is None else at(delisted_until),
        ),
    )


# -- the interval type -------------------------------------------------------


def test_an_interval_unbounded_on_both_sides_is_refused() -> None:
    """It constrains nothing, so it is not evidence and may not wear the type."""
    with pytest.raises(InvalidInterval, match="constrains nothing"):
        EventInterval(after=None, until=None)


def test_an_interval_that_runs_backwards_is_refused() -> None:
    with pytest.raises(InvalidInterval, match="must run forwards"):
        EventInterval.between(at("2024-10-01"), at("2024-07-01"))


def test_a_bounded_interval_answers_all_three_ways() -> None:
    subject = EventInterval.between(at("2024-07-01"), at("2024-10-01"))

    assert subject.certainly_not_by(at("2024-07-01"))
    assert subject.straddles(at("2024-08-15"))
    assert subject.certainly_by(at("2024-10-01"))
    assert not subject.is_unbounded_before
    assert not subject.is_unbounded_after


def test_an_interval_open_into_the_past_is_never_certainly_not_by() -> None:
    """A pair in the earliest archive we hold may have listed at any earlier time."""
    subject = EventInterval.at_or_before(at("2024-07-01"))

    assert subject.is_unbounded_before
    assert not subject.certainly_not_by(at("2020-01-01"))
    assert subject.straddles(at("2020-01-01"))
    assert subject.certainly_by(at("2024-07-01"))


def test_an_interval_open_into_the_future_is_never_certainly_by() -> None:
    """It covers "later" and "never" alike, which is all a finite archive supports."""
    subject = EventInterval.after_only(at("2024-10-01"))

    assert subject.is_unbounded_after
    assert subject.certainly_not_by(at("2024-10-01"))
    assert not subject.certainly_by(at("2099-01-01"))
    assert subject.straddles(at("2099-01-01"))


def test_an_interval_renders_its_open_ends_visibly() -> None:
    assert str(EventInterval.at_or_before(at("2024-07-01"))).startswith("(-inf, ")
    assert str(EventInterval.after_only(at("2024-10-01"))).endswith(", +inf]")


# -- membership --------------------------------------------------------------


def test_an_instant_inside_a_delisting_bracket_is_undetermined() -> None:
    """The whole reason the type exists.

    ANT was present at the end of Q2 2024 and absent at the end of Q3, so it
    died somewhere inside Q3. Asked about 15 August, the only supportable answer
    is that nobody knows - and it must reach the caller as that rather than as a
    quiet yes or a quiet no.
    """
    ant = window("2024-04-01", "2024-07-01", "2024-07-01", "2024-10-01")

    assert ant.membership_at(at("2024-07-01")) is MembershipState.LISTED
    assert ant.membership_at(at("2024-08-15")) is MembershipState.UNDETERMINED
    assert ant.membership_at(at("2024-10-01")) is MembershipState.NOT_LISTED
    assert ant.membership_at(at("2024-11-01")) is MembershipState.NOT_LISTED


def test_an_instant_before_a_listing_bracket_is_not_listed() -> None:
    subject = window("2024-04-01", "2024-07-01", "2024-10-01", None)

    assert subject.membership_at(at("2024-04-01")) is MembershipState.NOT_LISTED
    assert subject.membership_at(at("2024-05-15")) is MembershipState.UNDETERMINED
    assert subject.membership_at(at("2024-07-01")) is MembershipState.LISTED


def test_a_pair_at_the_edge_of_the_archive_is_undetermined_afterwards() -> None:
    """This is how the post-archive survivorship gap surfaces by itself.

    A pair listed at the last instant the archive covers may have died the day
    after. An open delisting bracket says exactly that, so every instant past
    the archive's end reads undetermined rather than as a confident survivor.
    """
    subject = window(None, "2024-07-01", "2024-10-01", None)

    assert subject.membership_at(at("2024-10-01")) is MembershipState.LISTED
    assert subject.membership_at(at("2025-01-01")) is MembershipState.UNDETERMINED


def test_the_conservative_listing_instant_is_the_upper_bound() -> None:
    """The only instant from a bracket usable without claiming more than is known."""
    subject = window(None, "2024-07-01", "2024-10-01", None)

    assert subject.certainly_listed_by == at("2024-07-01")


def test_a_listing_still_open_has_no_usable_instant() -> None:
    subject = IntervalListingWindow(
        listed_during=EventInterval.after_only(at("2024-10-01")),
        delisted_during=EventInterval.after_only(at("2024-10-01")),
    )

    assert subject.certainly_listed_by is None

"""Listing facts that are known to within a bracket rather than to an instant.

``ListingWindow`` in ``provenance`` carries instants, which is the right shape
when a venue publishes a date or when a bar series is dense enough that its
edges mean something. A membership snapshot is not that shape.

When the only evidence is "this pair was present at the end of one period and
absent at the end of the next", the honest statement is that it stopped trading
*somewhere inside* the second period. Writing that down as a date - the period's
end, its midpoint, the last bar - invents precision the evidence does not carry,
and every downstream number then inherits the invention while looking exactly
like a measurement.

So the event is an :class:`EventInterval`, and the question "was this listed at
``at``?" has three answers rather than two. The third one, ``UNDETERMINED``, is
the one that earns its keep: it is what an instant falling *inside* a bracket
must produce, and it is what keeps a strategy from being scored on a pair whose
membership nobody actually knows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sextant.domain.errors import DomainError
from sextant.domain.time import Timestamp


class InvalidInterval(DomainError):
    """An interval was constructed that constrains nothing, or runs backwards."""


class MembershipState(StrEnum):
    """Whether an instrument was tradable at an instant, allowing for ignorance.

    ``UNDETERMINED`` is not a failure and not a maybe-yes. It is the correct
    answer whenever the instant falls inside the bracket in which listing or
    delisting is known to have happened, and it must reach the caller intact:
    collapsing it into ``NOT_LISTED`` quietly deletes instruments from a
    historical universe, and collapsing it into ``LISTED`` quietly adds them.
    """

    LISTED = "listed"
    NOT_LISTED = "not_listed"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class EventInterval:
    """The half-open bracket ``(after, until]`` in which one event occurred.

    ``after`` is exclusive and ``until`` is inclusive, which is the shape a
    membership diff produces naturally: a pair present at the end of one period
    and absent at the end of the next changed state strictly after the first
    boundary and at or before the second.

    Either side may be ``None``, meaning unbounded. A pair present in the
    earliest archive we hold may have listed at any time before it, and a pair
    present in the latest may delist at any time after - or never. Both are
    modelled as unbounded rather than pinned to the edge of the archive, because
    the edge of an archive is a fact about the archive.

    Both sides unbounded is rejected. An interval that constrains nothing is not
    evidence, and admitting one would let "we have no idea" travel through the
    system wearing the same type as a measurement.
    """

    after: Timestamp | None
    until: Timestamp | None

    def __post_init__(self) -> None:
        if self.after is None and self.until is None:
            raise InvalidInterval(
                "An interval unbounded on both sides constrains nothing and is not "
                "evidence. Record the absence of evidence instead."
            )
        if self.after is not None and self.until is not None and self.after >= self.until:
            raise InvalidInterval(
                f"Interval must run forwards: after ({self.after.isoformat()}) is not "
                f"before until ({self.until.isoformat()})"
            )

    @classmethod
    def between(cls, after: Timestamp, until: Timestamp) -> EventInterval:
        """A bracket bounded on both sides."""
        return cls(after=after, until=until)

    @classmethod
    def at_or_before(cls, until: Timestamp) -> EventInterval:
        """A bracket open into the past: the event had happened by ``until``."""
        return cls(after=None, until=until)

    @classmethod
    def after_only(cls, after: Timestamp) -> EventInterval:
        """A bracket open into the future: the event had not happened by ``after``.

        This is also how "and it may never happen" is expressed. An open upper
        bound covers both a later event and no event at all, which is exactly
        what is known about an instrument still present in the last period we
        hold.
        """
        return cls(after=after, until=None)

    @property
    def is_unbounded_before(self) -> bool:
        """Whether the event could have happened arbitrarily early."""
        return self.after is None

    @property
    def is_unbounded_after(self) -> bool:
        """Whether the event may still be in the future, or may never happen."""
        return self.until is None

    def certainly_by(self, at: Timestamp) -> bool:
        """Whether the event had certainly happened at or before ``at``."""
        return self.until is not None and self.until <= at

    def certainly_not_by(self, at: Timestamp) -> bool:
        """Whether the event had certainly not happened by ``at``."""
        return self.after is not None and at <= self.after

    def straddles(self, at: Timestamp) -> bool:
        """Whether ``at`` falls inside the bracket, so the answer is unknown."""
        return not self.certainly_by(at) and not self.certainly_not_by(at)

    def __str__(self) -> str:
        left = "-inf" if self.after is None else self.after.isoformat()
        right = "+inf" if self.until is None else self.until.isoformat()
        return f"({left}, {right}]"


@dataclass(frozen=True, slots=True)
class IntervalListingWindow:
    """One instrument's trading life, with both ends stated as brackets.

    ``delisted_during`` is never ``None``. An instrument still present in the
    most recent evidence has a delisting bracket open into the future, which
    says "not by this instant, and possibly never". That is a different claim
    from "never", and only the first one is supportable from a finite archive.
    """

    listed_during: EventInterval
    delisted_during: EventInterval

    def membership_at(self, at: Timestamp) -> MembershipState:
        """Whether this instrument was tradable at ``at``, allowing for ignorance."""
        if self.listed_during.certainly_not_by(at):
            return MembershipState.NOT_LISTED
        if self.listed_during.straddles(at):
            return MembershipState.UNDETERMINED
        if self.delisted_during.certainly_by(at):
            return MembershipState.NOT_LISTED
        if self.delisted_during.straddles(at):
            return MembershipState.UNDETERMINED
        return MembershipState.LISTED

    @property
    def certainly_listed_by(self) -> Timestamp | None:
        """The latest instant by which listing had certainly happened.

        The conservative reading of the listing bracket, and the only instant
        from it that can be used without claiming more than is known. ``None``
        when the bracket is open into the future, which means listing itself is
        not yet established.
        """
        return self.listed_during.until

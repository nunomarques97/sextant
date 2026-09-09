"""The currency leg, when the account and the instrument disagree.

The account is funded in one currency. An instrument quoted in another one is
still perfectly tradable, and taking it doubles or triples the cross-section a
strategy has to choose from - but the breadth is not free. Two costs arrive with
it, and they are different in kind:

* **conversion**, which is a fee and is charged twice per position, on the way
  in and on the way out;
* **the move**, which is not a cost at all but an exposure. A position that
  returns 5% in the foreign currency returns something else in the account's,
  and the difference is whatever the pair did while the position was open.

The point of this module is that both are priced explicitly rather than assumed
to be zero, and that the difference between pricing them and ignoring them is
reportable as its own number. That difference is the price of the breadth, and
it is the number that decides the quote-currency policy.

Three policies, and why the middle one exists
----------------------------------------------

:class:`FxPolicy` has three values, and only two of them describe something a
real account could do.

``ACCOUNT_CURRENCY_ONLY`` is the narrow universe: nothing to convert.

``APPLIED`` is the wide universe, honestly priced.

``IGNORED`` is the wide universe with the currency leg deleted - a foreign
return credited to the account as though it were a domestic one, and no
conversion fee. **No account behaves like this.** It is a deliberate
counterfactual, and it exists because it is what a backtester does when nobody
has thought about currency. Running it alongside ``APPLIED`` turns "we should
probably model FX" into a number.

A result computed under ``IGNORED`` is an intermediate figure and every report
carrying one must label it as such.

Rates are point-in-time
------------------------

:class:`FxRates` answers with the most recent rate that had *closed* at or
before the instant asked about, and refuses to answer at all for an instant
before its first observation. It never interpolates and never carries a rate
forward across an unbounded gap: a stale rate used as a current one is a
look-ahead error running backwards.

Pure computation. No I/O, no clock, no venue.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sextant.domain.errors import DomainError
from sextant.domain.money import Notional
from sextant.domain.time import Timestamp

BASIS_POINTS = Decimal(10_000)


class FxRateUnavailable(DomainError):
    """No rate is known for the instant asked about.

    Raised rather than defaulting to 1.0 or to the nearest known rate. A missing
    exchange rate silently treated as parity is a 15% error that looks like
    alpha, and one carried forward across a six-month gap is worse.
    """


class FxPolicy(StrEnum):
    """How a position quoted in a foreign currency is accounted for."""

    ACCOUNT_CURRENCY_ONLY = "account_currency_only"
    """The universe is restricted to the account's own currency. No leg."""

    IGNORED = "ignored"
    """Counterfactual. Foreign returns credited as domestic, no conversion fee.
    Not a currency treatment; a deliberately unpriced one, for comparison."""

    APPLIED = "applied"
    """Conversion charged both ways and the realised move applied."""

    @property
    def is_counterfactual(self) -> bool:
        """Whether results under this policy must be labelled as intermediate."""
        return self is FxPolicy.IGNORED


@dataclass(frozen=True, slots=True)
class FxRates:
    """Dated conversion rates from one foreign currency into the account's.

    ``observations`` maps the instant a rate became knowable - a bar's close
    time, never its open time - to the number of account-currency units one
    foreign-currency unit buys.
    """

    foreign_currency: str
    account_currency: str
    observations: Mapping[Timestamp, Decimal]
    source: str
    _instants: tuple[int, ...] = field(default=(), repr=False)
    _rates: tuple[Decimal, ...] = field(default=(), repr=False)

    @classmethod
    def of(
        cls,
        foreign_currency: str,
        account_currency: str,
        observations: Mapping[Timestamp, Decimal],
        source: str,
    ) -> FxRates:
        """Build a rate series, ordered for point-in-time lookup."""
        ordered = sorted(observations.items(), key=lambda item: item[0])
        for instant, rate in ordered:
            if rate <= 0:
                raise FxRateUnavailable(
                    f"{foreign_currency}/{account_currency} rate at {instant.isoformat()} "
                    f"is {rate}, which is not a usable exchange rate."
                )
        return cls(
            foreign_currency=foreign_currency,
            account_currency=account_currency,
            observations=dict(ordered),
            source=source,
            _instants=tuple(instant.epoch_millis for instant, _ in ordered),
            _rates=tuple(rate for _, rate in ordered),
        )

    def rate_at(self, at: Timestamp) -> Decimal:
        """The most recent rate knowable at ``at``. Raises when there is none."""
        index = bisect_right(self._instants, at.epoch_millis)
        if index == 0:
            raise FxRateUnavailable(
                f"No {self.foreign_currency}/{self.account_currency} rate is known at or "
                f"before {at.isoformat()}. The series starts later, and inventing a rate "
                "here would put a currency move into a result as though it were a return."
            )
        return self._rates[index - 1]

    @property
    def first_instant(self) -> Timestamp | None:
        """When this series starts, or None when it is empty."""
        return next(iter(self.observations), None)

    def as_metadata(self) -> Mapping[str, str]:
        """The rate series in a form a run manifest carries verbatim."""
        return {
            "fx_pair": f"{self.foreign_currency}/{self.account_currency}",
            "fx_source": self.source,
            "fx_observations": str(len(self.observations)),
        }


@dataclass(frozen=True, slots=True)
class FxLeg:
    """The conversion charge, and the policy it is applied under.

    ``conversion_bps`` is an assumption in exactly the sense
    :class:`~sextant.engine.execution.costs.CostAssumption` means it: a
    configured number carrying the prose that justifies it. It is charged once
    on the way into the foreign currency and once on the way back.
    """

    policy: FxPolicy
    conversion_bps: Decimal
    basis: str
    rates: FxRates | None = None

    def __post_init__(self) -> None:
        if self.conversion_bps < 0:
            raise FxRateUnavailable(f"conversion_bps must not be negative: {self.conversion_bps}")
        if not self.basis.strip():
            raise FxRateUnavailable(
                "An FX conversion charge without a stated basis is indistinguishable "
                "from a measurement."
            )
        if self.policy is FxPolicy.APPLIED and self.rates is None:
            raise FxRateUnavailable(
                "FxPolicy.APPLIED needs a rate series. Applying a currency leg with no "
                "rates is the same as ignoring it, but labelled as though it were not."
            )

    def rate_at(self, at: Timestamp) -> Decimal:
        """Account-currency units per foreign unit at ``at``.

        Exactly ``1`` under the two policies that do not convert. Under
        ``IGNORED`` that ``1`` is the counterfactual itself: it is what makes a
        foreign return arrive as though it were domestic.
        """
        if self.policy is not FxPolicy.APPLIED:
            return Decimal(1)
        if self.rates is None:  # pragma: no cover - forbidden by __post_init__
            raise FxRateUnavailable("FxPolicy.APPLIED requires rates")
        return self.rates.rate_at(at)

    def conversion_cost(self, amount: Notional) -> Notional:
        """What one crossing of the currency boundary costs, in account units.

        Zero under every policy that does not convert, including ``IGNORED``.
        The whole point of ``IGNORED`` is that it is free, which is why the gap
        to ``APPLIED`` measures something.
        """
        if self.policy is not FxPolicy.APPLIED:
            return Notional(Decimal(0))
        return Notional(abs(amount.amount) * self.conversion_bps / BASIS_POINTS)

    def as_metadata(self) -> Mapping[str, str]:
        """The leg in a form a run manifest carries verbatim."""
        metadata: dict[str, str] = {
            "fx_policy": self.policy.value,
            "fx_conversion_bps": str(self.conversion_bps),
            "fx_conversion_basis": self.basis,
            "fx_conversion_kind": "assumption",
            "fx_is_counterfactual": str(self.policy.is_counterfactual),
        }
        if self.rates is not None:
            metadata.update(self.rates.as_metadata())
        return metadata


@dataclass(frozen=True, slots=True)
class CurrencyRouting:
    """Which instruments need the leg and which do not.

    An instrument quoted in the account's own currency never touches the FX
    machinery, whatever the policy is. That is not an optimisation: charging a
    conversion on a domestic position would be inventing a cost, and it would
    make the EUR-only and the EUR+USD policies incomparable.
    """

    account_currency: str
    quote_by_symbol: Mapping[str, str]

    def is_foreign(self, symbol: str) -> bool:
        """Whether this instrument is quoted in something other than the account's."""
        return self.quote_by_symbol.get(symbol, self.account_currency) != self.account_currency

    def foreign_symbols(self, symbols: Sequence[str]) -> tuple[str, ...]:
        """Which of these need the currency leg, in the order given."""
        return tuple(symbol for symbol in symbols if self.is_foreign(symbol))

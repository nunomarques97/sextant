"""Cutting the evaluation window into regimes, from information available then.

The rule is fixed in `docs/PRE-REGISTRATION-005.md` part 1 section 7 and is
reproduced here as a cascade of five clauses, first match wins. It is total and
deterministic: every rebalance instant receives exactly one label from bars that
had closed at or before it, and a label is never revised by anything that
happens afterwards.

Point-in-time matters more here than it looks. The obvious way to segment a
window is to look at the whole equity curve and mark the bear markets, which is
a look-ahead so complete that every strategy appears to survive its own worst
period. A rule that can only see backwards cannot do that, and the price is that
it labels the first month of a crash as whatever preceded it. That is the
correct trade and the report says which months each regime got.

The reference series is the venue's largest instrument, denominated in the
account's currency, because a regime measured in a foreign currency would tell a
EUR account about somebody else's market.

Pure computation. No I/O, no venue, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from sextant.domain.time import Timestamp

#: Part 1 section 7, verbatim.
CRASH_RETURN_30D = Decimal("-0.25")
BEAR_DRAWDOWN = Decimal("0.30")
RECOVERY_DRAWDOWN = Decimal("0.10")
DRAWDOWN_LOOKBACK_DAYS = 365
SHORT_LOOKBACK_DAYS = 30
LONG_LOOKBACK_DAYS = 90

#: Below this many months a regime's result is reported and nothing is concluded
#: from it. Part 1 section 7.
MINIMUM_MONTHS_TO_CONCLUDE = 6


class Regime(StrEnum):
    """The four labels the cascade can assign, plus the honest fifth."""

    BULL = "bull"
    BEAR = "bear"
    CRASH = "crash"
    RECOVERY = "recovery"
    NOT_EVALUABLE = "not_evaluable"
    """The reference series did not reach back far enough at this instant. Not a
    regime and never counted as one: it is the segmentation saying it cannot
    answer, which is a different fact from a quiet market."""


@dataclass(frozen=True, slots=True)
class RegimeInputs:
    """What the cascade saw at one instant, kept so a label can be audited."""

    at: Timestamp
    price: Decimal | None
    return_30d: Decimal | None
    return_90d: Decimal | None
    drawdown: Decimal | None
    regime: Regime

    def as_json(self) -> dict[str, object]:
        """Serialisable form, so a reader can re-derive the label by hand."""
        return {
            "at": self.at.isoformat(),
            "price": None if self.price is None else str(self.price),
            "return_30d": None if self.return_30d is None else str(self.return_30d),
            "return_90d": None if self.return_90d is None else str(self.return_90d),
            "drawdown": None if self.drawdown is None else str(self.drawdown),
            "regime": self.regime.value,
        }


def _last_at_or_before(
    instants: Sequence[Timestamp],
    closes: Mapping[Timestamp, Decimal],
    at: Timestamp,
) -> Decimal | None:
    """The most recent close at or before ``at``, or None when there is none."""
    from bisect import bisect_right

    index = bisect_right(instants, at)
    if index == 0:
        return None
    return closes[instants[index - 1]]


def classify(
    closes: Mapping[Timestamp, Decimal],
    at: Timestamp,
) -> RegimeInputs:
    """Label one instant by the pre-registered cascade.

    ``closes`` is keyed by the bar's **close time**, so a value is only visible
    at an instant once the bar carrying it has finished forming. Anything the
    cascade cannot compute makes the instant ``NOT_EVALUABLE`` rather than
    defaulting to a label, because defaulting would put the cold-start months
    into whichever regime the default happened to be.
    """
    instants = sorted(closes)
    price = _last_at_or_before(instants, closes, at)
    if price is None or price <= 0:
        return RegimeInputs(at, None, None, None, None, Regime.NOT_EVALUABLE)

    def ratio(days: int) -> Decimal | None:
        past = _last_at_or_before(instants, closes, at.plus(-timedelta(days=days)))
        if past is None or past <= 0:
            return None
        return price / past - Decimal(1)

    return_30d = ratio(SHORT_LOOKBACK_DAYS)
    return_90d = ratio(LONG_LOOKBACK_DAYS)

    floor = at.plus(-timedelta(days=DRAWDOWN_LOOKBACK_DAYS))
    trailing = [value for instant, value in closes.items() if floor < instant <= at]
    peak = max(trailing) if trailing else None
    drawdown = None if peak is None or peak <= 0 else Decimal(1) - price / peak

    if return_30d is None or return_90d is None or drawdown is None:
        return RegimeInputs(at, price, return_30d, return_90d, drawdown, Regime.NOT_EVALUABLE)

    if return_30d <= CRASH_RETURN_30D:
        label = Regime.CRASH
    elif drawdown >= BEAR_DRAWDOWN:
        label = Regime.BEAR
    elif drawdown >= RECOVERY_DRAWDOWN and return_90d > 0:
        label = Regime.RECOVERY
    elif return_90d > 0:
        label = Regime.BULL
    else:
        label = Regime.BEAR
    return RegimeInputs(at, price, return_30d, return_90d, drawdown, label)


def segment(
    closes: Mapping[Timestamp, Decimal],
    instants: Sequence[Timestamp],
) -> tuple[RegimeInputs, ...]:
    """Label every rebalance instant. One label each, in the order given."""
    return tuple(classify(closes, at) for at in instants)


def month_counts(labels: Sequence[RegimeInputs]) -> Mapping[Regime, int]:
    """How many instants fell in each regime, including the unevaluable ones."""
    counts: dict[Regime, int] = dict.fromkeys(Regime, 0)
    for item in labels:
        counts[item.regime] += 1
    return counts


def conclusive(counts: Mapping[Regime, int]) -> tuple[Regime, ...]:
    """The regimes carrying enough months for part 1 criterion 4 to apply."""
    return tuple(
        regime
        for regime in (Regime.BULL, Regime.BEAR, Regime.CRASH, Regime.RECOVERY)
        if counts.get(regime, 0) >= MINIMUM_MONTHS_TO_CONCLUDE
    )

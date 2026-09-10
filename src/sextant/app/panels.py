"""Two series every spike derives from the same panel, in one place.

The currency leg and the regime reference are not strategy choices. They are
readings of the venue's own archive, and SEXTANT-005 and SEXTANT-006 must take
them identically or a return in EUR in one task and a return in EUR in the other
are not the same quantity. Extracted here, unchanged, with the symbols and
currencies as explicit parameters rather than as constants a caller cannot see.

Nothing here is an assumption. Both functions read published daily closes and do
one arithmetic operation each; the reasons for that operation are in the
docstrings, because the direction of a currency conversion is exactly the sort of
thing that is obvious until it is wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from sextant.adapters.storage.panel import PanelRow
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.execution.fx import FxRates, FxRateUnavailable


class PanelIncomplete(ValueError):
    """A series the run cannot proceed without is absent from the store."""


def fx_rates_from(
    panel: Mapping[str, tuple[PanelRow, ...]],
    *,
    symbol: str,
    foreign_currency: str,
    account_currency: str,
) -> FxRates:
    """Account-currency units per foreign unit, from the venue's own pair.

    The pair is quoted as foreign units per account unit, so the rate the engine
    wants is its reciprocal. Each observation is dated by the bar's *close* time,
    never its open time: a rate is knowable when the bar that carries it has
    finished forming.
    """
    rows = panel.get(symbol)
    if not rows:
        raise PanelIncomplete(
            f"No {symbol} series in the store. The currency leg cannot be priced, and "
            "running without it would be the counterfactual wearing the label of the "
            "measurement."
        )
    observations: dict[Timestamp, Decimal] = {}
    for row in rows:
        close = Decimal(row.close)
        if close <= 0:
            continue
        opened = Timestamp.from_epoch_millis(row.open_time_ms)
        observations[opened.plus(Timeframe.D1.duration)] = Decimal(1) / close
    return FxRates.of(
        foreign_currency=foreign_currency,
        account_currency=account_currency,
        observations=observations,
        source=(
            f"{symbol} daily closes from the venue's own public archive, inverted to give "
            "account-currency units per foreign unit, dated by bar close. The pair is the "
            "account's currency against the quote asset directly, so no proxy is assumed."
        ),
    )


def reference_closes_from(
    panel: Mapping[str, tuple[PanelRow, ...]],
    rates: FxRates,
    *,
    symbol: str,
) -> Mapping[Timestamp, Decimal]:
    """The regime reference series, in the account's currency, dated by close."""
    rows = panel.get(symbol)
    if not rows:
        raise PanelIncomplete(f"No {symbol} series in the store; regimes cannot be cut.")
    closes: dict[Timestamp, Decimal] = {}
    for row in rows:
        close = Decimal(row.close)
        if close <= 0:
            continue
        closed_at = Timestamp.from_epoch_millis(row.open_time_ms).plus(Timeframe.D1.duration)
        try:
            rate = rates.rate_at(closed_at)
        except FxRateUnavailable:
            # Before the FX series starts there is no rate, and inventing one
            # would put a currency move into the regime cascade as though it
            # were a price move. The reference series simply starts later.
            continue
        closes[closed_at] = close * rate
    return closes


__all__ = ["PanelIncomplete", "fx_rates_from", "reference_closes_from"]

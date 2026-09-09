"""The accounting. Decimal throughout, and gross minus costs equals net exactly.

Every figure in this module is a ``Decimal``. Not "mostly Decimal", not "Decimal
at the boundaries": there is no float anywhere in the accounting path, and the
identity below holds to the last digit rather than to a tolerance.

    net_pnl == gross_pnl
               - fees - spread - slippage - funding
               - fx_conversion - delisting

A tolerance here would hide exactly the errors worth finding, and the types make
exactness achievable, so exactness is what is asserted -
``tests/unit/test_cost_accounting.py`` generates trade sequences and checks it.

How a rebalance is financed, and why the arithmetic looks the way it does
--------------------------------------------------------------------------

An allocation asks for weight ``w`` of equity in an instrument. The account has
to pay the entry costs out of the same pocket, so what it actually commits to
the asset is a little less than ``w`` of equity. Writing ``c`` for the total
entry cost rate, the capital deployed is ``equity * w``, the amount committed to
the asset is ``equity * w / (1 + c)``, and the entry charge is the difference.

Solved rather than estimated, so that the weights consume exactly the equity
they claim and cash never goes negative by the size of a fee. The quantity
bought is ``committed`` converted into the instrument's quote currency and
divided by its price - so the position is smaller than a cost-free backtest
would give it, which is the first place costs bite.

``gross_pnl`` is then the market move on the quantity actually held, and every
cost is a separate subtraction from it. That is what makes the identity exact:
gross and net describe the same position, differing only by charges that were
each recorded on their own line.

**Uninvested cash earns nothing.** An allocation that puts 60% to work leaves
40% in an account paying zero. That is a real drag and a deliberate one: an
assumed deposit rate is another unmeasured assumption, and this project has
enough.

Pure computation. No I/O, no clock, no venue, no floats.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from sextant.domain.errors import DomainError
from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timestamp
from sextant.engine.execution.markout import SeriesEnd

#: Every account-currency amount is held to twelve decimal places.
#:
#: Not for realism - no venue settles to a picoeuro - but for exactness. Sizing
#: divides by ``1 + cost_rate``, which is a non-terminating division, and an
#: unquantised result carries twenty-eight significant digits. Adding several of
#: those together rounds at the last digit, differently depending on the order,
#: and the gross-minus-costs identity then fails by ten to the minus twenty-six.
#: Quantising every monetary amount to a fixed scale makes the sums exact at any
#: equity this account will ever hold, which is what lets the identity be
#: asserted with ``==`` rather than with a tolerance.
MONEY_SCALE = Decimal("0.000000000001")

ZERO = Notional(Decimal(0))


def money(amount: Decimal) -> Notional:
    """An account-currency amount, quantised to :data:`MONEY_SCALE`."""
    return Notional(amount.quantize(MONEY_SCALE, rounding=ROUND_HALF_EVEN))


class LedgerError(DomainError):
    """The accounting was asked to do something it cannot do consistently."""


@dataclass(frozen=True, slots=True)
class CostLines:
    """Every kind of cost, kept apart. A total alone is not acceptable output.

    ``delisting`` is not a trading cost and is deliberately in the same object
    anyway: it is a mark-out assumption, it changes the result, and a reader
    comparing two runs needs to see which of the two moved.
    """

    fees: Notional = ZERO
    spread: Notional = ZERO
    slippage: Notional = ZERO
    funding: Notional = ZERO
    fx_conversion: Notional = ZERO
    delisting: Notional = ZERO

    @property
    def total(self) -> Notional:
        """Everything, summed. Never reported without the lines beside it."""
        return Notional(
            self.fees.amount
            + self.spread.amount
            + self.slippage.amount
            + self.funding.amount
            + self.fx_conversion.amount
            + self.delisting.amount
        )

    def __add__(self, other: CostLines) -> CostLines:
        return CostLines(
            fees=Notional(self.fees.amount + other.fees.amount),
            spread=Notional(self.spread.amount + other.spread.amount),
            slippage=Notional(self.slippage.amount + other.slippage.amount),
            funding=Notional(self.funding.amount + other.funding.amount),
            fx_conversion=Notional(self.fx_conversion.amount + other.fx_conversion.amount),
            delisting=Notional(self.delisting.amount + other.delisting.amount),
        )

    def as_json(self) -> dict[str, str]:
        """Serialisable form. Every line, always, including the zeroes."""
        return {
            "fees": str(self.fees.amount),
            "spread": str(self.spread.amount),
            "slippage": str(self.slippage.amount),
            "funding": str(self.funding.amount),
            "fx_conversion": str(self.fx_conversion.amount),
            "delisting": str(self.delisting.amount),
            "total": str(self.total.amount),
        }


def sum_costs(lines: Sequence[CostLines]) -> CostLines:
    """Add up cost lines, keeping every line separate."""
    total = CostLines()
    for item in lines:
        total = total + item
    return total


@dataclass(frozen=True, slots=True)
class PositionOutcome:
    """One position held from one rebalance to the next, fully accounted.

    ``entry_rate`` and ``exit_rate`` are account-currency units per unit of the
    instrument's quote currency. Both are exactly ``1`` for a domestic
    instrument and under the counterfactual FX policy, which is what lets the
    same arithmetic serve all three policies without branching.
    """

    key: InstrumentKey
    opened_at: Timestamp
    closed_at: Timestamp
    capital_deployed: Notional
    committed: Notional
    quantity: Quantity
    entry_price: Price
    exit_price: Price
    entry_rate: Decimal
    exit_rate: Decimal
    gross_proceeds: Notional
    """What the position realises in account currency before any charge."""
    entry_costs: CostLines
    exit_costs: CostLines
    """Kept apart from the entry costs because they are settled at different
    moments and out of different pockets. The entry charge is already inside
    ``capital_deployed``; the exit charge comes out of the proceeds. Merging them
    into one figure is what breaks the gross-minus-costs identity, and it broke
    it once before this split existed."""
    series_end: SeriesEnd

    @property
    def costs(self) -> CostLines:
        """Everything this position was charged, entry and exit together."""
        return self.entry_costs + self.exit_costs

    @property
    def realised(self) -> Notional:
        """What the account actually gets back when the position is closed."""
        return Notional(self.gross_proceeds.amount - self.exit_costs.total.amount)

    @property
    def gross_pnl(self) -> Notional:
        """The market move on the quantity actually held."""
        return Notional(self.gross_proceeds.amount - self.committed.amount)

    @property
    def net_pnl(self) -> Notional:
        """What the account gained, after everything."""
        return Notional(self.realised.amount - self.capital_deployed.amount)

    def as_json(self) -> dict[str, object]:
        """Serialisable form, for the decision stream."""
        return {
            "instrument": str(self.key),
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "capital_deployed": str(self.capital_deployed.amount),
            "quantity": str(self.quantity.amount),
            "entry_price": str(self.entry_price.amount),
            "exit_price": str(self.exit_price.amount),
            "entry_rate": str(self.entry_rate),
            "exit_rate": str(self.exit_rate),
            "gross_pnl": str(self.gross_pnl.amount),
            "net_pnl": str(self.net_pnl.amount),
            "series_end": self.series_end.value,
            "costs": self.costs.as_json(),
        }


@dataclass(frozen=True, slots=True)
class RebalanceOutcome:
    """One holding period, from the rebalance that opened it to the one that closed it."""

    opened_at: Timestamp
    closed_at: Timestamp
    equity_before: Notional
    equity_after: Notional
    positions: tuple[PositionOutcome, ...]
    uninvested: Notional
    candidates_considered: int
    note: str = ""

    @property
    def costs(self) -> CostLines:
        """Every cost charged across this holding period."""
        return sum_costs([position.costs for position in self.positions])

    @property
    def gross_pnl(self) -> Notional:
        """Market move across every position held."""
        return Notional(sum((item.gross_pnl.amount for item in self.positions), Decimal(0)))

    @property
    def net_pnl(self) -> Notional:
        """What the account gained across this holding period."""
        return Notional(self.equity_after.amount - self.equity_before.amount)

    def as_json(self) -> dict[str, object]:
        """Serialisable form."""
        return {
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "equity_before": str(self.equity_before.amount),
            "equity_after": str(self.equity_after.amount),
            "positions_held": len(self.positions),
            "candidates_considered": self.candidates_considered,
            "uninvested": str(self.uninvested.amount),
            "gross_pnl": str(self.gross_pnl.amount),
            "net_pnl": str(self.net_pnl.amount),
            "costs": self.costs.as_json(),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Ledger:
    """The whole run: an equity curve, and where every euro of it went.

    ``equity_curve`` has one point per rebalance instant including the terminal
    one, so a run over N holding periods has N+1 points. That is what the return
    series is computed from, and it is why the boundary function refuses a curve
    with fewer than two points.
    """

    account_currency: str
    initial_equity: Notional
    instants: tuple[Timestamp, ...]
    equity_curve: tuple[Notional, ...]
    rebalances: tuple[RebalanceOutcome, ...]

    def __post_init__(self) -> None:
        if len(self.instants) != len(self.equity_curve):
            raise LedgerError(
                f"{len(self.instants)} instants against {len(self.equity_curve)} equity "
                "points. A curve that does not line up with its own clock is not a curve."
            )

    @property
    def terminal_equity(self) -> Notional:
        """Equity at the end of the run."""
        return self.equity_curve[-1] if self.equity_curve else self.initial_equity

    @property
    def costs(self) -> CostLines:
        """Every cost across the whole run, itemised."""
        return sum_costs([item.costs for item in self.rebalances])

    @property
    def gross_pnl(self) -> Notional:
        """Market move across the whole run, on the quantities actually held."""
        return Notional(sum((item.gross_pnl.amount for item in self.rebalances), Decimal(0)))

    @property
    def net_pnl(self) -> Notional:
        """What the account gained across the whole run."""
        return Notional(self.terminal_equity.amount - self.initial_equity.amount)

    @property
    def terminal_return(self) -> Decimal:
        """Net return over the whole run, as a fraction of the starting equity."""
        if self.initial_equity.amount == 0:
            raise LedgerError("An account that started with nothing has no return.")
        return self.net_pnl.amount / self.initial_equity.amount

    @property
    def max_drawdown(self) -> Decimal:
        """Deepest peak-to-trough fall in the equity curve, as a positive fraction.

        Computed in ``Decimal`` from the equity curve rather than from the float
        return series. It is a ratio of two monetary quantities and the exact
        answer is available, so there is no reason to cross the boundary for it.
        """
        peak = self.initial_equity.amount
        worst = Decimal(0)
        for value in self.equity_curve:
            peak = max(peak, value.amount)
            if peak > 0:
                fall = (peak - value.amount) / peak
                worst = max(worst, fall)
        return worst

    def reconciles(self) -> bool:
        """Whether gross minus every cost line equals net, exactly.

        The identity the accounting exists to satisfy. Checked here so that a
        caller can assert it on a real run, not only in a unit test on a
        fixture.
        """
        return self.net_pnl.amount == self.gross_pnl.amount - self.costs.total.amount

    def as_json(self) -> dict[str, object]:
        """Serialisable form: the cost breakdown, never a net figure alone."""
        return {
            "account_currency": self.account_currency,
            "initial_equity": str(self.initial_equity.amount),
            "terminal_equity": str(self.terminal_equity.amount),
            "gross_pnl": str(self.gross_pnl.amount),
            "costs": self.costs.as_json(),
            "net_pnl": str(self.net_pnl.amount),
            "terminal_return": str(self.terminal_return),
            "max_drawdown": str(self.max_drawdown),
            "rebalances": len(self.rebalances),
            "reconciles": self.reconciles(),
        }


@dataclass(slots=True)
class LedgerBuilder:
    """Accumulates a ledger while the engine walks forward."""

    account_currency: str
    initial_equity: Notional
    _instants: list[Timestamp] = field(default_factory=list)
    _curve: list[Notional] = field(default_factory=list)
    _rebalances: list[RebalanceOutcome] = field(default_factory=list)

    def open(self, at: Timestamp) -> None:
        """Record the starting point of the curve."""
        if self._instants:
            raise LedgerError("This ledger has already been opened.")
        self._instants.append(at)
        self._curve.append(self.initial_equity)

    def record(self, outcome: RebalanceOutcome) -> None:
        """Add one completed holding period."""
        if not self._instants:
            raise LedgerError("Record a starting point before recording a holding period.")
        self._rebalances.append(outcome)
        self._instants.append(outcome.closed_at)
        self._curve.append(outcome.equity_after)

    @property
    def equity(self) -> Notional:
        """Equity as it stands."""
        return self._curve[-1] if self._curve else self.initial_equity

    def build(self) -> Ledger:
        """Freeze into a :class:`Ledger`."""
        return Ledger(
            account_currency=self.account_currency,
            initial_equity=self.initial_equity,
            instants=tuple(self._instants),
            equity_curve=tuple(self._curve),
            rebalances=tuple(self._rebalances),
        )


def merge(ledgers: Sequence[Ledger]) -> Ledger:
    """Chain ledgers end to end, as consecutive out-of-sample folds.

    Each fold is run from the equity the previous one ended at, so the merged
    curve is the account's actual path across the whole out-of-sample period.
    The alternative - restarting every fold at the initial equity and averaging
    the returns - hides compounding and hides a drawdown that spans a fold
    boundary.
    """
    if not ledgers:
        raise LedgerError("Cannot merge an empty sequence of ledgers.")
    instants: list[Timestamp] = [ledgers[0].instants[0]]
    curve: list[Notional] = [ledgers[0].equity_curve[0]]
    rebalances: list[RebalanceOutcome] = []
    for ledger in ledgers:
        instants.extend(ledger.instants[1:])
        curve.extend(ledger.equity_curve[1:])
        rebalances.extend(ledger.rebalances)
    return Ledger(
        account_currency=ledgers[0].account_currency,
        initial_equity=ledgers[0].initial_equity,
        instants=tuple(instants),
        equity_curve=tuple(curve),
        rebalances=tuple(rebalances),
    )


def monthly_returns(ledger: Ledger) -> Mapping[Timestamp, Decimal]:
    """The return of each holding period, keyed by the instant it closed.

    Exact, in ``Decimal``, and computed before anything crosses the numeric
    boundary. What crosses is this, not the equity.
    """
    returns: dict[Timestamp, Decimal] = {}
    for index in range(1, len(ledger.equity_curve)):
        previous = ledger.equity_curve[index - 1].amount
        if previous == 0:
            raise LedgerError(
                f"Equity is zero at {ledger.instants[index - 1].isoformat()}; the next "
                "return is undefined. A wiped-out account is reported as one."
            )
        returns[ledger.instants[index]] = ledger.equity_curve[index].amount / previous - Decimal(1)
    return returns

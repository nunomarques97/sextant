"""The walk-forward, cross-sectional backtest engine.

Walk-forward is the only mode
------------------------------

There is no ``run_in_sample``. There is no flag that turns the fitting window
into a scored window. :meth:`BacktestEngine.run` takes a
:class:`~sextant.engine.backtest.window.WalkForwardPlan`, fits each fold's
strategy on that fold's in-sample window, and produces a ledger from the
out-of-sample window only. The fitting step returns a
:class:`~sextant.engine.backtest.allocation.FitRecord`, which by construction
carries parameters and no performance.

Cross-sectional by construction
--------------------------------

At every rebalance the engine resolves the point-in-time executable universe,
hands the *whole* candidate set to the allocator in a deterministic order, and
executes the weights that come back. An allocator that ignores the rest of the
set is expressible and is labelled ``is_cross_sectional = False`` in the run
metadata, so a per-symbol signal can never be reported as a cross-sectional
result.

Driven by the ports
--------------------

Prices arrive through ``BarRepository``. Time arrives through ``Clock``. The
engine never reads a store, a file or a wall clock, which is what makes the
claim "backtest, paper and live run the same engine" testable rather than
architectural - drive it with a different clock and a different repository and
nothing else changes.

Prices used, and why they cannot look ahead
--------------------------------------------

A rebalance at the first instant of a month executes at the last daily close
that had *finished forming* before that instant - in practice the last day of
the previous month. A position opened at one rebalance is closed at the next,
at the same kind of price. So a holding period's return is the move between two
closes that were both observable when the respective decisions were made, and
the engine has no path to a price it could not have seen.

An instrument whose series stops mid-period is marked out at its last observed
close, with the delisting haircut applied when - and only when - a source says
it was delisted.

Decimal throughout. No floats, no venue, no I/O.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Protocol, runtime_checkable

from sextant.domain.decision import DecisionRecord
from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.risk import RiskVerdict
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.backtest.allocation import (
    Allocation,
    Allocator,
    FitRecord,
    Strategy,
    TargetWeights,
)
from sextant.engine.backtest.ledger import (
    CostLines,
    Holding,
    Ledger,
    LedgerBuilder,
    RebalanceOutcome,
    Trade,
    merge,
    money,
)
from sextant.engine.backtest.market import PointInTimeView
from sextant.engine.backtest.window import (
    WalkForwardFold,
    WalkForwardPlan,
    Window,
    add_months,
    monthly_instants,
)
from sextant.engine.execution.costs import ItemisedCostModel
from sextant.engine.execution.funding import FundingSchedule
from sextant.engine.execution.fx import CurrencyRouting, FxLeg
from sextant.engine.execution.markout import DelistingHaircut, SeriesEnd
from sextant.ports.clock import AdvanceableClock, Clock
from sextant.ports.repository import BarRepository

#: How far back a view reaches. Wide enough to find the last close of an
#: instrument whose series stopped at a quarter boundary - the worst case this
#: archive produces - and finite so that the recorded read set in the look-ahead
#: tests means something. A view that could read from the beginning of time would
#: make a strategy's lookback invisible.
DEFAULT_LOOKBACK_DAYS = 120


class EngineError(DomainError):
    """The engine was asked to run something it cannot run consistently."""


@runtime_checkable
class ExecutableUniverse(Protocol):
    """The point-in-time set of instruments this account could actually trade."""

    @property
    def policy_name(self) -> str:
        """Recorded in the run manifest, so a universe is reproducible."""
        ...

    def executable_at(self, at: Timestamp) -> tuple[InstrumentKey, ...]:
        """Members at ``at``, in a deterministic order, using only data from before it."""
        ...


@runtime_checkable
class SeriesEndOracle(Protocol):
    """Why an instrument's observed history stops, at a given instant."""

    def series_end_at(self, key: InstrumentKey, at: Timestamp) -> SeriesEnd:
        """Whether a series that has stopped by ``at`` stopped because of a delisting."""
        ...


@dataclass(frozen=True, slots=True)
class _Held:
    """One position in the book: what it is worth, and what it was last marked at.

    **Value is the tracked state, not a coin count.** A position's value evolves
    by the price relative and the currency relative together, which is exactly
    what ``quantity * price * rate`` does without ever recomputing it from a
    quantity. Tracking value directly is what makes the accounting identity
    exact: every trade adds a quantised amount to a quantised value, so nothing
    rounds on its way through a division and back again. The coin count is still
    reported - it is ``value / (price * rate)`` - but it is derived for the audit
    record rather than carried as state.
    """

    value: Notional
    price: Price
    rate: Decimal

    @property
    def quantity(self) -> Quantity:
        """How much of the base asset this position represents."""
        if self.price.amount == 0 or self.rate == 0:
            return Quantity(Decimal(0))
        return Quantity(self.value.amount / self.rate / self.price.amount)


@dataclass(frozen=True, slots=True)
class _Step:
    """What one pass of trading produced: a new book, new cash, and the trades."""

    book: Mapping[InstrumentKey, _Held]
    cash: Notional
    trades: tuple[Trade, ...]


@dataclass(frozen=True, slots=True)
class _FoldOutcome:
    """One fold's ledger, and the account state the next fold inherits."""

    ledger: Ledger
    decisions: tuple[DecisionRecord, ...]
    universe_sizes: Mapping[str, int]
    book: Mapping[InstrumentKey, _Held]
    cash: Notional
    holds_foreign_currency: bool
    """Whether the account's capital is sitting in the quote currency at the boundary.

    Carried across folds because a fold boundary is a reporting boundary: an account
    that was in USDT at the end of fold one is in USDT at the start of fold two, and
    charging it to convert back and forth across a line drawn in a report would be
    charging a cost nobody pays. It is state about the *run*, so it travels through the
    outcome rather than living on the engine, which stores nothing about a run.
    """


@dataclass(frozen=True, slots=True)
class _Intent:
    """One trade the allocation wants, before the cash constraint is applied."""

    key: InstrumentKey
    price: Price
    rate: Decimal
    current: Decimal
    delta: Decimal
    cost_rate: Decimal


def _buy_scale(intents: Sequence[_Intent], cash: Notional) -> Decimal:
    """How much of the intended buying the account can actually pay for.

    Sells settle first and their charges come out of the proceeds; what remains
    has to cover the buys *and* the charges on the buys. Every cost line in this
    engine is proportional to notional, so the constraint is linear and the
    factor is exact::

        available = cash + sells - charges_on_sells
        needed = buys + charges_on_buys
        scale = min(1, available / needed)

    which is ``1 / (1 + c)`` for a fully invested allocation from cash. Returns
    one when there is nothing to buy, or when the account can pay in full.
    """
    buys = Decimal(0)
    buy_charges = Decimal(0)
    proceeds = Decimal(0)
    sell_charges = Decimal(0)
    for intent in intents:
        if intent.delta > 0:
            buys += intent.delta
            buy_charges += intent.delta * intent.cost_rate
        else:
            proceeds += -intent.delta
            sell_charges += -intent.delta * intent.cost_rate
    needed = buys + buy_charges
    if buys <= 0 or needed <= 0:
        return Decimal(1)
    available = cash.amount + proceeds - sell_charges
    if available >= needed:
        return Decimal(1)
    if available <= 0:
        return Decimal(0)
    return available / needed


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One walk-forward run: the ledger, the folds, and what was fitted."""

    ledger: Ledger
    """Out-of-sample only. There is no in-sample ledger anywhere in this system."""
    fold_ledgers: tuple[Ledger, ...]
    fits: tuple[FitRecord, ...]
    decisions: tuple[DecisionRecord, ...]
    universe_sizes: Mapping[str, int]
    """Candidate count at every rebalance instant, keyed by ISO instant."""
    is_cross_sectional: bool
    allocator_name: str
    parameter_set_id: str

    def as_json(self) -> dict[str, object]:
        """Serialisable form. The cost breakdown comes with it, always."""
        return {
            "allocator": self.allocator_name,
            "parameter_set_id": self.parameter_set_id,
            "is_cross_sectional": self.is_cross_sectional,
            "out_of_sample": self.ledger.as_json(),
            "folds": [item.as_json() for item in self.fold_ledgers],
            "fits": [item.as_json() for item in self.fits],
            "universe_sizes": dict(sorted(self.universe_sizes.items())),
            "decisions_recorded": len(self.decisions),
        }


@dataclass(frozen=True, slots=True)
class BacktestEngine:
    """The engine. One instance per data set and cost configuration.

    Reusable across many strategies and many seeds: nothing about a run is
    stored on the engine, so two runs of the same engine with the same inputs
    produce the same output and two runs with different strategies cannot
    contaminate each other.
    """

    repository: BarRepository
    clock: Clock
    timeframe: Timeframe
    instruments: Mapping[InstrumentKey, Instrument]
    universe: ExecutableUniverse
    cost_model: ItemisedCostModel
    fx: FxLeg
    routing: CurrencyRouting
    haircut: DelistingHaircut
    series_end: SeriesEndOracle
    initial_equity: Notional
    account_currency: str
    funding: FundingSchedule | None = None
    """Realised, published funding per instrument. ``None`` keeps the flat
    ``cost_model.funding_bps_per_day`` behaviour every result before SEXTANT-006
    was produced under, which for spot is a structural zero reported as its own
    line. A schedule here replaces that line with what the venue actually
    settled, signed by the side of the position."""
    haircut_is_a_loss_on_either_side: bool = False
    """Whether a delisting write-down costs a short as well as a long.

    False is the long-only reading: the write-down is a fraction of the
    position's signed value, so a short would *gain* from it. That is the
    behaviour every published result rests on and it is correct for a book that
    cannot short.

    True makes the write-down a loss of ``fraction * abs(value)`` whichever way
    the position faces. Nothing in this project charges it to a perpetual - a
    delisted contract is settled by the venue against an index rather than dumped
    - but a two-sided book needs the option to exist, and a family that wants a
    long leg written down while its short leg is not must say so by pricing the
    two legs through different oracles rather than by relying on a sign."""
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    record_decisions: bool = True
    """Off for the null sweep, where ten thousand runs would produce tens of
    millions of records nobody will read. The baselines that are reported are
    always run with it on."""

    _advance: bool = field(default=True, repr=False)
    """Whether to drive a simulated clock forward. False when the clock is not
    ours to advance, which is the paper and live case."""

    _views: dict[Timestamp, PointInTimeView] = field(default_factory=dict, repr=False)
    """Views, kept per instant across runs of this engine.

    A view is a read-through cache of the port at a fixed instant, so two runs
    at the same instant would have received identical data anyway; sharing it
    changes nothing about what any run can see. It is here because the null
    experiment runs the same thirty instants ten thousand times, and rebuilding
    the same thirty views three hundred thousand times is the difference between
    minutes and hours.

    The cache belongs to the engine instance. A test that mutates the underlying
    data builds a new engine, and therefore sees the new data."""

    _candidates_cache: dict[Timestamp, tuple[Instrument, ...]] = field(
        default_factory=dict, repr=False
    )
    """The resolved universe per instant, cached for the same reason."""

    # -- the public entry point ----------------------------------------------

    def with_clock(self, clock: Clock) -> BacktestEngine:
        """The same engine and the same caches, driven by a different clock.

        A run is one simulation and a simulation starts at its own beginning, so
        a second run needs a clock that has not already reached the end of the
        first - a simulated clock refuses to move backwards, and rightly. This
        is how the null experiment runs ten thousand simulations over one
        engine: the data caches are shared, because they are read-through views
        of the port and cannot differ between runs, while the clock is fresh
        each time.
        """
        return BacktestEngine(
            repository=self.repository,
            clock=clock,
            timeframe=self.timeframe,
            instruments=self.instruments,
            universe=self.universe,
            cost_model=self.cost_model,
            fx=self.fx,
            routing=self.routing,
            haircut=self.haircut,
            series_end=self.series_end,
            initial_equity=self.initial_equity,
            account_currency=self.account_currency,
            funding=self.funding,
            haircut_is_a_loss_on_either_side=self.haircut_is_a_loss_on_either_side,
            lookback_days=self.lookback_days,
            record_decisions=self.record_decisions,
            _advance=self._advance,
            _views=self._views,
            _candidates_cache=self._candidates_cache,
        )

    def recording(self, *, record_decisions: bool) -> BacktestEngine:
        """The same engine with the decision stream switched on or off."""
        engine = self.with_clock(self.clock)
        return BacktestEngine(
            repository=engine.repository,
            clock=engine.clock,
            timeframe=engine.timeframe,
            instruments=engine.instruments,
            universe=engine.universe,
            cost_model=engine.cost_model,
            fx=engine.fx,
            routing=engine.routing,
            haircut=engine.haircut,
            series_end=engine.series_end,
            initial_equity=engine.initial_equity,
            account_currency=engine.account_currency,
            funding=engine.funding,
            haircut_is_a_loss_on_either_side=engine.haircut_is_a_loss_on_either_side,
            lookback_days=engine.lookback_days,
            record_decisions=record_decisions,
            _advance=engine._advance,
            _views=engine._views,
            _candidates_cache=engine._candidates_cache,
        )

    def run(self, plan: WalkForwardPlan, strategy: Strategy, run_id: str) -> RunSummary:
        """Fit and evaluate ``strategy`` across every fold of ``plan``.

        The returned ledger covers the out-of-sample windows and nothing else.
        Folds are chained, so fold two starts from the equity fold one ended
        with and a drawdown spanning the boundary is visible.
        """
        fold_ledgers: list[Ledger] = []
        fits: list[FitRecord] = []
        decisions: list[DecisionRecord] = []
        sizes: dict[str, int] = {}
        book: Mapping[InstrumentKey, _Held] = {}
        cash = self.initial_equity
        holds_foreign = False
        allocator_name = strategy.name
        parameter_set_id = "unfitted"
        cross_sectional = False

        for fold in plan.folds:
            allocator, fit = self._fit(fold, strategy)
            fits.append(
                FitRecord(
                    fold_index=fold.index,
                    window=fold.in_sample,
                    allocator_name=fit.allocator_name,
                    parameter_set_id=fit.parameter_set_id,
                    is_cross_sectional=fit.is_cross_sectional,
                    chosen_parameters=fit.chosen_parameters,
                )
            )
            allocator_name = allocator.name
            parameter_set_id = allocator.parameter_set_id
            cross_sectional = allocator.is_cross_sectional

            outcome = self._evaluate(
                window=fold.out_of_sample,
                allocator=allocator,
                book=book,
                cash=cash,
                run_id=run_id,
                liquidate=fold.index == plan.folds[-1].index,
                holds_foreign=holds_foreign,
            )
            fold_ledgers.append(outcome.ledger)
            decisions.extend(outcome.decisions)
            sizes.update(outcome.universe_sizes)
            book, cash = outcome.book, outcome.cash
            holds_foreign = outcome.holds_foreign_currency

        return RunSummary(
            ledger=merge(fold_ledgers),
            fold_ledgers=tuple(fold_ledgers),
            fits=tuple(fits),
            decisions=tuple(decisions),
            universe_sizes=sizes,
            is_cross_sectional=cross_sectional,
            allocator_name=allocator_name,
            parameter_set_id=parameter_set_id,
        )

    # -- fitting -------------------------------------------------------------

    def _fit(self, fold: WalkForwardFold, strategy: Strategy) -> tuple[Allocator, FitRecord]:
        """Fit on the in-sample window, through a view that ends where it ends.

        The view's ``as_of`` is the in-sample window's end, so a fitting
        procedure cannot read a single bar from the window it is about to be
        judged on. That is the structural guarantee, and it is here rather than
        in a strategy's good manners.
        """
        view = PointInTimeView.at(
            self.repository,
            self.timeframe,
            fold.in_sample.end,
            lookback_days=self.lookback_days,
        )
        return strategy.fit(fold.in_sample, view)

    # -- evaluation ----------------------------------------------------------

    def _evaluate(
        self,
        *,
        window: Window,
        allocator: Allocator,
        book: Mapping[InstrumentKey, _Held],
        cash: Notional,
        run_id: str,
        liquidate: bool,
        holds_foreign: bool = False,
    ) -> _FoldOutcome:
        """Walk one out-of-sample window, rebalancing monthly.

        Positions carry across rebalances **and across folds**. A fold boundary
        is a reporting boundary, not an instruction to the account to sell
        everything and buy it back: liquidating at each boundary would charge a
        three-fold walk-forward two round trips it would never pay, and would do
        it to the benchmarks as well as to the strategies. Only the last fold
        sells out, so the closing equity is cash and every construct pays exactly
        one exit.
        """
        instants = [*monthly_instants(window), window.end]
        opening = self._reprice(book, self._view(instants[0]))
        builder = LedgerBuilder(
            account_currency=self.account_currency,
            initial_equity=money(cash.amount + _total(opening)),
        )
        builder.open(instants[0])
        decisions: list[DecisionRecord] = []
        sizes: dict[str, int] = {}
        last = len(instants) - 2

        for index, (opened_at, closed_at) in enumerate(pairwise(instants)):
            self._advance_clock(opened_at)
            view = self._view(opened_at)
            book = self._reprice(book, view)
            equity_before = money(cash.amount + _total(book))

            candidates = self._candidates(opened_at)
            sizes[opened_at.isoformat()] = len(candidates)
            allocation = allocator.allocate(candidates, opened_at, view)

            step = self._trade_to(book, allocation, equity_before, cash, opened_at, view)
            book, cash = step.book, step.cash
            invested_after = money(_total(book))
            holdings = self._holdings(book, opened_at)
            as_traded = book

            self._advance_clock(closed_at)
            close_view = self._view(closed_at)
            book = self._reprice(book, close_view)
            gain = money(_total(book) - invested_after.amount)

            financing = self._financing(as_traded, opened_at, closed_at, close_view)
            cash = money(cash.amount - financing.total.amount)
            trades = step.trades

            if index == last and liquidate:
                closing = self._trade_to(
                    book,
                    Allocation(weights=(), at=closed_at, candidates_considered=0),
                    money(cash.amount + _total(book)),
                    cash,
                    closed_at,
                    close_view,
                )
                book, cash = closing.book, closing.cash
                trades = (*trades, *closing.trades)

            # Rule A12.9. The currency crossing is charged here, once, on the account's
            # equity - not inside each trade on its notional. It fires only when the
            # account's capital actually changes currency: entering on the rebalance that
            # opens the first foreign position, leaving on the one that closes the last.
            now_foreign = self._holds_foreign(book)
            crossing = self._crossing_cost(
                was_foreign=holds_foreign,
                is_foreign=now_foreign,
                capital=equity_before if now_foreign else money(cash.amount + _total(book)),
            )
            cash = money(cash.amount - crossing.total.amount)
            financing = financing + crossing
            holds_foreign = now_foreign

            outcome = RebalanceOutcome(
                opened_at=opened_at,
                closed_at=closed_at,
                equity_before=equity_before,
                equity_after=money(cash.amount + _total(book)),
                trades=trades,
                holdings=holdings,
                market_gain=gain,
                financing=financing,
                cash=cash,
                candidates_considered=len(candidates),
                turnover=money(sum((abs(item.notional.amount) for item in trades), Decimal(0))),
                note=allocation.note,
            )
            builder.record(outcome)
            if self.record_decisions:
                decisions.extend(self._decisions(run_id, allocator, outcome))

        return _FoldOutcome(
            ledger=builder.build(),
            decisions=tuple(decisions),
            universe_sizes=sizes,
            book=book,
            cash=cash,
            holds_foreign_currency=holds_foreign,
        )

    # -- the book ------------------------------------------------------------

    def _reprice(
        self, book: Mapping[InstrumentKey, _Held], view: PointInTimeView
    ) -> dict[InstrumentKey, _Held]:
        """Mark every position at the latest price and rate knowable to ``view``.

        A position moves by the price relative and the currency relative
        together, which is what makes an unhedged foreign holding behave the way
        one actually behaves. A position whose instrument has no bar inside the
        lookback keeps the price it was last marked at: a stale mark is honest
        where an invented price is not, and the instrument is on its way out of
        the universe regardless.
        """
        repriced: dict[InstrumentKey, _Held] = {}
        for key, held in book.items():
            instrument = self.instruments[key]
            quoted = view.last_close(instrument)
            price = quoted if quoted is not None and quoted.amount > 0 else held.price
            rate = self._rate_for(instrument.symbol, view.as_of)
            ratio = (price.amount / held.price.amount) * (rate / held.rate)
            repriced[key] = _Held(value=money(held.value.amount * ratio), price=price, rate=rate)
        return repriced

    def _holdings(self, book: Mapping[InstrumentKey, _Held], at: Timestamp) -> tuple[Holding, ...]:
        """The book as marked-to-market holdings, in a stable order."""
        return tuple(
            Holding(
                key=key,
                quantity=book[key].quantity,
                price=book[key].price,
                rate=book[key].rate,
                value=book[key].value,
                series_end=self.series_end.series_end_at(key, at),
            )
            for key in sorted(book)
        )

    def _trade_to(
        self,
        book: Mapping[InstrumentKey, _Held],
        allocation: TargetWeights,
        equity: Notional,
        cash: Notional,
        at: Timestamp,
        view: PointInTimeView,
    ) -> _Step:
        """Trade the difference between what is held and what is wanted.

        Three things happen here and they happen in this order.

        **Delisted positions are written down and sold outright.** A position
        whose instrument a source says is gone cannot be held, and it cannot be
        sold at its last observed close either, because that close predates the
        news the haircut stands in for. The write-down is charged on its own
        line and never folded into a fee.

        **The buys are scaled to the cash that actually exists.** An allocation
        asking for all of equity would otherwise leave the account short by the
        size of its own fees. The scaling factor is solved rather than
        estimated - every cost line here is proportional to notional, so the
        cash constraint is linear - and it comes out at exactly ``1/(1 + c)``
        for a fully invested allocation, which is what a real account can pay
        for. A future non-proportional cost breaks this loudly rather than
        quietly.

        **Nothing is traded for an instrument whose price is not knowable.** Its
        position is carried at its stale mark, which is the only honest thing to
        do with a holding nobody can price.
        """
        written_down: dict[InstrumentKey, Notional] = {}
        values: dict[InstrumentKey, Decimal] = {}
        for holding in self._holdings(book, at):
            if holding.series_end.takes_haircut:
                base = (
                    abs(holding.value.amount)
                    if self.haircut_is_a_loss_on_either_side
                    else holding.value.amount
                )
                charge = money(base * self.haircut.fraction)
                written_down[holding.key] = charge
                values[holding.key] = holding.value.amount - charge.amount
            else:
                values[holding.key] = holding.value.amount

        haircut_total = sum((item.amount for item in written_down.values()), Decimal(0))
        investable = money(equity.amount - haircut_total)
        targets = {
            key: money(investable.amount * weight).amount for key, weight in allocation.weights
        }

        intents: list[_Intent] = []
        for key in sorted(set(values) | set(targets)):
            instrument = self.instruments[key]
            held = book.get(key)
            price = held.price if held is not None else view.last_close(instrument)
            if price is None or price.amount <= 0:
                continue
            rate = held.rate if held is not None else self._rate_for(instrument.symbol, at)
            current = values.get(key, Decimal(0))
            wanted = Decimal(0) if key in written_down else targets.get(key, Decimal(0))
            intents.append(
                _Intent(
                    key=key,
                    price=price,
                    rate=rate,
                    current=current,
                    delta=wanted - current,
                    cost_rate=self._cost_rate(key, at),
                )
            )

        scale = _buy_scale(intents, cash)
        trades: list[Trade] = []
        updated: dict[InstrumentKey, _Held] = {}
        cash_amount = cash.amount

        for intent in intents:
            delta = money(intent.delta * scale if intent.delta > 0 else intent.delta)
            write_down = written_down.get(intent.key)
            if delta.amount == 0 and write_down is None:
                if intent.current != 0:
                    updated[intent.key] = _Held(
                        value=money(intent.current), price=intent.price, rate=intent.rate
                    )
                continue

            costs = self._trade_costs(intent.key, delta, at)
            if write_down is not None:
                costs = costs + CostLines(delisting=write_down)
            trades.append(
                Trade(
                    key=intent.key,
                    at=at,
                    notional=delta,
                    quantity=Quantity(delta.amount / intent.rate / intent.price.amount),
                    price=intent.price,
                    rate=intent.rate,
                    costs=costs,
                )
            )
            cash_amount -= delta.amount + costs.total.amount - costs.delisting.amount
            remaining = money(intent.current + delta.amount)
            if remaining.amount != 0:
                updated[intent.key] = _Held(value=remaining, price=intent.price, rate=intent.rate)

        return _Step(book=updated, cash=money(cash_amount), trades=tuple(trades))

    def _cost_rate(self, key: InstrumentKey, at: Timestamp) -> Decimal:
        """Total trading charge as a rate on notional, for the cash constraint.

        The currency conversion is deliberately absent: it is not a rate on notional any
        more, so including it here would reserve cash for a charge this trade does not
        incur and would shrink every buy by a tenth of a per cent for no reason.
        """
        return self._trade_costs(key, Notional(Decimal(1)), at).total.amount

    def _financing(
        self,
        book: Mapping[InstrumentKey, _Held],
        opened_at: Timestamp,
        closed_at: Timestamp,
        view: PointInTimeView,
    ) -> CostLines:
        """Financing on the book over the holding period.

        Charged per day held rather than per trade, which is the whole point of
        the distinction.

        **Two shapes, and which one applies is a property of the data, not a
        setting.** Without a :class:`~sextant.engine.execution.funding.FundingSchedule`
        this is the flat ``funding_bps_per_day`` on the book: zero for spot,
        which is every position in this project before SEXTANT-006, and reported
        as its own line regardless, because a cost that is structurally absent
        and one nobody measured look identical in a report that omits the row.

        With a schedule it is the sum, over every settlement the venue actually
        published inside ``(opened_at, closed_at]``, of the position's marked
        value at that settlement times the rate settled there. The value is
        marked forward from the book **as it was traded at the start of the
        period** by the price relative and the currency relative to the
        settlement instant, which is the same arithmetic :meth:`_reprice` does
        and has to be, or a position would accrue funding on a notional it never
        had. The sign needs no special case: value is signed, so a short with a
        positive rate produces a negative charge, which is a receipt.
        """
        if self.funding is None:
            if self.cost_model.funding_bps_per_day == 0:
                return CostLines()
            days = (closed_at.value - opened_at.value).days
            flat = Decimal(0)
            for held in book.values():
                flat += self.cost_model.funding_over(held.value, days).amount
            return CostLines(funding=money(flat))

        total = Decimal(0)
        for key, held in book.items():
            settlements = self.funding.settlements(key, opened_at, closed_at)
            if not settlements:
                continue
            instrument = self.instruments[key]
            series = view.bars([instrument])[key]
            stamps = [bar.close_time.epoch_millis for bar in series]
            for item in settlements:
                index = bisect_right(stamps, item.at.epoch_millis) - 1
                price = series[index].close if index >= 0 else held.price
                if price.amount <= 0:
                    price = held.price
                rate = self._rate_for(instrument.symbol, item.at)
                marked = held.value.amount * (price.amount / held.price.amount) * (rate / held.rate)
                total += marked * item.rate
        return CostLines(funding=money(total))

    def _rate_for(self, symbol: str, at: Timestamp) -> Decimal:
        """Account-currency units per unit of this instrument's quote currency."""
        if not self.routing.is_foreign(symbol):
            return Decimal(1)
        return self.fx.rate_at(at)

    # -- costs ---------------------------------------------------------------

    def _trade_costs(self, key: InstrumentKey, notional: Notional, at: Timestamp) -> CostLines:
        """Fees, spread and slippage on one trade.

        Charged on the absolute notional traded. A rebalance that leaves a
        position where it is trades nothing and is charged nothing, which is the
        difference between a passive benchmark that costs four percent a year
        and one that costs twenty-six.

        **No currency conversion.** Amendment 12, rule A12.9: the conversion charge
        scales with the number of times capital crosses a currency boundary and never
        with turnover. Rotating between two instruments quoted in the same foreign
        currency moves no capital across any boundary - selling one into the quote asset
        and buying the other out of it is one currency throughout - so charging it per
        trade double-counted the line on every rebalance after the first.
        :meth:`_crossing_cost` charges it where it is actually incurred.
        """
        trade = self.cost_model.cost_of(key, notional, at)
        return CostLines(
            fees=money(trade.fee.amount),
            spread=money(trade.spread.amount),
            slippage=money(trade.slippage.amount),
        )

    def _holds_foreign(self, book: Mapping[InstrumentKey, _Held]) -> bool:
        """Whether any of the account's capital is currently in a foreign currency.

        Read off the book rather than off the trades, because the question is where the
        capital *is* and not what was done to it. An empty book is capital in the
        account's own currency; any open foreign-quoted position means it is not.
        """
        return any(
            self.routing.is_foreign(self.instruments[key].symbol)
            for key, held in book.items()
            if held.value.amount != 0
        )

    def _crossing_cost(
        self, *, was_foreign: bool, is_foreign: bool, capital: Notional
    ) -> CostLines:
        """The conversion charge for one currency crossing, or nothing.

        Two crossings in a run that stays invested: capital enters the quote currency
        when the first foreign position is opened and leaves it when the last one is
        closed. A strategy that goes fully to cash and back in pays for both of those
        crossings too, because it really did make them.

        ``capital`` is the account's equity at the crossing instant, which is the amount
        that changes currency. Not the notional traded: a levered or market-neutral book
        moves more notional than it has capital, and the notional never touches the
        account's own currency at all.
        """
        if was_foreign == is_foreign:
            return CostLines()
        return CostLines(fx_conversion=money(self.fx.conversion_cost(capital).amount))

    # -- plumbing ------------------------------------------------------------

    def _view(self, at: Timestamp) -> PointInTimeView:
        view = self._views.get(at)
        if view is None:
            view = PointInTimeView.at(
                self.repository, self.timeframe, at, lookback_days=self.lookback_days
            )
            self._views[at] = view
        return view

    def _advance_clock(self, at: Timestamp) -> None:
        """Bring the clock to ``at``, and refuse to act before it has got there.

        The engine holds a ``Clock``. A simulated one can be advanced and is; a
        wall clock cannot be and is not. The test is structural - a protocol
        that declares ``advance_to`` - rather than an isinstance check against a
        concrete adapter, which the engine is forbidden to import.

        Either way the clock is load-bearing: a rebalance instant the clock has
        not reached is refused. In a backtest that can only fire if the plan and
        the driver disagree; in a live run it is the thing that stops the engine
        acting on a bar that has not happened.
        """
        if self._advance and isinstance(self.clock, AdvanceableClock):
            self.clock.advance_to(at)
        now = self.clock.now()
        if now < at:
            raise EngineError(
                f"The clock reads {now.isoformat()} but the engine was asked to "
                f"rebalance at {at.isoformat()}. Acting on an instant that has not "
                "arrived is the definition of look-ahead."
            )

    def _candidates(self, at: Timestamp) -> tuple[Instrument, ...]:
        """The executable universe at ``at``, as instruments, in a stable order.

        Sorted by key rather than left in whatever order the universe produced.
        Iteration order that depends on a set or a dictionary is the commonest
        cause of a backtest that is not reproducible, and sorting here is
        cheaper than finding that out later.
        """
        cached = self._candidates_cache.get(at)
        if cached is not None:
            return cached
        keys = self.universe.executable_at(at)
        resolved = tuple(self.instruments[key] for key in sorted(keys) if key in self.instruments)
        self._candidates_cache[at] = resolved
        return resolved

    def _decisions(
        self,
        run_id: str,
        allocator: Allocator,
        outcome: RebalanceOutcome,
    ) -> tuple[DecisionRecord, ...]:
        """One audit record per trade actually placed.

        Per trade rather than per position, because a rebalance that holds still
        placed no order and there is nothing to audit. The records that exist
        are the ones a live run would have sent to the venue.
        """
        return tuple(
            DecisionRecord(
                timestamp=trade.at,
                run_id=run_id,
                symbol=trade.key.symbol,
                venue=trade.key.venue,
                regime=None,
                strategy=allocator.name,
                signal=(
                    f"{'buy' if trade.is_buy else 'sell'} "
                    f"cross_sectional={allocator.is_cross_sectional}"
                ),
                llm_decision=None,
                confidence=None,
                entry=trade.price,
                stop=None,
                target=None,
                size=trade.quantity,
                expected_edge_bps=None,
                expected_costs_bps=_bps_of(trade.costs.total, trade.notional),
                net_expected_edge_bps=None,
                risk_verdict=RiskVerdict.APPROVE,
                risk_reason=(
                    "no risk engine is wired in SEXTANT-004; every baseline allocation "
                    "is executed as produced"
                ),
                outcome=None,
            )
            for trade in outcome.trades
        )


def _total(book: Mapping[InstrumentKey, _Held]) -> Decimal:
    """What the whole book is worth, in account currency."""
    return sum((held.value.amount for held in book.values()), Decimal(0))


def _bps_of(amount: Notional, base: Notional) -> Decimal | None:
    """``amount`` as basis points of ``base``, or None when the base is zero."""
    if base.amount == 0:
        return None
    return amount.amount / abs(base.amount) * Decimal(10_000)


def usable_window(first_month: Timestamp, months: int) -> Window:
    """A month-aligned window of ``months`` starting at ``first_month``."""
    return Window(start=first_month, end=add_months(first_month, months))


def rebalance_count(window: Window) -> int:
    """How many holding periods a monthly rebalance produces over ``window``."""
    return len(monthly_instants(window))

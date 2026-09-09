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
)
from sextant.engine.backtest.ledger import (
    CostLines,
    Ledger,
    LedgerBuilder,
    PositionOutcome,
    RebalanceOutcome,
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
        equity = self.initial_equity
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

            ledger, fold_decisions, fold_sizes = self._evaluate(
                window=fold.out_of_sample,
                allocator=allocator,
                equity=equity,
                run_id=run_id,
            )
            fold_ledgers.append(ledger)
            decisions.extend(fold_decisions)
            sizes.update(fold_sizes)
            equity = ledger.terminal_equity

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
        equity: Notional,
        run_id: str,
    ) -> tuple[Ledger, tuple[DecisionRecord, ...], Mapping[str, int]]:
        """Walk one out-of-sample window, rebalancing monthly."""
        instants = [*monthly_instants(window), window.end]
        builder = LedgerBuilder(
            account_currency=self.account_currency,
            initial_equity=equity,
        )
        builder.open(instants[0])
        decisions: list[DecisionRecord] = []
        sizes: dict[str, int] = {}

        for opened_at, closed_at in pairwise(instants):
            self._advance_clock(opened_at)
            open_view = self._view(opened_at)
            candidates = self._candidates(opened_at)
            sizes[opened_at.isoformat()] = len(candidates)
            allocation = allocator.allocate(candidates, opened_at, open_view)

            self._advance_clock(closed_at)
            close_view = self._view(closed_at)
            outcome = self._hold(
                allocation=allocation,
                equity=builder.equity,
                opened_at=opened_at,
                closed_at=closed_at,
                open_view=open_view,
                close_view=close_view,
                candidates_considered=len(candidates),
            )
            builder.record(outcome)
            if self.record_decisions:
                decisions.extend(self._decisions(run_id, allocator, allocation, outcome, opened_at))

        return builder.build(), tuple(decisions), sizes

    def _hold(
        self,
        *,
        allocation: Allocation,
        equity: Notional,
        opened_at: Timestamp,
        closed_at: Timestamp,
        open_view: PointInTimeView,
        close_view: PointInTimeView,
        candidates_considered: int,
    ) -> RebalanceOutcome:
        """Open every position in ``allocation``, hold it, and close it."""
        positions: list[PositionOutcome] = []
        deployed = Decimal(0)
        for key, weight in allocation.weights:
            instrument = self.instruments[key]
            entry_price = open_view.last_close(instrument)
            if entry_price is None or entry_price.amount <= 0:
                continue
            position = self._position(
                instrument=instrument,
                target=money(equity.amount * weight),
                entry_price=entry_price,
                opened_at=opened_at,
                closed_at=closed_at,
                close_view=close_view,
            )
            positions.append(position)
            deployed += position.capital_deployed.amount

        realised = sum((item.realised.amount for item in positions), Decimal(0))
        uninvested = money(equity.amount - deployed)
        return RebalanceOutcome(
            opened_at=opened_at,
            closed_at=closed_at,
            equity_before=equity,
            equity_after=money(realised + uninvested.amount),
            positions=tuple(positions),
            uninvested=uninvested,
            candidates_considered=candidates_considered,
            note=allocation.note,
        )

    def _position(
        self,
        *,
        instrument: Instrument,
        target: Notional,
        entry_price: Price,
        opened_at: Timestamp,
        closed_at: Timestamp,
        close_view: PointInTimeView,
    ) -> PositionOutcome:
        """One position, from entry to exit, with every charge on its own line.

        ``target`` is what the allocation asked for. What is actually deployed is
        the sum of what went into the asset and what the entry charges took, and
        that sum is computed rather than assumed: dividing by ``1 + c`` rounds at
        the last digit of the working precision, and if ``capital_deployed`` were
        the un-rounded target instead, the gross-minus-costs identity would fail
        by that rounding. The residual - a few units in the twenty-sixth decimal
        place - lands in uninvested cash, where it belongs and where it is
        visible.
        """
        foreign = self.routing.is_foreign(instrument.symbol)
        entry_rate = self.fx.rate_at(opened_at) if foreign else Decimal(1)
        exit_rate = self.fx.rate_at(closed_at) if foreign else Decimal(1)

        entry_rate_bps = self._entry_cost_rate(instrument.key, opened_at, foreign=foreign)
        committed = money(target.amount / (Decimal(1) + entry_rate_bps))
        entry_costs = self._trade_costs(instrument.key, committed, opened_at, foreign=foreign)
        capital_deployed = money(committed.amount + entry_costs.total.amount)

        quantity = Quantity(committed.amount / entry_rate / entry_price.amount)
        exit_price, series_end = self._exit(
            instrument, entry_price, opened_at, closed_at, close_view
        )
        gross_proceeds = money(quantity.amount * exit_price.amount * exit_rate)

        haircut_cost = (
            money(gross_proceeds.amount * self.haircut.fraction)
            if series_end.takes_haircut
            else Notional(Decimal(0))
        )
        days_held = (closed_at.value - opened_at.value).days
        exit_costs = self._trade_costs(
            instrument.key, gross_proceeds, closed_at, foreign=foreign
        ) + CostLines(
            funding=money(self.cost_model.funding_over(committed, days_held).amount),
            delisting=haircut_cost,
        )
        return PositionOutcome(
            key=instrument.key,
            opened_at=opened_at,
            closed_at=closed_at,
            capital_deployed=capital_deployed,
            committed=committed,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_rate=entry_rate,
            exit_rate=exit_rate,
            gross_proceeds=gross_proceeds,
            entry_costs=entry_costs,
            exit_costs=exit_costs,
            series_end=series_end,
        )

    def _exit(
        self,
        instrument: Instrument,
        entry_price: Price,
        opened_at: Timestamp,
        closed_at: Timestamp,
        close_view: PointInTimeView,
    ) -> tuple[Price, SeriesEnd]:
        """The exit price, and why the series stopped if it did.

        A series still running produces its own last close and no haircut. A
        series that produced nothing at all inside the holding period is marked
        out at whatever its last observed close was, and the haircut applies
        only where the oracle says the instrument was delisted rather than that
        the archive simply stopped.
        """
        last = close_view.last_bar(instrument)
        if last is None:
            return entry_price, self.series_end.series_end_at(instrument.key, closed_at)
        if last.close_time <= opened_at:
            return last.close, self.series_end.series_end_at(instrument.key, closed_at)
        return last.close, SeriesEnd.STILL_LISTED

    # -- costs ---------------------------------------------------------------

    def _entry_cost_rate(self, key: InstrumentKey, at: Timestamp, *, foreign: bool) -> Decimal:
        """Total entry charge as a rate on the committed notional.

        Computed as a rate rather than an amount so that the financing identity
        in :mod:`sextant.engine.backtest.ledger` can be solved exactly. Every
        line here is proportional to notional, which is what makes that possible
        and is stated so that a future non-proportional cost breaks loudly.
        """
        probe = Notional(Decimal(1))
        costs = self._trade_costs(key, probe, at, foreign=foreign)
        return costs.total.amount

    def _trade_costs(
        self, key: InstrumentKey, notional: Notional, at: Timestamp, *, foreign: bool
    ) -> CostLines:
        """Fees, spread, slippage, funding and any currency conversion."""
        trade = self.cost_model.cost_of(key, notional, at)
        conversion = self.fx.conversion_cost(notional) if foreign else Notional(Decimal(0))
        return CostLines(
            fees=money(trade.fee.amount),
            spread=money(trade.spread.amount),
            slippage=money(trade.slippage.amount),
            fx_conversion=money(conversion.amount),
        )

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
        allocation: Allocation,
        outcome: RebalanceOutcome,
        at: Timestamp,
    ) -> tuple[DecisionRecord, ...]:
        """One audit record per position taken."""
        weights = dict(allocation.weights)
        return tuple(
            DecisionRecord(
                timestamp=at,
                run_id=run_id,
                symbol=position.key.symbol,
                venue=position.key.venue,
                regime=None,
                strategy=allocator.name,
                signal=(
                    f"weight={weights.get(position.key, Decimal(0))} "
                    f"cross_sectional={allocator.is_cross_sectional}"
                ),
                llm_decision=None,
                confidence=None,
                entry=position.entry_price,
                stop=None,
                target=None,
                size=position.quantity,
                expected_edge_bps=None,
                expected_costs_bps=_bps_of(position.costs.total, position.capital_deployed),
                net_expected_edge_bps=None,
                risk_verdict=RiskVerdict.APPROVE,
                risk_reason=(
                    "no risk engine is wired in SEXTANT-004; every baseline allocation "
                    "is executed as produced"
                ),
                outcome=str(position.net_pnl.amount),
            )
            for position in outcome.positions
        )


def _bps_of(amount: Notional, base: Notional) -> Decimal | None:
    """``amount`` as basis points of ``base``, or None when the base is zero."""
    if base.amount == 0:
        return None
    return amount.amount / base.amount * Decimal(10_000)


def usable_window(first_month: Timestamp, months: int) -> Window:
    """A month-aligned window of ``months`` starting at ``first_month``."""
    return Window(start=first_month, end=add_months(first_month, months))


def rebalance_count(window: Window) -> int:
    """How many holding periods a monthly rebalance produces over ``window``."""
    return len(monthly_instants(window))


def as_sequence(keys: Sequence[InstrumentKey]) -> tuple[InstrumentKey, ...]:
    """Freeze a key sequence in the order given, for deterministic iteration."""
    return tuple(keys)

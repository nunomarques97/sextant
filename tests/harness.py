"""In-memory doubles for driving the engine without a store or a network.

Every double here implements a port or an engine protocol structurally. None of
them subclasses anything from ``src``, which is the point: if the engine can be
driven by objects that share nothing with the real adapters but their shape,
then the ports are doing their job.

The synthetic dataset is deliberately small and boring - a handful of
instruments on smooth, deterministic paths - because these tests are about the
engine's arithmetic and its refusals, not about market realism. Where a test
needs a specific event (a series that stops, a universe that changes) it builds
that event explicitly rather than hoping the fixture contains one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sextant.adapters.clocks import SimulatedClock
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.market_data import Bar
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue
from sextant.engine.execution.costs import (
    CostAssumption,
    FeeSchedule,
    ItemisedCostModel,
    LiquidityBand,
    LiquidityClassifier,
)
from sextant.engine.execution.fx import FxLeg, FxPolicy
from sextant.engine.execution.markout import DelistingHaircut, SeriesEnd

VENUE = Venue("testvenue")
OTHER_VENUE = Venue("othervenue")


def ts(text: str) -> Timestamp:
    """An ISO-8601 UTC instant."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def instrument(symbol: str, *, venue: Venue = VENUE, quote: str = "EUR") -> Instrument:
    """A candidate with permissive constraints and a sourced listing window."""
    return Instrument(
        venue=venue,
        symbol=symbol,
        base=symbol[:-3],
        quote=quote,
        listed_at=ts("2020-01-01T00:00:00"),
        tick_size=Price(Decimal("0.0001")),
        lot_size=Quantity(Decimal("0.00000001")),
        min_notional=Notional(Decimal(1)),
    )


def daily_bars(
    item: Instrument,
    *,
    first_day: str,
    closes: Sequence[str],
    is_closed: bool = True,
) -> tuple[Bar, ...]:
    """Consecutive daily bars from ``first_day``, one per close given."""
    start = ts(first_day)
    return tuple(
        Bar(
            instrument=item,
            timeframe=Timeframe.D1,
            open_time=start.plus(timedelta(days=offset)),
            open=Price(Decimal(close)),
            high=Price(Decimal(close)),
            low=Price(Decimal(close)),
            close=Price(Decimal(close)),
            volume=Quantity(Decimal(1000)),
            is_closed=is_closed,
            quote_volume=Notional(Decimal(close) * Decimal(1000)),
        )
        for offset, close in enumerate(closes)
    )


def ramp(days: int, *, start: str, step: str) -> tuple[str, ...]:
    """A deterministic price path: an arithmetic ramp, as exact decimal text."""
    first = Decimal(start)
    increment = Decimal(step)
    return tuple(str(first + increment * Decimal(day)) for day in range(days))


@dataclass(slots=True)
class InMemoryBarRepository:
    """A ``BarRepository`` over a dictionary, which records what it was asked for.

    The recording is what makes the structural look-ahead test possible: after a
    run, ``reads`` holds every bar handed out, and a bar closing after the
    instant it was requested for is a leak whatever the engine claims.
    """

    series: dict[InstrumentKey, tuple[Bar, ...]] = field(default_factory=dict)
    reads: list[tuple[Timestamp, Bar]] = field(default_factory=list)
    leak_future: bool = False
    """When True the repository ignores the caller's upper bound and hands back
    everything it holds. Nothing in production does this; it exists so a test can
    prove the view refuses a leak rather than absorbing it."""

    def add(self, bars: Iterable[Bar]) -> None:
        """Store bars, grouped by instrument, in time order."""
        grouped: dict[InstrumentKey, list[Bar]] = {}
        for bar in bars:
            grouped.setdefault(bar.instrument.key, []).append(bar)
        for key, collected in grouped.items():
            merged = [*self.series.get(key, ()), *collected]
            self.series[key] = tuple(sorted(merged, key=lambda item: item.open_time))

    def replace_after(self, instant: Timestamp, close: str) -> None:
        """Overwrite every bar closing after ``instant`` with a garbage price.

        The poisoned-future mechanism. Nothing decided at or before ``instant``
        may change as a result of this.
        """
        poisoned = Price(Decimal(close))
        for key, bars in self.series.items():
            self.series[key] = tuple(
                bar
                if bar.close_time <= instant
                else Bar(
                    instrument=bar.instrument,
                    timeframe=bar.timeframe,
                    open_time=bar.open_time,
                    open=poisoned,
                    high=poisoned,
                    low=poisoned,
                    close=poisoned,
                    volume=bar.volume,
                    is_closed=bar.is_closed,
                    quote_volume=bar.quote_volume,
                )
                for bar in bars
            )

    def read(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        closed_only: bool = True,
    ) -> Sequence[Bar]:
        """Bars closing in ``[start, end)``, recording every one handed out."""
        collected: list[Bar] = []
        for item in instruments:
            for bar in self.series.get(item.key, ()):
                if bar.timeframe is not timeframe:
                    continue
                if closed_only and not bar.is_closed:
                    continue
                if bar.close_time < start:
                    continue
                if bar.close_time >= end and not self.leak_future:
                    continue
                collected.append(bar)
                self.reads.append((end, bar))
        return collected

    def write(self, bars: Sequence[Bar]) -> int:
        """Store bars. Present so this double implements the whole port."""
        self.add(bars)
        return len(bars)

    def latest_close_time(self, item: Instrument, timeframe: Timeframe) -> Timestamp | None:
        """The close time of the most recent stored closed bar."""
        closed = [
            bar
            for bar in self.series.get(item.key, ())
            if bar.is_closed and bar.timeframe is timeframe
        ]
        return closed[-1].close_time if closed else None


@dataclass(slots=True)
class TracingClock:
    """Wraps an advanceable clock and records every instant it reported.

    The trace it produces is what the clock-equivalence test replays through a
    clock the engine cannot drive.
    """

    inner: SimulatedClock
    trace: list[Timestamp] = field(default_factory=list)

    def now(self) -> Timestamp:
        """The wrapped clock's instant, recorded on the way past."""
        instant = self.inner.now()
        self.trace.append(instant)
        return instant

    def advance_to(self, instant: Timestamp) -> None:
        """Move the wrapped clock."""
        self.inner.advance_to(instant)


@dataclass(slots=True)
class RecordedClock:
    """Replays a fixed sequence of instants, as a wall clock would deliver them.

    Used for the clock-equivalence test. It has no ``advance_to``, so the engine
    treats it exactly as it would treat a system clock: it cannot drive it, only
    read it. The sequence is stepped by ``now()`` calls in the order the engine
    makes them.
    """

    instants: Sequence[Timestamp]
    position: int = 0

    def now(self) -> Timestamp:
        """The next recorded instant, holding at the last one."""
        instant = self.instants[min(self.position, len(self.instants) - 1)]
        self.position += 1
        return instant


@dataclass(frozen=True, slots=True)
class StaticUniverse:
    """A universe that answers from a fixed mapping, in a fixed order."""

    members: Mapping[Timestamp, tuple[InstrumentKey, ...]]
    default: tuple[InstrumentKey, ...] = ()
    label: str = "static"

    @property
    def policy_name(self) -> str:
        """Identifier recorded in the manifest."""
        return self.label

    def executable_at(self, at: Timestamp) -> tuple[InstrumentKey, ...]:
        """Members at ``at``."""
        return self.members.get(at, self.default)


@dataclass(frozen=True, slots=True)
class StaticSeriesEnd:
    """Says why a series stopped, from a fixed mapping."""

    reasons: Mapping[InstrumentKey, SeriesEnd] = field(default_factory=dict)
    default: SeriesEnd = SeriesEnd.UNDETERMINED

    def series_end_at(self, key: InstrumentKey, at: Timestamp) -> SeriesEnd:
        """Why ``key``'s series stopped, ignoring ``at``."""
        del at
        return self.reasons.get(key, self.default)


@dataclass(frozen=True, slots=True)
class FixedBand:
    """Puts every instrument in one band. Keeps cost tests arithmetic-only."""

    band: LiquidityBand = LiquidityBand.DEEP

    def band_at(self, key: InstrumentKey, at: Timestamp) -> LiquidityBand:
        """The configured band, whatever is asked."""
        del key, at
        return self.band


def assumption(label: str, bps: Decimal) -> CostAssumption:
    """A flat cost assumption across every band, for arithmetic tests."""
    return CostAssumption(
        by_band=dict.fromkeys(LiquidityBand, bps),
        basis=f"test fixture: flat {bps} bps, not a measurement",
        label=label,
    )


def cost_model(
    *,
    maker_bps: str = "40",
    taker_bps: str = "80",
    spread_bps: str = "10",
    slippage_bps: str = "5",
    fill_mix_maker: str = "1",
    liquidity: LiquidityClassifier | None = None,
) -> ItemisedCostModel:
    """A cost model with every rate stated explicitly by the caller."""
    from sextant.engine.execution.costs import FillMix

    return ItemisedCostModel(
        schedule=FeeSchedule(
            maker_bps=Decimal(maker_bps),
            taker_bps=Decimal(taker_bps),
            tier="test fixture",
            source="test fixture",
        ),
        fill_mix=FillMix(maker_fraction=Decimal(fill_mix_maker), label="test fixture"),
        spread=assumption("spread", Decimal(spread_bps)),
        slippage=assumption("slippage", Decimal(slippage_bps)),
        liquidity=liquidity or FixedBand(),
    )


def no_fx() -> FxLeg:
    """A currency leg for an account that never leaves its own currency."""
    return FxLeg(
        policy=FxPolicy.ACCOUNT_CURRENCY_ONLY,
        conversion_bps=Decimal(0),
        basis="test fixture: no foreign instruments",
    )


def haircut(fraction: str = "0.20") -> DelistingHaircut:
    """The mark-out assumption, at a stated fraction."""
    return DelistingHaircut(fraction=Decimal(fraction))

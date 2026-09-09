"""The only way the engine and a strategy see prices.

Everything reaches this object through the ``BarRepository`` port, and this
object refuses to return anything that had not closed by the instant it was
constructed for. Those two properties together are what make the structural
look-ahead test possible: drive the engine through a repository that records its
reads, and assert that no bar in the recorded set has a ``close_time`` after the
``as_of`` of the view that asked for it.

**Closed bars only, and not by convention.** ``closed_only`` defaults to True on
the port, and this view never passes False. On top of that it filters on
``close_time <= as_of`` itself, because a store built by an ingest that ran at a
different instant carries closed flags decided against a different clock. The
flag says whether the bar had finished forming; the comparison says whether it
had finished forming *by the moment the decision was made*. Both are needed.

**No ambient symbol.** Every method takes its instruments explicitly, per
invariant 3.

Pure computation over a port. No venue, and no wall clock: the ``as_of`` comes
from the ``Clock`` port through the engine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sextant.domain.errors import DomainError
from sextant.domain.instrument import Instrument, InstrumentKey
from sextant.domain.market_data import Bar, OpenBarConsumed
from sextant.domain.money import Notional, Price
from sextant.domain.time import Timeframe, Timestamp
from sextant.ports.repository import BarRepository


class FutureReadRefused(DomainError):
    """A view was asked for data at or after the instant it was built for."""


@dataclass(frozen=True, slots=True)
class PointInTimeView:
    """Bars for a set of instruments, as knowable at one instant.

    Constructed fresh at every rebalance. It holds no state that survives the
    decision it was built for, which is what stops a lookback accidentally
    becoming a look-ahead when the loop advances.
    """

    repository: BarRepository
    timeframe: Timeframe
    as_of: Timestamp
    horizon: Timestamp
    """The earliest instant this view will read from. Not a performance knob: a
    view that could read from the beginning of time would make a strategy's
    lookback invisible in the recorded read set."""

    _cache: dict[InstrumentKey, tuple[Bar, ...]] = field(default_factory=dict, repr=False)

    @classmethod
    def at(
        cls,
        repository: BarRepository,
        timeframe: Timeframe,
        as_of: Timestamp,
        *,
        lookback_days: int,
    ) -> PointInTimeView:
        """A view at ``as_of`` reaching ``lookback_days`` into the past."""
        if lookback_days <= 0:
            raise ValueError(f"lookback_days must be positive, got {lookback_days}")
        return cls(
            repository=repository,
            timeframe=timeframe,
            as_of=as_of,
            horizon=as_of.plus(-timedelta(days=lookback_days)),
        )

    def bars(self, instruments: Sequence[Instrument]) -> Mapping[InstrumentKey, tuple[Bar, ...]]:
        """Closed bars for each instrument, none of them closing after ``as_of``.

        Instruments already read by this view are served from its own cache. The
        cache is per-view and per-instant, so it can only ever return what a
        read at this instant would have returned.
        """
        wanted = [item for item in instruments if item.key not in self._cache]
        if wanted:
            fetched: dict[InstrumentKey, list[Bar]] = {item.key: [] for item in wanted}
            for bar in self.repository.read(
                wanted,
                self.timeframe,
                self.horizon,
                self.as_of,
                closed_only=True,
            ):
                if not bar.is_closed:
                    raise OpenBarConsumed(bar)
                if bar.close_time > self.as_of:
                    raise FutureReadRefused(
                        f"{bar.instrument} bar closing {bar.close_time.isoformat()} was "
                        f"returned for a view as of {self.as_of.isoformat()}. The store "
                        "returned a bar from the future; the read path is broken."
                    )
                fetched[bar.instrument.key].append(bar)
            for key, collected in fetched.items():
                self._cache[key] = tuple(sorted(collected, key=lambda item: item.open_time))
        return {item.key: self._cache[item.key] for item in instruments}

    def last_close(self, instrument: Instrument) -> Price | None:
        """The most recent close knowable at ``as_of``, or None when there is none.

        ``None`` rather than a raise: an instrument with no bar inside the
        lookback is a real and common condition - it listed last week, or it has
        stopped trading - and the caller decides what that means.
        """
        series = self.bars([instrument])[instrument.key]
        return series[-1].close if series else None

    def last_bar(self, instrument: Instrument) -> Bar | None:
        """The most recent closed bar knowable at ``as_of``."""
        series = self.bars([instrument])[instrument.key]
        return series[-1] if series else None

    def turnover(self, instrument: Instrument) -> Notional | None:
        """Quote turnover of the most recent knowable bar, when the source has it."""
        bar = self.last_bar(instrument)
        return None if bar is None else bar.quote_volume

    def reads(self) -> Mapping[InstrumentKey, int]:
        """How many bars this view has handed out per instrument.

        Exists for the look-ahead tests: a feature that claims a thirty-day
        lookback and reads ninety bars is doing something other than what it
        says.
        """
        return {key: len(series) for key, series in self._cache.items()}

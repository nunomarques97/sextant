"""Rule T4: does any variant ever actually select a thin-band instrument?

Amendment 13, section 35.6. Rule M1 could not measure the thin band on the registered
days, and the registered days are what make the deep and mid measurements comparable to
each other. Moving them would buy a figure and spend the only thing that made the figures
mean anything, so **the thin band stays unmeasured** and the question becomes whether
that matters.

It is a computation over the universe, not a measurement. Every registered variant is
asked, at every rebalance instant of the family's own window, which pairs it opens; each
opened perpetual is banded by the cost model's own floors at that instant; and the
question is whether a thin-band instrument ever appears.

Three outcomes, all registered before this ran:

* **none does** - the band is empty in practice, its default is never charged, and the
  question closes with no acquisition and no substitution;
* **some does** - that band keeps the assumption at full strength with **no bound
  reported for it**, and any result depending on a thin-band instrument is
  spread-contingent under rule A12.2, which fires rule H1 for the acquisition.

**The selection is recomputed, not read.** F1's result file records no per-instant
holdings, so the variants' allocations are recomputed from the same world, the same
universe and the same point-in-time views the grid ran on. Nothing here scores a return,
charges a trial or touches the engine's accounting: it asks nine already-registered
allocators what they chose.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sextant.app.spike_006_f1 import (
    ENGINE_LOOKBACK_DAYS,
    FUNDING_TRAILING_DAYS,
    HISTORICAL_QUOTES_RULE,
    REGISTERED_VARIANTS,
    RESEARCH_ROOT,
    SPREAD_CONTINGENT,
    THIN_BAND,
    THIN_BAND_RULE,
)
from sextant.app.spike_006_f1_bands import BANDS, band_of
from sextant.app.spike_006_f1_run import build_variant
from sextant.app.spike_006_f1_world import World
from sextant.domain.instrument import InstrumentKey
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.backtest.market import PointInTimeView

#: Where the answer lands. Committed, like every other result file.
THIN_RESULTS = RESEARCH_ROOT / "spike-006-f1-thin.json"

NEWLINE = chr(10)


class ThinBandStudyIncomplete(RuntimeError):
    """The study cannot be assembled as registered, and nothing is substituted."""


@dataclass(frozen=True, slots=True)
class Selection:
    """One variant's answer at one instant, banded."""

    variant: str
    at: Timestamp
    by_band: Mapping[str, tuple[str, ...]]
    unbandable: tuple[str, ...]

    @property
    def thin(self) -> tuple[str, ...]:
        return self.by_band.get(THIN_BAND, ())


@dataclass(frozen=True, slots=True)
class ThinBandStudy:
    """Rule T4's answer for one family, per variant and pooled."""

    family: str
    instants: int
    selections: tuple[Selection, ...]

    @property
    def variants(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.variant for item in self.selections))

    def thin_selections_of(self, variant: str) -> tuple[Selection, ...]:
        """Every instant at which this variant opened a thin-band instrument."""
        return tuple(item for item in self.selections if item.variant == variant and item.thin)

    @property
    def any_variant_selects_thin(self) -> bool:
        """Rule T4's question, in one boolean."""
        return any(item.thin for item in self.selections)

    @property
    def instants_with_a_thin_selection(self) -> tuple[str, ...]:
        """The rebalance dates at which some variant opened a thin-band instrument.

        Reported rather than counted because *when* the band is occupied is a different
        fact from how often: a band that appears only at the end of the window is a
        statement about the venue's listings, not about the strategy.
        """
        return tuple(sorted({item.at.isoformat()[:10] for item in self.selections if item.thin}))

    @property
    def thin_symbols(self) -> tuple[str, ...]:
        """Every distinct thin-band instrument any variant ever opened."""
        return tuple(sorted({symbol for item in self.selections for symbol in item.thin}))

    def counts_by_band(self) -> Mapping[str, int]:
        """Instrument-instants selected into each band, pooled across every variant.

        Instrument-instants and not instruments: a band touched once by one name and a
        band held every month by ten are different facts about whether its cost matters.
        """
        out = dict.fromkeys(BANDS, 0)
        for item in self.selections:
            for band in BANDS:
                out[band] += len(item.by_band.get(band, ()))
        return out

    @property
    def total_selected(self) -> int:
        return sum(self.counts_by_band().values())

    def share(self, band: str) -> Decimal | None:
        total = self.total_selected
        if total == 0:
            return None
        return Decimal(self.counts_by_band()[band]) / Decimal(total)

    def as_json(self) -> dict[str, object]:
        thin_by_variant = {
            variant: len(self.thin_selections_of(variant)) for variant in self.variants
        }
        return {
            "rule": THIN_BAND_RULE,
            "family": self.family,
            "is_a_measurement": False,
            "what_this_is": (
                "A computation over the family's own registered universe: every "
                "registered variant asked, at every rebalance instant, which pairs it "
                "opens, with each opened perpetual banded by the cost model's own floors "
                "at that instant. No return is scored, no trial is charged and the engine "
                "is not run."
            ),
            "rebalance_instants": self.instants,
            "variants": list(self.variants),
            "instrument_instants_selected": self.total_selected,
            "instrument_instants_by_band": dict(self.counts_by_band()),
            "share_of_selected_instrument_instants_by_band": {
                band: None if (value := self.share(band)) is None else str(value) for band in BANDS
            },
            "any_variant_selects_a_thin_band_instrument": self.any_variant_selects_thin,
            "thin_band_selections_by_variant": thin_by_variant,
            "thin_band_symbols_ever_selected": list(self.thin_symbols),
            "instants_with_a_thin_band_selection": list(self.instants_with_a_thin_selection),
            "instruments_whose_band_was_unknowable": sorted(
                {symbol for item in self.selections for symbol in item.unbandable}
            ),
            "outcome": (
                (
                    "The thin band is OCCUPIED IN PRACTICE for this family. It keeps the "
                    "assumption at FULL STRENGTH and NO bound is reported for it, because "
                    "there is no measurement for a bound to be made of. A result "
                    "depending on a thin-band instrument is then referred to rule A12.2, "
                    "which is a GATE and not a verdict: A12.2 records a family as "
                    f"{SPREAD_CONTINGENT} only when it FAILS at the headline and CLEARS "
                    "at the bound. A family that fails at both is closed normally and "
                    f"rule {HISTORICAL_QUOTES_RULE} does not fire for it."
                )
                if self.any_variant_selects_thin
                else (
                    "The thin band is EMPTY IN PRACTICE for this family: no variant ever "
                    "opens an instrument in it, so its default is never charged and the "
                    "question closes. No acquisition, no substitution, no cost."
                )
            ),
            "why_the_registered_days_were_not_moved": (
                "The registered days are what make the deep and mid measurements "
                "comparable. Measuring the thin band two years later on one symbol of "
                "four would produce a number that cannot be compared with the two it "
                "exists to be compared against."
            ),
            "per_variant_per_instant": [
                {
                    "variant": item.variant,
                    "at": item.at.isoformat()[:10],
                    "selected_by_band": {band: list(item.by_band.get(band, ())) for band in BANDS},
                    "unbandable": list(item.unbandable),
                }
                for item in self.selections
            ],
        }


def band_at(world: World, key: InstrumentKey, at: Timestamp) -> str | None:
    """One instrument's band at one instant, by the cost model's own floors.

    ``None`` when the trailing turnover is unknowable, which is a third answer and not
    the cheapest band: an instrument whose turnover cannot be established has no band,
    and is reported as having none.
    """
    history = world.perpetual_histories.get(key)
    if history is None:
        return None
    median = history.median_quote_volume(at, FUNDING_TRAILING_DAYS)
    return None if median is None else band_of(median)


def selections(world: World, instants: Sequence[Timestamp]) -> tuple[Selection, ...]:
    """Every registered variant's opened pairs at every instant, banded.

    One view per instant, shared across the nine variants, for the same reason the engine
    builds one: a view is what makes a lookback visible, and nine copies of it at one
    instant would read the same bars nine times to no purpose.
    """
    out: list[Selection] = []
    allocators = [(spec.label, build_variant(spec, world)) for spec in REGISTERED_VARIANTS]
    for at in instants:
        keys = world.universe.executable_at(at)
        if not keys:
            continue
        candidates = [world.instruments[key] for key in keys if key in world.instruments]
        view = PointInTimeView.at(
            world.repository,
            Timeframe.D1,
            at,
            lookback_days=ENGINE_LOOKBACK_DAYS,
        )
        for label, allocator in allocators:
            allocation = allocator.allocate(candidates, at, view)
            by_band: dict[str, list[str]] = {}
            unbandable: list[str] = []
            for key in allocation.keys:
                if key not in world.perpetual_histories:
                    continue
                band = band_at(world, key, at)
                if band is None:
                    unbandable.append(key.symbol)
                    continue
                by_band.setdefault(band, []).append(key.symbol)
            out.append(
                Selection(
                    variant=label,
                    at=at,
                    by_band={band: tuple(sorted(names)) for band, names in by_band.items()},
                    unbandable=tuple(sorted(unbandable)),
                )
            )
    return tuple(out)


def study(world: World, instants: Sequence[Timestamp], *, family: str) -> ThinBandStudy:
    """Rule T4, applied to one family's own universe."""
    if not instants:
        raise ThinBandStudyIncomplete(
            "no rebalance instants were supplied, so no variant can be asked what it "
            "selected and the question is not answered by silence."
        )
    return ThinBandStudy(
        family=family,
        instants=len(instants),
        selections=selections(world, instants),
    )


def write(result: ThinBandStudy, destination: Path) -> Path:
    """Write the study. The path is a parameter with no default pointing anywhere real."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(result.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


__all__ = [
    "THIN_RESULTS",
    "Selection",
    "ThinBandStudy",
    "ThinBandStudyIncomplete",
    "band_at",
    "selections",
    "study",
    "write",
]

"""Rules B1 and M1: what the liquidity bands should be cut on, and whether they are used.

Amendment 12, sections 34.3 and 34.4. Two questions, and the cheap one is answered first
because its answer decides whether the expensive one costs anything.

**Rule M1's precondition, computed before anything is downloaded.** The cost model has
three bands and the measured sample reached only one of them. Before measuring the other
two, count how often the traded universe actually occupies them: band membership is
decided by the cost model's own floors at every rebalance instant of the F1 window, from
data already held. A band no member ever occupies needs no measurement, and recording it
as empty in practice is a better answer than a measured number for instruments the
strategy would never touch.

**Rule B1's re-cut.** The bands are cut on quote turnover. The measurement found two
orders of magnitude of spread inside one band, which means turnover is not what determines
spread here. So four candidate quantities are ranked against the measured half-spread by
Spearman's rho with a permutation p-value, and the cut goes to the largest correlation
among those that clear the registered level. If none clears it, nothing is re-cut and that
is the finding.

**The band count is decided by separation, not by there being three now.** Three bands,
then two, then one; the largest that gives every band at least the registered minimum of
sampled symbols and adjacent bands a factor of two between their measured medians.

Nothing here changes an F1 figure. F1 ran and was judged on the turnover-cut bands.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from math import log, sqrt
from pathlib import Path

from sextant.adapters.exchanges.binance.costs import DEEP_BAND_FLOOR, MID_BAND_FLOOR
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey
from sextant.app.futures_archive import PERP_VENUE
from sextant.app.futures_archive import STORE_ROOT as PERP_ROOT
from sextant.app.spike_006_f1 import (
    BAND_MINIMUM_SYMBOLS,
    BAND_RECUT_ALPHA,
    BAND_RECUT_PERMUTATIONS,
    BAND_RECUT_RULE,
    BAND_RECUT_SEED,
    BAND_SEPARATION_FACTOR,
    EXTENDED_SAMPLE_PER_BAND,
    EXTENDED_SAMPLE_RULE,
    FUNDING_TRAILING_DAYS,
    RESEARCH_ROOT,
)
from sextant.app.spike_006_f1_estimator import WINDOW_FIRST_MONTH, WINDOW_LAST_MONTH
from sextant.app.spike_006_f1_spread import SPREAD_RESULTS
from sextant.app.spike_006_f1_world import World, carry_bases_at
from sextant.domain.money import Notional
from sextant.domain.time import Timeframe, Timestamp
from sextant.engine.statistics.rank import spearman_rank_correlation

#: Where the answer lands. Committed, like every other result file.
BANDS_RESULTS = RESEARCH_ROOT / "spike-006-f1-bands.json"

#: The three bands as the cost model cuts them now, widest first.
BANDS = ("deep", "mid", "thin")

NEWLINE = chr(10)


class BandStudyIncomplete(RuntimeError):
    """The study cannot be assembled as registered, and nothing is substituted."""


def band_of(turnover: Notional) -> str:
    """The cost model's own band for one turnover level."""
    if turnover.amount >= DEEP_BAND_FLOOR.amount:
        return "deep"
    if turnover.amount >= MID_BAND_FLOOR.amount:
        return "mid"
    return "thin"


@dataclass(frozen=True, slots=True)
class Occupancy:
    """How often the traded universe sits in each band, over the whole window.

    ``instrument_instants`` counts (symbol, rebalance) pairs rather than symbols, because
    a band occupied once by one name and a band occupied every month by forty are
    different facts about whether its cost matters.
    """

    instants: int
    instrument_instants: Mapping[str, int]
    distinct_symbols: Mapping[str, tuple[str, ...]]
    instants_with_any: Mapping[str, int]
    unrankable: int

    @property
    def total_instrument_instants(self) -> int:
        return sum(self.instrument_instants.values())

    def is_empty(self, band: str) -> bool:
        """Whether no member of the traded universe ever occupied this band."""
        return self.instrument_instants.get(band, 0) == 0

    def share(self, band: str) -> Decimal | None:
        """What share of instrument-instants fell in this band."""
        total = self.total_instrument_instants
        if total == 0:
            return None
        return Decimal(self.instrument_instants.get(band, 0)) / Decimal(total)

    def as_json(self) -> dict[str, object]:
        return {
            "rule": EXTENDED_SAMPLE_RULE,
            "rebalance_instants": self.instants,
            "instrument_instants_by_band": dict(self.instrument_instants),
            "share_of_instrument_instants_by_band": {
                band: None if (value := self.share(band)) is None else str(value) for band in BANDS
            },
            "distinct_symbols_by_band": {
                band: list(self.distinct_symbols.get(band, ())) for band in BANDS
            },
            "distinct_symbol_count_by_band": {
                band: len(self.distinct_symbols.get(band, ())) for band in BANDS
            },
            "instants_with_at_least_one_member_by_band": dict(self.instants_with_any),
            "empty_in_practice": [band for band in BANDS if self.is_empty(band)],
            "members_whose_turnover_was_unknowable": self.unrankable,
            "decided_before_anything_was_downloaded": (
                "Band membership is the cost model's own floors applied to the trailing "
                f"{FUNDING_TRAILING_DAYS}-day median quote turnover already held for every "
                "member, at every rebalance instant of the F1 window. No bytes were fetched "
                "to answer it."
            ),
            "what_an_empty_band_means": (
                "A band no member of the traded universe ever occupied has a default that is "
                "never charged. Measuring it would produce a number about instruments this "
                "strategy would not touch, which is why rule M1 registers the empty answer "
                "as an answer."
            ),
        }


def rebalance_instants() -> tuple[Timestamp, ...]:
    """Every month start of the F1 window, which is every instant a decision was made at.

    The same list the runner rebalanced on, derived from the registered window rather than
    read from a result file, so that occupancy is a statement about the specification and
    not about whatever a file happens to contain.
    """
    out: list[Timestamp] = []
    at = Timestamp.parse(f"{WINDOW_FIRST_MONTH}-01T00:00:00+00:00")
    stop = Timestamp.parse(f"{WINDOW_LAST_MONTH}-01T00:00:00+00:00")
    while at <= stop:
        out.append(at)
        year, month = at.value.year, at.value.month
        at = Timestamp.parse(
            f"{year + 1}-01-01T00:00:00+00:00"
            if month == 12
            else f"{year}-{month + 1:02d}-01T00:00:00+00:00"
        )
    return tuple(out)


def occupancy(world: World, instants: Sequence[Timestamp]) -> Occupancy:
    """How often each band is occupied, across every rebalance of the window."""
    counts = dict.fromkeys(BANDS, 0)
    symbols: dict[str, set[str]] = {band: set() for band in BANDS}
    with_any = dict.fromkeys(BANDS, 0)
    unrankable = 0
    for at in instants:
        bases = set(carry_bases_at(world, at))
        seen: set[str] = set()
        for key, history in world.perpetual_histories.items():
            instrument = world.instruments.get(key)
            if instrument is None or instrument.base not in bases:
                continue
            median = history.median_quote_volume(at, FUNDING_TRAILING_DAYS)
            if median is None:
                unrankable += 1
                continue
            band = band_of(median)
            counts[band] += 1
            symbols[band].add(key.symbol)
            seen.add(band)
        for band in seen:
            with_any[band] += 1
    return Occupancy(
        instants=len(instants),
        instrument_instants=counts,
        distinct_symbols={band: tuple(sorted(symbols[band])) for band in BANDS},
        instants_with_any=with_any,
        unrankable=unrankable,
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate cut quantity, and how well it orders the measured spread."""

    name: str
    values: Mapping[str, float]
    rho: float | None
    p_value: float | None

    @property
    def clears(self) -> bool:
        """Whether this candidate may be used at all, under rule B1's level."""
        return (
            self.rho is not None
            and self.p_value is not None
            and self.p_value < float(BAND_RECUT_ALPHA)
        )

    @property
    def strength(self) -> float:
        """The absolute rank correlation, which is what the candidates are ranked on."""
        return 0.0 if self.rho is None else abs(self.rho)

    def as_json(self) -> dict[str, object]:
        return {
            "quantity": self.name,
            "values_by_symbol": {symbol: self.values[symbol] for symbol in sorted(self.values)},
            "spearman_rho": self.rho,
            "permutation_p_value": self.p_value,
            "absolute_rho": self.strength,
            "clears_the_registered_level": self.clears,
        }


def permutation_p_value(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """A two-sided p-value for Spearman's rho, by permutation at the registered seed.

    Exact tables at n = 6 exist, and a permutation test is used instead because it needs no
    table, extends to whatever n rule M1 ends up with, and is reproducible from a
    registered seed. The statistic is the absolute rho, so the test is two-sided by
    construction rather than by doubling a one-sided number.
    """
    observed = spearman_rank_correlation(xs, ys)
    if observed is None:
        return None
    generator = random.Random(BAND_RECUT_SEED)  # noqa: S311
    shuffled = list(ys)
    at_least_as_extreme = 0
    for _ in range(BAND_RECUT_PERMUTATIONS):
        generator.shuffle(shuffled)
        drawn = spearman_rank_correlation(xs, shuffled)
        if drawn is not None and abs(drawn) >= abs(observed) - 1e-12:
            at_least_as_extreme += 1
    return float(at_least_as_extreme + 1) / float(BAND_RECUT_PERMUTATIONS + 1)


@dataclass(frozen=True, slots=True)
class Recut:
    """Rule B1's answer: what to cut on, and how many bands the evidence supports."""

    measured: Mapping[str, float]
    candidates: tuple[Candidate, ...]
    chosen: str | None
    bands_supported: int
    edges: tuple[float, ...]
    groups: tuple[tuple[str, ...], ...]
    group_medians: tuple[float, ...]
    limited_by_the_sample: bool

    def as_json(self) -> dict[str, object]:
        return {
            "rule": BAND_RECUT_RULE,
            "measured_half_spread_bps_by_symbol": {
                symbol: self.measured[symbol] for symbol in sorted(self.measured)
            },
            "candidates": [item.as_json() for item in self.candidates],
            "registered_level": str(BAND_RECUT_ALPHA),
            "permutations": BAND_RECUT_PERMUTATIONS,
            "seed": BAND_RECUT_SEED,
            "chosen_quantity": self.chosen,
            "bands_supported_by_the_evidence": self.bands_supported,
            "minimum_symbols_per_band": BAND_MINIMUM_SYMBOLS,
            "separation_factor": str(BAND_SEPARATION_FACTOR),
            "band_edges_on_the_chosen_quantity": list(self.edges),
            "band_members": [list(group) for group in self.groups],
            "band_median_half_spread_bps": list(self.group_medians),
            "band_count_is_limited_by_the_sample_size": self.limited_by_the_sample,
            "what_limited_it": (
                f"{len(self.measured)} symbols are measured and rule B1 requires at least "
                f"{BAND_MINIMUM_SYMBOLS} in every band, so at most "
                f"{len(self.measured) // BAND_MINIMUM_SYMBOLS} bands can be tested however "
                "well the quantity separates them. That is a limit of the sample and not a "
                "statement about the market."
            )
            if self.limited_by_the_sample
            else "the separation requirement, not the sample size",
        }


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _separates(groups: Sequence[Sequence[str]], measured: Mapping[str, float]) -> bool:
    """Whether every group is big enough and adjacent medians are far enough apart."""
    if any(len(group) < BAND_MINIMUM_SYMBOLS for group in groups):
        return False
    medians = [_median([measured[symbol] for symbol in group]) for group in groups]
    factor = float(BAND_SEPARATION_FACTOR)
    for first, second in pairwise(medians):
        if first <= 0.0 or second <= 0.0:
            return False
        ratio = max(first, second) / min(first, second)
        if ratio < factor:
            return False
    return True


def _split(ordered: Sequence[str], parts: int) -> tuple[tuple[str, ...], ...]:
    """Cut an ordered list of symbols into ``parts`` contiguous groups, as evenly as it goes."""
    size, extra = divmod(len(ordered), parts)
    groups: list[tuple[str, ...]] = []
    start = 0
    for index in range(parts):
        end = start + size + (1 if index < extra else 0)
        groups.append(tuple(ordered[start:end]))
        start = end
    return tuple(groups)


def recut(measured: Mapping[str, float], candidates: Sequence[Candidate]) -> Recut:
    """Rule B1, applied: the cut quantity, then the number of bands it supports."""
    clearing = [item for item in candidates if item.clears]
    chosen = max(clearing, key=lambda item: item.strength) if clearing else None
    if chosen is None:
        return Recut(
            measured=measured,
            candidates=tuple(candidates),
            chosen=None,
            bands_supported=1,
            edges=(),
            groups=(tuple(sorted(measured)),),
            group_medians=(_median([measured[symbol] for symbol in measured]),),
            limited_by_the_sample=False,
        )
    ordered = sorted(measured, key=lambda symbol: chosen.values[symbol])
    ceiling = max(1, len(measured) // BAND_MINIMUM_SYMBOLS)
    for parts in range(min(len(BANDS), ceiling), 1, -1):
        groups = _split(ordered, parts)
        if _separates(groups, measured):
            return Recut(
                measured=measured,
                candidates=tuple(candidates),
                chosen=chosen.name,
                bands_supported=parts,
                edges=tuple(chosen.values[group[0]] for group in groups[1:]),
                groups=groups,
                group_medians=tuple(
                    _median([measured[symbol] for symbol in group]) for group in groups
                ),
                limited_by_the_sample=ceiling < len(BANDS),
            )
    return Recut(
        measured=measured,
        candidates=tuple(candidates),
        chosen=chosen.name,
        bands_supported=1,
        edges=(),
        groups=(tuple(ordered),),
        group_medians=(_median([measured[symbol] for symbol in measured]),),
        limited_by_the_sample=ceiling < len(BANDS),
    )


def measured_half_spreads(payload: Mapping[str, object]) -> dict[str, float]:
    """The measured median quoted HALF-spread per symbol, from rule S1's sample.

    Halved, because the cost model charges a half-spread per leg and the comparison has to
    be between the same quantity on both sides.
    """
    rows = payload.get("per_symbol_day")
    if not isinstance(rows, list):
        raise BandStudyIncomplete(
            "the measured spread file carries no symbol-days, so rule B1 has nothing to "
            "rank a candidate quantity against and nothing is re-cut."
        )
    per_symbol: dict[str, list[float]] = {}
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        whole = entry.get("whole_day")
        if not isinstance(whole, dict) or whole.get("median_bps") is None:
            continue
        per_symbol.setdefault(str(entry["symbol"]), []).append(float(str(whole["median_bps"])))
    return {symbol: _median(values) / 2.0 for symbol, values in per_symbol.items()}


def _tick_size(prices: Sequence[Decimal]) -> Decimal:
    """The venue's price increment, as the greatest common divisor of what it published.

    Every price the venue publishes is a whole number of ticks, so the greatest common
    divisor of a few hundred of them is the tick, and it converges fast. Computed in exact
    integers by scaling to a fixed exponent first: a float gcd is not a gcd.
    """
    scale = Decimal(10) ** 12
    divisor = 0
    for price in prices:
        divisor = _gcd(divisor, int(price * scale))
    if divisor == 0:
        raise BandStudyIncomplete("every published price was zero, so no tick can be derived.")
    return Decimal(divisor) / scale


def _gcd(left: int, right: int) -> int:
    while right:
        left, right = right, left % right
    return abs(left)


def candidates_for(
    store_root: Path, symbols: Sequence[str], at: Timestamp
) -> tuple[Candidate, ...]:
    """Rule B1's four candidate quantities, for the sampled symbols, at one instant.

    Read from the stored bars rather than from the world's daily histories, because one of
    the four is the trade count and a history does not carry it. The turnover figure is the
    same quantity the cost model's bands are cut on - the median of close times volume over
    the trailing window - computed from the same bytes.
    """
    store = ParquetBarStore(store_root)
    turnover: dict[str, float] = {}
    relative_tick: dict[str, float] = {}
    volatility: dict[str, float] = {}
    trades: dict[str, float] = {}
    horizon = at.epoch_millis
    floor = horizon - FUNDING_TRAILING_DAYS * 24 * 60 * 60 * 1000
    for symbol in symbols:
        key = SeriesKey(venue=PERP_VENUE, symbol=symbol, timeframe=Timeframe.D1)
        if not store.has_series(key):
            continue
        window = [
            bar for bar in store.read_series(key) if floor <= bar.open_time.epoch_millis < horizon
        ]
        if len(window) < 2:
            continue
        closes = [Decimal(bar.close) for bar in window]
        prices = [Decimal(field) for bar in window for field in (bar.high, bar.low, bar.close)]
        turnover[symbol] = float(
            _median_decimal([Decimal(bar.close) * Decimal(bar.volume) for bar in window])
        )
        relative_tick[symbol] = float(_tick_size(prices) / _median_decimal(closes))
        volatility[symbol] = _realised_volatility(closes)
        trades[symbol] = sum(bar.trades for bar in window) / float(len(window))
    return (
        Candidate(
            name="trailing 30-day median quote turnover",
            values=turnover,
            rho=None,
            p_value=None,
        ),
        Candidate(name="relative tick size", values=relative_tick, rho=None, p_value=None),
        Candidate(name="realised daily volatility", values=volatility, rho=None, p_value=None),
        Candidate(name="mean daily trade count", values=trades, rho=None, p_value=None),
    )


def _median_decimal(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _realised_volatility(closes: Sequence[Decimal]) -> float:
    """The standard deviation of daily log returns over the window."""
    returns = [
        log(float(second / first)) for first, second in pairwise(closes) if first > 0 and second > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    return sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))


def scored(candidate: Candidate, measured: Mapping[str, float]) -> Candidate:
    """The same candidate with its rank correlation and permutation p-value computed.

    A candidate that is missing any sampled symbol answers ``None`` rather than correlating
    over the subset it happens to have: a rank correlation over five of six symbols is a
    different statistic from the one rule B1 registered.
    """
    shared = sorted(set(candidate.values) & set(measured))
    if len(shared) < len(measured):
        return Candidate(name=candidate.name, values=candidate.values, rho=None, p_value=None)
    xs = [candidate.values[symbol] for symbol in shared]
    ys = [measured[symbol] for symbol in shared]
    return Candidate(
        name=candidate.name,
        values=candidate.values,
        rho=spearman_rank_correlation(xs, ys),
        p_value=permutation_p_value(xs, ys),
    )


@dataclass(frozen=True, slots=True)
class BandStudy:
    """Both answers: whether the unmeasured bands are used, and what to cut on."""

    occupancy: Occupancy
    recut: Recut
    selected_at: str

    @property
    def bands_needing_measurement(self) -> tuple[str, ...]:
        """The bands rule M1 must measure: unmeasured by rule S1 and not empty in practice."""
        return tuple(band for band in ("mid", "thin") if not self.occupancy.is_empty(band))

    def as_json(self) -> dict[str, object]:
        return {
            "occupancy": self.occupancy.as_json(),
            "recut": self.recut.as_json(),
            "selected_at": self.selected_at,
            "rule_m1": {
                "bands_rule_s1_measured": ["deep"],
                "bands_needing_measurement": list(self.bands_needing_measurement),
                "symbols_required_per_band": EXTENDED_SAMPLE_PER_BAND,
                "decision": (
                    "every band the traded universe occupies has already been measured, so "
                    "rule M1 acquires nothing and the unmeasured defaults are never charged"
                    if not self.bands_needing_measurement
                    else "acquire the bands listed above, at the registered size"
                ),
            },
            "changes_no_f1_figure": (
                "F1 ran and was judged on the turnover-cut bands and is not re-scored on "
                "re-cut ones. Both rules apply from F2."
            ),
        }


def study(
    world: World,
    instants: Sequence[Timestamp],
    *,
    spread_path: Path = SPREAD_RESULTS,
    store_root: Path = PERP_ROOT,
) -> BandStudy:
    """Rules M1 and B1, computed. Occupancy first, because it decides the acquisition."""
    payload = json.loads(spread_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BandStudyIncomplete(f"{spread_path} does not hold a mapping.")
    measured = measured_half_spreads({str(k): v for k, v in payload.items()})
    if not measured:
        raise BandStudyIncomplete("no measured spread to rank a candidate quantity against.")
    selected_at = str(payload.get("symbols_selected_at", ""))
    at = Timestamp.parse(f"{selected_at}T00:00:00+00:00")
    scored_candidates = tuple(
        scored(item, measured) for item in candidates_for(store_root, sorted(measured), at)
    )
    return BandStudy(
        occupancy=occupancy(world, instants),
        recut=recut(measured, scored_candidates),
        selected_at=selected_at,
    )


def write(result: BandStudy, destination: Path) -> Path:
    """Write the study. The path is a parameter with no default pointing anywhere real."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(result.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


__all__ = [
    "BANDS",
    "BANDS_RESULTS",
    "BandStudy",
    "BandStudyIncomplete",
    "Candidate",
    "Occupancy",
    "Recut",
    "band_of",
    "candidates_for",
    "measured_half_spreads",
    "occupancy",
    "permutation_p_value",
    "rebalance_instants",
    "recut",
    "scored",
    "study",
    "write",
]

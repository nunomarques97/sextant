"""What the F1 results will be computed from, measured and written down first.

Part 2 of the pre-registration. It exists as a separate stage, and is committed
before any strategy result, for the reason SEXTANT-005 split its own: the
specification needs no data and must be fixed before any is seen, while the
dataset section can only be written once acquisition has happened. Both halves are
committed before a single return exists.

Every number here is measured rather than asserted. Nothing in this module
evaluates a strategy, ranks an asset or produces a return; it counts what was
downloaded, what was stored, what the window rule resolved to, how broad the carry
universe was at each rebalance, and what each universe rule excluded. Then it
fingerprints the bytes, so a later result can be tied to the data it came from.

Written to ``research/spike-006-f1-dataset.json`` and committed. The
pre-registration document quotes it rather than restating it, so there is one copy
of each figure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from sextant.adapters.exchanges.binance.listing_calendar import BinanceListingCalendar
from sextant.adapters.storage.derivatives import FundingStore, PremiumStore
from sextant.app.futures_archive import (
    CALENDAR_NAME as PERP_CALENDAR_NAME,
)
from sextant.app.futures_archive import (
    FETCH_NAME as PERP_FETCH_NAME,
)
from sextant.app.futures_archive import (
    INDEX_NAME as PERP_INDEX_NAME,
)
from sextant.app.futures_archive import (
    PERP_VENUE,
    FuturesFetchReport,
    FuturesIndex,
    not_evaluable_months,
    total_missing,
)
from sextant.app.futures_archive import (
    STORE_ROOT as PERP_ROOT,
)
from sextant.app.futures_archive import (
    TREES as PERP_TREES,
)
from sextant.app.spike_006_f1 import FAMILY, RESEARCH_ROOT
from sextant.app.spike_006_f1_world import SPOT_ROOT, World, build_world
from sextant.domain.time import Timestamp
from sextant.engine.backtest.trials import dataset_fingerprint

DATASET_PATH = RESEARCH_ROOT / "spike-006-f1-dataset.json"
NEWLINE = chr(10)


@dataclass(frozen=True, slots=True)
class Acquisition:
    """What the download and the ingest produced, from their own reports."""

    objects: int
    bytes_written: int
    minutes: float
    failures: Mapping[str, str]
    publisher_verified: int
    publisher_absent: int
    fingerprint: str

    def as_json(self) -> dict[str, object]:
        return {
            "objects_downloaded": self.objects,
            "bytes_written": self.bytes_written,
            "megabytes_written": round(self.bytes_written / 1_000_000, 1),
            "minutes": round(self.minutes, 1),
            "failures": dict(sorted(self.failures.items())),
            "failure_count": len(self.failures),
            "publisher_checksum_verified": self.publisher_verified,
            "publisher_checksum_absent": self.publisher_absent,
            "dataset_fingerprint": self.fingerprint,
            "fingerprint_basis": (
                "SHA-256 over the sorted set of every downloaded object's own digest, so a "
                "changed dataset is a different trial rather than the same one with different "
                "data. Recorded on every registry row this family writes."
            ),
        }


@dataclass(frozen=True, slots=True)
class Coverage:
    """Which months each tree published, and where the trees disagree."""

    months_by_tree: Mapping[str, tuple[str, str, int]]
    kline_months_without_funding: int
    symbols_with_funding_and_no_klines: tuple[str, ...]
    symbols_with_klines_and_no_premium: tuple[str, ...]
    contracts_stopped_before_the_last_month: int

    def as_json(self) -> dict[str, object]:
        return {
            "months_by_tree": {
                tree: {"first": first, "last": last, "months": count}
                for tree, (first, last, count) in sorted(self.months_by_tree.items())
            },
            "kline_months_with_no_funding_object": self.kline_months_without_funding,
            "kline_months_with_no_funding_object_note": (
                "The listing calendar is built from KLINE presence, not funding presence. "
                "Using funding presence would delist a contract on a missing cash-flow file, "
                "which is a fact about the archive rather than about the venue."
            ),
            "symbols_with_funding_and_no_klines": list(self.symbols_with_funding_and_no_klines),
            "symbols_with_funding_and_no_klines_note": (
                "No kline series means no calendar entry, so these cannot enter the universe. "
                "Counted because silently dropping a contract the venue published cash flows "
                "for is the quiet kind of exclusion invariant 9 exists to make visible."
            ),
            "symbols_with_klines_and_no_premium": list(self.symbols_with_klines_and_no_premium),
            "symbols_with_klines_and_no_premium_note": (
                "Unrankable by carry-premium-10 and counted as not-evaluable there. NOT "
                "excluded from the universe: the other seven variants do not read the premium "
                "index, and dropping an asset because one variant cannot score it would make "
                "the universe depend on which variants exist."
            ),
            "contracts_that_stopped_publishing_before_the_last_month": (
                self.contracts_stopped_before_the_last_month
            ),
        }


def acquisition_facts(*, perp_root: Path = PERP_ROOT) -> Acquisition:
    """The download's own report, and one fingerprint over every byte of it."""
    report = FuturesFetchReport.read_json(perp_root / PERP_FETCH_NAME)
    return Acquisition(
        objects=len(report.digests),
        bytes_written=report.bytes_written,
        minutes=report.seconds / 60,
        failures=report.failures,
        publisher_verified=report.publisher_verified,
        publisher_absent=report.publisher_absent,
        fingerprint=dataset_fingerprint(dict(report.digests)),
    )


def coverage_facts(*, perp_root: Path = PERP_ROOT) -> Coverage:
    """Where the three trees agree on months and symbols, and where they do not."""
    index = FuturesIndex.read_json(perp_root / PERP_INDEX_NAME)
    months_by_tree: dict[str, tuple[str, str, int]] = {}
    for tree in PERP_TREES:
        labels = sorted(
            {item.month.label for objects in index.for_tree(tree).values() for item in objects}
        )
        months_by_tree[tree.value] = (labels[0], labels[-1], len(labels))
    gaps = not_evaluable_months(index)
    funding_symbols = FundingStore(perp_root).symbols(PERP_VENUE)
    premium_symbols = PremiumStore(perp_root).symbols(PERP_VENUE)
    kline_symbols = frozenset(
        symbol for symbol, objects in index.for_tree(PERP_TREES[1]).items() if objects
    )
    calendar = BinanceListingCalendar.read_json(perp_root / PERP_CALENDAR_NAME)
    last_month = months_by_tree[PERP_TREES[1].value][1]
    stopped = sum(1 for entry in calendar.entries.values() if entry.last_seen.label < last_month)
    return Coverage(
        months_by_tree=months_by_tree,
        kline_months_without_funding=total_missing(gaps),
        symbols_with_funding_and_no_klines=tuple(sorted(funding_symbols - kline_symbols)),
        symbols_with_klines_and_no_premium=tuple(sorted(kline_symbols - premium_symbols)),
        contracts_stopped_before_the_last_month=stopped,
    )


@dataclass(frozen=True, slots=True)
class FundingGaps:
    """Settlement instants the venue owed and did not publish.

    Measured because one of them moves the carry universe by two thirds at a single
    rebalance, and a reader who saw only the universe series would have to guess
    why. A hole in a published cash-flow series is a fact about the archive; the
    registered rule's response to it - disqualify the asset for that month - is a
    choice, and both belong in the dataset section rather than in a footnote to a
    result.
    """

    symbols_scanned: int
    symbols_with_a_gap: int
    total_missing_settlements: int
    worst_instants: tuple[tuple[str, int], ...]

    def as_json(self) -> dict[str, object]:
        return {
            "symbols_scanned": self.symbols_scanned,
            "symbols_with_at_least_one_gap": self.symbols_with_a_gap,
            "total_missing_settlements": self.total_missing_settlements,
            "instants_with_the_most_symbols_missing": [
                {"settled_at": at, "symbols_missing_it": count} for at, count in self.worst_instants
            ],
            "how_a_gap_is_detected": (
                "Consecutive published settlements more than one of the venue's own stated "
                "funding intervals apart. The interval is the venue's `funding_interval_hours` "
                "column and is never inferred from the gaps themselves: a hole would redefine "
                "an inferred cadence and declare the series complete."
            ),
            "what_the_registered_rule_does": (
                "Section 6 rule 4 disqualifies an asset whose trailing 30 days contains a gap, "
                "for that rebalance only. It leaves the universe and returns the next month. "
                "The alternative - summing whatever is on file - ranks a partial sum against "
                "whole ones and understates it every time, always in the same direction."
            ),
        }


def funding_gaps(*, perp_root: Path = PERP_ROOT, worst: int = 5) -> FundingGaps:
    """Scan every stored funding series for holes, and tally them by instant.

    Read from the ingested store rather than the raw archive, so this measures the
    same rows the universe rules will read. A gap present in the archive and a gap
    introduced by the ingest would both show here, which is the point: the number
    that matters is what the run can see.
    """
    store = FundingStore(perp_root)
    by_instant: dict[str, int] = {}
    scanned = 0
    with_gap = 0
    total = 0
    for symbol in sorted(store.symbols(PERP_VENUE)):
        rows = store.read_series(PERP_VENUE, symbol)
        if len(rows) < 2:
            continue
        scanned += 1
        found = 0
        for earlier, later in pairwise(rows):
            step = earlier.interval_hours * 60 * 60 * 1000
            elapsed = later.settled_at.epoch_millis - earlier.settled_at.epoch_millis
            if elapsed <= step + 60_000:
                continue
            missing = (elapsed - 1) // step
            for index in range(1, int(missing) + 1):
                at = Timestamp.from_epoch_millis(earlier.settled_at.epoch_millis + index * step)
                label = at.isoformat()[:16]
                by_instant[label] = by_instant.get(label, 0) + 1
                found += 1
        total += found
        if found:
            with_gap += 1
    ranked = sorted(by_instant.items(), key=lambda item: (-item[1], item[0]))
    return FundingGaps(
        symbols_scanned=scanned,
        symbols_with_a_gap=with_gap,
        total_missing_settlements=total,
        worst_instants=tuple(ranked[:worst]),
    )


def universe_facts(world: World) -> dict[str, object]:
    """Carry-universe breadth per rebalance, and what each rule excluded."""
    sizes = world.universe.sizes
    ordered = sorted(sizes)
    thin = [
        at.isoformat()[:10]
        for at in sorted(world.universe.bases)
        if len(world.universe.bases[at]) < ordered[len(ordered) // 2] // 2
    ]
    ordered_instants = sorted(world.universe.bases)
    contractions = [
        (
            ordered_instants[index].isoformat()[:10],
            len(world.universe.bases[ordered_instants[index - 1]]),
            len(world.universe.bases[ordered_instants[index]]),
        )
        for index in range(1, len(ordered_instants))
    ]
    worst = min(contractions, key=lambda item: item[2] - item[1]) if contractions else None
    return {
        "rebalances": len(world.instants),
        "pairs_minimum": min(sizes),
        "pairs_median": ordered[len(ordered) // 2],
        "pairs_maximum": max(sizes),
        "pairs_by_rebalance": {
            at.isoformat()[:10]: len(world.universe.bases[at])
            for at in sorted(world.universe.bases)
        },
        "rebalances_below_half_the_median": thin,
        "largest_month_on_month_contraction": (
            None
            if worst is None
            else {
                "at": worst[0],
                "pairs_before": worst[1],
                "pairs_after": worst[2],
                "note": (
                    "A large single-month contraction is worth explaining rather than "
                    "absorbing. Check it against the funding_gaps block: a settlement instant "
                    "the venue did not publish disqualifies every asset whose trailing 30 days "
                    "contains it, for that rebalance only."
                ),
            }
        ),
        "spot_candidates_with_a_priced_series": len(world.spot_histories),
        "perpetual_candidates_with_a_priced_series": len(world.perpetual_histories),
        "census": world.census.as_json(),
    }


def build_manifest(
    *, spot_root: Path = SPOT_ROOT, perp_root: Path = PERP_ROOT
) -> dict[str, object]:
    """Every measured fact about the dataset, in one payload."""
    world = build_world(spot_root=spot_root, perp_root=perp_root)
    return {
        "family": FAMILY,
        "part": 2,
        "what_this_is": (
            "The dataset section of the F1 pre-registration. Measured after acquisition and "
            "committed before any strategy result exists. Nothing here evaluates a strategy."
        ),
        "spot_archive_root": spot_root.as_posix(),
        "perpetual_archive_root": perp_root.as_posix(),
        "acquisition": acquisition_facts(perp_root=perp_root).as_json(),
        "coverage": coverage_facts(perp_root=perp_root).as_json(),
        "window": world.window.as_json(),
        "funding_gaps": funding_gaps(perp_root=perp_root).as_json(),
        "universe": universe_facts(world),
    }


def write_manifest(
    *,
    spot_root: Path = SPOT_ROOT,
    perp_root: Path = PERP_ROOT,
    path: Path,
) -> Path:
    """Measure everything and write it where the report and the document read it.

    ``path`` has no default pointing anywhere real, per invariant 11.
    """
    payload = build_manifest(spot_root=spot_root, perp_root=perp_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def render_summary(payload: Mapping[str, object]) -> str:
    """The half-dozen figures worth printing when the stage runs."""
    acquisition = payload["acquisition"]
    window = payload["window"]
    universe = payload["universe"]
    if not (
        isinstance(acquisition, dict) and isinstance(window, dict) and isinstance(universe, dict)
    ):
        raise TypeError("the manifest is not shaped as this summary expects")
    lines = [
        f"objects: {acquisition['objects_downloaded']} "
        f"({acquisition['megabytes_written']} MB, {acquisition['failure_count']} failures)",
        f"publisher checksums verified: {acquisition['publisher_checksum_verified']}, "
        f"absent: {acquisition['publisher_checksum_absent']}",
        f"fingerprint: {str(acquisition['dataset_fingerprint'])[:16]}",
        f"window: {window['first_usable_month']} to {window['last_usable_month_end']}, "
        f"{window['usable_months']} usable months",
        f"carry pairs: {universe['pairs_minimum']} to {universe['pairs_maximum']} "
        f"(median {universe['pairs_median']}) across {universe['rebalances']} rebalances",
    ]
    return NEWLINE.join(f"  {line}" for line in lines)


__all__ = [
    "DATASET_PATH",
    "Acquisition",
    "Coverage",
    "FundingGaps",
    "acquisition_facts",
    "build_manifest",
    "coverage_facts",
    "funding_gaps",
    "render_summary",
    "universe_facts",
    "write_manifest",
]

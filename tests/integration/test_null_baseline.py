"""The null-baseline runner, end to end, over a synthetic archive.

Nothing here touches the real dataset, the committed trial registry or the
published report. Every path the runner writes to is a parameter and every one
of them is pointed at ``tmp_path``. That is the standing invariant a test may
never write to real project data, asserted here rather than assumed - the
project has already been bitten once, when a test overwrote the committed
archive checksums.

What is checked: that the whole grid runs, that every result carries its cost
breakdown, that the FX gap is a real number rather than zero, that the three
fill mixes order as they must, that the trial registry is appended to, and that
the report says the things it is required to say.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.adapters.exchanges.kraken.archive import (
    ArchiveFile,
    ArchiveManifest,
    PairPresence,
    Quarter,
)
from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.listing_calendar import (
    KrakenListingCalendar,
    QuarterlyMembership,
)
from sextant.adapters.storage.bars import ParquetBarStore, SeriesKey, StoredBar
from sextant.app import benchmark_config, null_baseline
from sextant.app.benchmark_config import RegistrationDrift
from sextant.domain.time import Timeframe, Timestamp
from tests.harness import ts

#: A small synthetic universe. Two EUR names, two USD names and the FX pair, so
#: that the currency leg has something to convert and the EUR-only policy has
#: something to hold.
SYMBOLS: Mapping[str, tuple[str, str]] = {
    "AAAEUR": ("100", "1.004"),
    "BBBEUR": ("50", "0.997"),
    "CCCUSD": ("200", "1.006"),
    "DDDUSD": ("10", "0.995"),
    "XBTEUR": ("30000", "1.002"),
    "EURUSD": ("1.08", "1.0001"),
}

FIRST_QUARTER = Quarter(2023, 1)
LAST_QUARTER = Quarter(2026, 1)
DAYS = 1190


def _closes(start: str, factor: str) -> list[str]:
    """A smooth geometric path, exact in decimal."""
    price = Decimal(start)
    growth = Decimal(factor)
    values: list[str] = []
    for _ in range(DAYS):
        values.append(str(price.quantize(Decimal("0.00000001"))))
        price *= growth
    return values


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    """A synthetic archive: a bar store, a calendar and a manifest, in tmp_path."""
    root = tmp_path / "archive"
    store = ParquetBarStore(root)
    first_day = ts("2023-01-01T00:00:00")
    for symbol, (start, factor) in SYMBOLS.items():
        store.write_series(
            SeriesKey(VENUE, symbol, Timeframe.D1),
            [
                StoredBar(
                    open_time=Timestamp.from_epoch_millis(
                        first_day.epoch_millis + offset * 86_400_000
                    ),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume="500000",
                    trades=1000,
                    is_closed=True,
                )
                for offset, close in enumerate(_closes(start, factor))
            ],
        )

    quarters: list[Quarter] = []
    cursor = FIRST_QUARTER
    while cursor <= LAST_QUARTER:
        quarters.append(cursor)
        cursor = cursor.next()
    calendar = KrakenListingCalendar.from_membership(
        VENUE,
        [
            QuarterlyMembership(
                quarter=quarter,
                presence=dict.fromkeys(SYMBOLS, PairPresence.LISTED_TRADED),
            )
            for quarter in quarters
        ],
    )
    calendar.write_json(root / "listing_calendar.json")
    ArchiveManifest.of(
        [
            ArchiveFile(
                quarter=quarter,
                path=root / quarter.archive_name,
                sha256=f"{index:064d}",
                size_bytes=1,
            )
            for index, quarter in enumerate(quarters)
        ],
        [],
    ).write_json(root / "manifest.json")
    return root


@pytest.fixture
def config() -> benchmark_config.BenchmarkConfig:
    """The real registered configuration, with the seed count cut for speed."""
    return benchmark_config.load()


def _run(
    tmp_path: Path,
    store_root: Path,
    config: benchmark_config.BenchmarkConfig,
    *,
    seeds: int = 12,
) -> null_baseline.BaselineReport:
    """One full pass of the grid, writing only into ``tmp_path``."""
    return null_baseline.run_all(
        config=config,
        store_root=store_root,
        seed_count=seeds,
        plan_names=("walk_forward",),
        report_path=tmp_path / "NULL-BASELINE.md",
        results_path=tmp_path / "null-baseline.json",
        decisions_path=tmp_path / "decisions.jsonl",
        registry_path=tmp_path / "trial-registry.jsonl",
    )


def test_the_whole_grid_runs_and_every_result_carries_its_cost_breakdown(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Nine cells, two deterministic constructs each, every cost line present."""
    report = _run(tmp_path, store_root, config)

    assert len(report.nulls) == len(config.variants) * len(config.fill_mixes)
    assert len(report.constructs) == len(config.variants) * len(config.fill_mixes) * 2
    for result in report.constructs:
        payload = result.as_json()
        assert payload["denomination"] == "EUR"
        costs = payload["costs"]
        assert isinstance(costs, dict)
        for line in ("fees", "spread", "slippage", "funding", "fx_conversion", "delisting"):
            assert line in costs
        assert "gross_pnl" in payload
        assert "net_pnl" in payload
        assert result.ledger.reconciles()


def test_more_taker_fills_cost_more_in_every_variant(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """The fill mix is an assumption, and its effect is monotone and visible."""
    report = _run(tmp_path, store_root, config)
    by_variant: dict[str, dict[str, Decimal]] = {}
    for result in report.constructs:
        if not result.construct.startswith("equal-weight"):
            continue
        by_variant.setdefault(result.variant, {})[result.fill_mix] = result.ledger.costs.fees.amount
    for fees in by_variant.values():
        ordered = [fees[mix.label] for mix in config.fill_mixes]
        assert ordered[0] < ordered[1] < ordered[2]


def test_pricing_the_currency_leg_changes_the_answer(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """The gap between ignoring FX and applying it is the price of the breadth."""
    report = _run(tmp_path, store_root, config)
    by_key = {(item.construct, item.variant, item.fill_mix): item for item in report.constructs}
    compared = 0
    for (construct, variant, mix), item in by_key.items():
        if variant != "eur_usd_fx_ignored":
            continue
        applied = by_key[(construct, "eur_usd_fx_applied", mix)]
        assert item.ledger.costs.fx_conversion.amount == 0
        if construct.startswith("single-asset"):
            # The single-asset benchmark holds a EUR-quoted instrument, so it
            # crosses no currency boundary and the two variants agree exactly.
            # That agreement is itself the check: the leg is charged where it
            # applies and nowhere else.
            assert applied.ledger.costs.fx_conversion.amount == 0
            assert applied.ledger.terminal_return == item.ledger.terminal_return
            continue
        assert applied.ledger.costs.fx_conversion.amount > 0
        assert applied.ledger.terminal_return != item.ledger.terminal_return
        compared += 1
    assert compared > 0


def test_the_eur_only_policy_never_charges_a_conversion(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """An account that stays in its own currency has no currency leg to pay for."""
    report = _run(tmp_path, store_root, config)
    for result in report.constructs:
        if result.variant == "eur_only":
            assert result.ledger.costs.fx_conversion.amount == 0


def test_the_counterfactual_variant_is_labelled_everywhere_it_appears(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """A figure that is not a currency treatment must never read as one."""
    report = _run(tmp_path, store_root, config)
    for result in report.constructs:
        assert result.is_counterfactual == (result.variant == "eur_usd_fx_ignored")
    text = (tmp_path / "NULL-BASELINE.md").read_text(encoding="utf-8")
    assert "counterfactual" in text
    assert "intermediate" in text


def test_the_report_states_the_seed_count_and_the_percentile_intervals(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """A gate built on an unstated seed count or an unstated interval is not a gate."""
    _run(tmp_path, store_root, config)
    text = (tmp_path / "NULL-BASELINE.md").read_text(encoding="utf-8")
    assert "without replacement" in text
    assert "bootstrap interval" in text
    assert "rejection filter" in text
    assert "has not demonstrated edge" in text
    assert "ASSUMPTION" in text or "assumption, not a measurement" in text


def test_every_reported_percentile_carries_an_interval(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """50th, 90th, 95th and 99th, each with a bootstrap confidence interval."""
    report = _run(tmp_path, store_root, config)
    for _, distribution in report.nulls:
        assert set(distribution.percentile_estimates) == set(config.percentiles)
        for estimate in distribution.percentile_estimates.values():
            assert estimate.low <= estimate.value <= estimate.high
            assert estimate.resamples == config.bootstrap_resamples


def test_the_run_is_reproducible_given_the_same_seeds(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Same dataset, same configuration, same seed set, same distribution."""
    first = _run(tmp_path / "a", store_root, config)
    second = _run(tmp_path / "b", store_root, config)
    for (_, left), (_, right) in zip(first.nulls, second.nulls, strict=True):
        assert left.sharpes == right.sharpes
        assert left.terminal_returns == right.terminal_returns


def test_the_trial_registry_is_appended_to_and_never_the_committed_one(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Every construct is recorded, in a registry inside the temporary directory."""
    report = _run(tmp_path, store_root, config)
    registry_file = tmp_path / "trial-registry.jsonl"
    assert registry_file.is_file()
    assert report.trial_count_including_nulls > 0
    assert report.trial_count == 0, "no strategy exists yet, so the search count is zero"
    assert registry_file.resolve() != (Path("research") / "trial-registry.jsonl").resolve()


def test_rerunning_does_not_inflate_the_trial_count(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Producing the report twice is not twice as many trials."""
    first = _run(tmp_path, store_root, config)
    second = _run(tmp_path, store_root, config)
    assert first.trial_count_including_nulls == second.trial_count_including_nulls


def test_the_results_json_carries_every_assumption_as_an_assumption(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """The manifest must never let a configured cost read as a measured one."""
    _run(tmp_path, store_root, config)
    payload = json.loads((tmp_path / "null-baseline.json").read_text(encoding="utf-8"))
    manifests = payload["manifests"]
    assert manifests
    for manifest in manifests.values():
        costs = manifest["cost_assumptions"]
        assert costs["spread_kind"] == "assumption"
        assert costs["slippage_kind"] == "assumption"
        assert costs["fee_kind"] == "published schedule"
        assert "ASSUMPTION" in costs["fill_mix_kind"]
        assert manifest["code_version"]
        assert manifest["library_versions"]["numpy"]
        assert manifest["dataset_checksums"]


def test_a_cost_assumption_that_has_drifted_from_the_registration_stops_the_run(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Pre-registration that can silently disagree with the code is not registration."""
    drifted = replace(config, maker_bps=Decimal(5))
    with pytest.raises(RegistrationDrift, match="maker fee"):
        _run(tmp_path, store_root, drifted)


def test_a_missing_fx_series_stops_the_run_rather_than_defaulting_to_parity(
    tmp_path: Path, store_root: Path, config: benchmark_config.BenchmarkConfig
) -> None:
    """Applying a currency leg with no rates is the counterfactual, mislabelled."""
    (store_root / "bars" / f"venue={VENUE.name}" / "timeframe=1d" / "EURUSD.parquet").unlink()
    with pytest.raises(ValueError, match="currency leg cannot be priced"):
        _run(tmp_path, store_root, config)

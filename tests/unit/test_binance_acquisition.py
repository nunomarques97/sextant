"""The acquisition pipeline: what it plans, what it refuses, what it records.

Nothing here reaches the network. The index a listing would have produced is
built by hand, because what is under test is the reasoning on top of a listing
rather than the listing itself.

Every path written to is under ``tmp_path``. Invariant 11: the committed
checksum record and the trial registry are things a test must not be able to
touch, and the instance that prompted the invariant was a test that overwrote
the real archive checksums.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sextant.adapters.exchanges.binance.archive import Month, MonthlyFile, months_between
from sextant.app.binance_archive import (
    WARMUP_MONTHS,
    AcquisitionPlan,
    FetchReport,
    MonthIndex,
    build_plan,
    cold_start_days,
    dataset_checksums,
    is_leveraged_token,
    missing_months,
    raw_path,
    split_symbol,
    symbols_in_policy,
    usable_window_instants,
    write_checksums,
)


def _file(symbol: str, month: Month) -> MonthlyFile:
    return MonthlyFile(
        symbol=symbol,
        interval="1d",
        month=month,
        key=f"data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month.label}.zip",
        size_bytes=2048,
    )


def _index(
    symbols: dict[str, tuple[Month, ...]],
    *,
    first: Month,
    last: Month,
) -> MonthIndex:
    return MonthIndex(
        files={
            symbol: tuple(_file(symbol, month) for month in months)
            for symbol, months in symbols.items()
        },
        first_month=first,
        last_month=last,
    )


def _span(first: Month, last: Month) -> tuple[Month, ...]:
    return months_between(first, last)


# ---------------------------------------------------------------------------
# Symbol classification
# ---------------------------------------------------------------------------


def test_only_the_two_registered_quote_assets_are_recognised() -> None:
    assert split_symbol("BTCUSDT") == ("BTC", "USDT")
    assert split_symbol("BTCEUR") == ("BTC", "EUR")
    assert split_symbol("BTCTRY") is None
    assert split_symbol("USDT") is None


def test_a_leveraged_token_is_caught_only_when_its_stem_is_a_real_asset() -> None:
    """The stem test is what makes it a rule rather than a hand-curated list."""
    known = frozenset({"BTC", "ADA", "SUPER"})
    assert is_leveraged_token("BTCUP", known)
    assert is_leveraged_token("ADADOWN", known)
    assert not is_leveraged_token("SUPER", known)
    assert not is_leveraged_token("JUP", known)
    assert not is_leveraged_token("UP", known)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_the_window_rule_starts_the_warmup_after_the_fx_pair_appears() -> None:
    fx_first = Month(2020, 1)
    index = _index(
        {
            "EURUSDT": _span(fx_first, Month(2026, 8)),
            "BTCUSDT": _span(Month(2019, 1), Month(2026, 8)),
        },
        first=Month(2019, 1),
        last=Month(2026, 8),
    )
    plan = build_plan(index)
    expected = fx_first
    for _ in range(WARMUP_MONTHS):
        expected = expected.next()
    assert plan.first_usable_month == expected
    assert plan.fx_first_month == fx_first
    assert plan.last_usable_month_end == Month(2026, 8)
    assert plan.first_fetch_month == fx_first


def test_the_fetch_window_never_reaches_earlier_than_the_warmup_needs() -> None:
    """The brief forbids nine years of everything in order to have nine years."""
    index = _index(
        {
            "EURUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2017, 8), Month(2026, 8)),
        },
        first=Month(2017, 8),
        last=Month(2026, 8),
    )
    plan = build_plan(index)
    fetched = {item.month for item in plan.objects if item.symbol == "BTCUSDT"}
    assert min(fetched) == plan.first_fetch_month
    assert Month(2017, 8) not in fetched
    assert cold_start_days(plan) > 360


def test_a_missing_fx_pair_stops_the_plan_rather_than_substituting_one() -> None:
    index = _index(
        {"BTCUSDT": _span(Month(2020, 1), Month(2026, 8))},
        first=Month(2020, 1),
        last=Month(2026, 8),
    )
    with pytest.raises(ValueError, match="EURUSDT"):
        build_plan(index)


def test_a_window_below_the_registered_floor_refuses_to_proceed() -> None:
    """Part 1 section 5: below 36 months the spike reports (C) on sample size alone."""
    index = _index(
        {
            "EURUSDT": _span(Month(2024, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2024, 1), Month(2026, 8)),
        },
        first=Month(2024, 1),
        last=Month(2026, 8),
    )
    with pytest.raises(ValueError, match="below the pre-registered floor"):
        build_plan(index)


def test_symbols_outside_the_policies_are_excluded_with_their_reason() -> None:
    index = _index(
        {
            "EURUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUPUSDT": _span(Month(2021, 6), Month(2022, 1)),
            # Its only month predates the fetch window, warm-up included.
            "OLDUSDT": (Month(2019, 2),),
        },
        first=Month(2019, 1),
        last=Month(2026, 8),
    )
    plan = build_plan(index)
    assert "leveraged token" in plan.excluded_symbols["BTCUPUSDT"]
    assert "no month inside the window" in plan.excluded_symbols["OLDUSDT"]
    assert "BTCUPUSDT" not in plan.symbols
    assert "BTCUSDT" in plan.symbols


def test_the_plan_reports_what_it_will_weigh_before_anything_is_fetched() -> None:
    index = _index(
        {
            "EURUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2020, 1), Month(2026, 8)),
        },
        first=Month(2020, 1),
        last=Month(2026, 8),
    )
    plan = build_plan(index)
    assert plan.total_bytes() == len(plan.objects) * 2048
    assert plan.usable_months == len(_span(plan.first_usable_month, Month(2026, 8))) - 1
    start, end = usable_window_instants(plan)
    assert start == plan.first_usable_month.starts_at
    assert end == plan.last_usable_month_end.starts_at


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_the_index_and_the_plan_round_trip_through_json(tmp_path: Path) -> None:
    index = _index(
        {
            "EURUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2020, 1), Month(2026, 8)),
        },
        first=Month(2020, 1),
        last=Month(2026, 8),
    )
    index.write_json(tmp_path / "month_index.json")
    reloaded = MonthIndex.read_json(tmp_path / "month_index.json")
    assert reloaded.first_month == index.first_month
    assert reloaded.files.keys() == index.files.keys()

    plan = build_plan(index)
    plan.write_json(tmp_path / "acquisition_plan.json")
    again = AcquisitionPlan.read_json(tmp_path / "acquisition_plan.json", reloaded)
    assert again.first_usable_month == plan.first_usable_month
    assert len(again.objects) == len(plan.objects)
    assert again.excluded_symbols == plan.excluded_symbols


def test_presence_is_a_flag_per_month_of_the_whole_archive_span() -> None:
    index = _index(
        {"AAAUSDT": (Month(2020, 1), Month(2020, 3))},
        first=Month(2020, 1),
        last=Month(2020, 4),
    )
    assert index.presence()["AAAUSDT"] == (True, False, True, False)
    assert index.total_bytes() == 2 * 2048


def test_an_interior_hole_is_reported_rather_than_smoothed_over() -> None:
    index = _index(
        {"AAAUSDT": (Month(2020, 1), Month(2020, 4))},
        first=Month(2020, 1),
        last=Month(2020, 4),
    )
    assert missing_months(index, ["AAAUSDT"])["AAAUSDT"] == ("2020-02", "2020-03")


def test_symbols_are_listed_per_quote_policy() -> None:
    index = _index(
        {"AAAUSDT": (Month(2020, 1),), "AAAEUR": (Month(2020, 1),)},
        first=Month(2020, 1),
        last=Month(2020, 1),
    )
    assert symbols_in_policy(index, "USDT") == ("AAAUSDT",)
    assert symbols_in_policy(index, "EUR") == ("AAAEUR",)


def test_the_fetch_report_round_trips_and_carries_its_failures(tmp_path: Path) -> None:
    """A failure is never an empty result: it is recorded with its reason."""
    report = FetchReport(
        digests={"a/b/c.zip": "0" * 64},
        bytes_written=2048,
        seconds=12.5,
        failures={"a/b/d.zip": "ArchiveError: not a readable ZIP"},
    )
    report.write_json(tmp_path / "fetch_report.json")
    reloaded = FetchReport.read_json(tmp_path / "fetch_report.json")
    assert reloaded.digests == report.digests
    assert reloaded.failures == report.failures
    assert dataset_checksums(reloaded) == dict(sorted(report.digests.items()))


def test_the_checksum_record_localises_a_difference_to_a_symbol(tmp_path: Path) -> None:
    index = _index(
        {
            "EURUSDT": _span(Month(2020, 1), Month(2026, 8)),
            "BTCUSDT": _span(Month(2020, 1), Month(2026, 8)),
        },
        first=Month(2020, 1),
        last=Month(2026, 8),
    )
    plan = build_plan(index)
    digests = {item.key: f"{index_of:064x}" for index_of, item in enumerate(plan.objects)}
    report = FetchReport(digests=digests, bytes_written=1, seconds=1.0, failures={})

    path = tmp_path / "checksums.md"
    rows = write_checksums(report, plan, path)
    first = path.read_text(encoding="utf-8")
    assert rows == 2
    assert "| `BTCUSDT` |" in first
    assert "Dataset fingerprint:" in first

    changed = dict(digests)
    key = next(item.key for item in plan.objects if item.symbol == "BTCUSDT")
    changed[key] = "f" * 64
    second = write_checksums(
        FetchReport(digests=changed, bytes_written=1, seconds=1.0, failures={}), plan, path
    )
    assert second == 2
    assert path.read_text(encoding="utf-8") != first


def test_a_downloaded_object_lands_under_its_own_symbol(tmp_path: Path) -> None:
    item = _file("BTCUSDT", Month(2024, 1))
    assert raw_path(item, tmp_path) == tmp_path / "BTCUSDT" / "BTCUSDT-1d-2024-01.zip"
    assert item.url.endswith("BTCUSDT-1d-2024-01.zip")
    assert item.name == "BTCUSDT-1d-2024-01.zip"

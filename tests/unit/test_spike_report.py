"""The offline half of the research spike.

Everything here reads a fetched tree off disk and produces the universe tables,
so the tests build a small, fully controlled tree and assert on the numbers that
come out. Two behaviours carry the weight:

* a first bar sitting on the edge of a truncated servable window dates the
  truncation, not the listing, and must be recorded ``UNVERIFIED``;
* a symbol established by announcement but absent from the venue's instrument
  endpoint stays in the calendar as unrebuildable rather than disappearing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sextant.app.spike_report import (
    MAX_MEDIAN_SPREAD_BPS,
    MIN_MEDIAN_QUOTE_VOLUME,
    VenueDataset,
    candidates,
    days_of_history,
    histories,
    measure,
    month_starts,
    observed_window,
    policies,
    reconstruct_calendar,
    render_markdown,
    target_position_note,
)
from sextant.domain.instrument import InstrumentKey
from sextant.domain.money import Notional
from sextant.domain.provenance import Provenance
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue
from sextant.engine.universe.rules import AccountParameters

VENUE = Venue("testvenue")
LIVE = frozenset({"online"})
DAY_MILLIS = 86_400_000


def at(text: str) -> Timestamp:
    """A UTC instant from a bare date."""
    return Timestamp(datetime.fromisoformat(text).replace(tzinfo=UTC))


def daily_rows(start: str, days: int, close: str, quote_volume: str) -> list[list[object]]:
    """A flat daily series with a constant close and turnover."""
    first = at(start).epoch_millis
    return [[first + index * DAY_MILLIS, close, quote_volume] for index in range(days)]


def write_venue_tree(
    root: Path,
    *,
    metadata: dict[str, dict[str, str]],
    series: dict[str, list[list[object]]],
    spreads: dict[str, str] | None = None,
    spread_observed_at: str | None = None,
) -> Path:
    """Write a fetched tree in the shape the collect stages produce."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pair_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    bars = root / "bars"
    bars.mkdir(exist_ok=True)
    for symbol, rows in series.items():
        (bars / f"{symbol}.json").write_text(
            json.dumps({"symbol": symbol, "timeframe": "1d", "rows": rows}), encoding="utf-8"
        )
    if spreads is not None:
        payload: dict[str, object] = {"bps": spreads}
        if spread_observed_at is not None:
            payload["observed_at"] = at(spread_observed_at).isoformat()
        (root / "spread_snapshot.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def pair(base: str, quote: str, status: str = "online") -> dict[str, str]:
    """One metadata entry with constraints an ordinary account can meet."""
    return {
        "canonical": f"X{base}Z{quote}",
        "base": base,
        "quote": quote,
        "status": status,
        "tick_size": "0.01",
        "lot_size": "0.00001",
        "min_notional": "0.45",
    }


def dataset(tmp_path: Path) -> VenueDataset:
    """A two-pair venue: one live survivor and one the venue no longer lists."""
    root = write_venue_tree(
        tmp_path / "testvenue",
        metadata={
            "BTCEUR": pair("BTC", "EUR"),
            "DEADEUR": pair("DEAD", "EUR", status="delisted"),
        },
        series={
            "BTCEUR": daily_rows("2024-01-01", 400, "50000", "9000000"),
            "DEADEUR": daily_rows("2024-01-01", 200, "3", "9000000"),
        },
        spreads={"BTCEUR": "4"},
        spread_observed_at="2025-02-01",
    )
    return VenueDataset.load(VENUE, root)


def account() -> AccountParameters:
    """The D2 account: 1,500 EUR over at most 8 concurrent positions."""
    return AccountParameters(equity_quote=Notional(Decimal(1500)), max_positions=8)


# -- loading -----------------------------------------------------------------


def test_a_fetched_tree_round_trips_into_a_dataset(tmp_path: Path) -> None:
    subject = dataset(tmp_path)

    assert set(subject.metadata) == {"BTCEUR", "DEADEUR"}
    assert len(subject.series["BTCEUR"]) == 400
    assert subject.spreads_bps == {"BTCEUR": Decimal(4)}
    assert subject.spread_observed_at == at("2025-02-01")


def test_a_dataset_with_no_bars_and_no_snapshot_still_loads(tmp_path: Path) -> None:
    """The measure stage must be able to say "nothing was fetched" rather than crash."""
    root = write_venue_tree(tmp_path / "empty", metadata={"BTCEUR": pair("BTC", "EUR")}, series={})

    subject = VenueDataset.load(VENUE, root)

    assert subject.series == {}
    assert subject.spreads_bps == {}
    assert subject.spread_observed_at is None
    assert observed_window(subject) is None
    assert days_of_history(subject) == 0


def test_the_symbol_metadata_filename_is_used_when_present(tmp_path: Path) -> None:
    """The two venues name the same artifact differently; both must load."""
    root = tmp_path / "alt"
    root.mkdir()
    (root / "symbol_metadata.json").write_text(
        json.dumps({"BTCEUR": pair("BTC", "EUR")}), encoding="utf-8"
    )

    assert set(VenueDataset.load(VENUE, root).metadata) == {"BTCEUR"}


def test_a_snapshot_without_an_instant_yields_no_observation_time(tmp_path: Path) -> None:
    root = write_venue_tree(
        tmp_path / "nostamp",
        metadata={"BTCEUR": pair("BTC", "EUR")},
        series={"BTCEUR": daily_rows("2024-01-01", 5, "1", "1")},
        spreads={"BTCEUR": "4"},
    )

    assert VenueDataset.load(VENUE, root).spread_observed_at is None


# -- calendar reconstruction -------------------------------------------------


def test_a_live_symbol_gets_no_delisting_date_and_a_dead_one_does(tmp_path: Path) -> None:
    calendar = reconstruct_calendar(dataset(tmp_path), live_statuses=LIVE)

    alive = calendar.entry_for("BTCEUR")
    dead = calendar.entry_for("DEADEUR")
    assert alive is not None and alive.window.delisted_at is None
    assert dead is not None and dead.window.delisted_at == at("2024-07-19")
    assert alive.window.provenance is Provenance.RECONSTRUCTED


def test_a_first_bar_on_the_truncation_edge_is_recorded_unverified(tmp_path: Path) -> None:
    """The edge of a servable window is not a birthday.

    A venue that serves only the last two years hands back the same first bar
    for every symbol. Treating that as a listing date would age the whole
    universe from one fictional date and produce a confident, wrong answer.
    """
    subject = dataset(tmp_path)

    calendar = reconstruct_calendar(
        subject, live_statuses=LIVE, truncation_boundary=at("2024-01-01")
    )

    entry = calendar.entry_for("BTCEUR")
    assert entry is not None
    assert entry.window.provenance is Provenance.UNVERIFIED
    assert "dates the truncation" in entry.window.note


def test_a_symbol_the_venue_no_longer_describes_survives_as_unrebuildable(
    tmp_path: Path,
) -> None:
    calendar = reconstruct_calendar(
        dataset(tmp_path),
        live_statuses=LIVE,
        known_missing=(("GONEEUR", at("2024-09-25")),),
    )

    entry = calendar.entry_for("GONEEUR")
    assert entry is not None
    assert not entry.metadata_available
    assert entry.window.provenance is Provenance.VENUE_ANNOUNCEMENT
    assert calendar.missing_at(at("2024-06-01")) == frozenset({"GONEEUR"})
    assert calendar.missing_at(at("2025-06-01")) == frozenset()


def test_a_symbol_with_no_fetched_bars_gets_no_calendar_entry(tmp_path: Path) -> None:
    """No bars is not evidence of a listing window, so none is invented."""
    root = write_venue_tree(
        tmp_path / "sparse",
        metadata={"BTCEUR": pair("BTC", "EUR"), "NOBARS": pair("NOB", "EUR")},
        series={"BTCEUR": daily_rows("2024-01-01", 5, "1", "1")},
    )

    calendar = reconstruct_calendar(VenueDataset.load(VENUE, root), live_statuses=LIVE)

    assert calendar.entry_for("NOBARS") is None
    assert calendar.entry_for("BTCEUR") is not None


def test_the_methodology_travels_with_the_calendar(tmp_path: Path) -> None:
    """The report quotes it verbatim, so a reader can judge it rather than trust it."""
    calendar = reconstruct_calendar(dataset(tmp_path), live_statuses=LIVE)

    assert "lower bound" in calendar.methodology


# -- histories and candidates ------------------------------------------------


def test_histories_are_queryable_at_a_point_in_time(tmp_path: Path) -> None:
    built = histories(dataset(tmp_path))

    history = built[InstrumentKey(VENUE, "BTCEUR")]
    assert history.first_open_time == at("2024-01-01")
    assert history.close_as_of(at("2024-01-05")) is not None
    assert history.close_as_of(at("2023-01-01")) is None


def test_only_symbols_with_rebuildable_constraints_become_candidates(tmp_path: Path) -> None:
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(
        subject, live_statuses=LIVE, known_missing=(("GONEEUR", at("2024-09-25")),)
    )

    built = candidates(subject, calendar)

    assert [instrument.symbol for instrument in built] == ["BTCEUR", "DEADEUR"]
    assert built[0].quote == "EUR"


# -- refresh instants --------------------------------------------------------


def test_month_starts_are_the_first_utc_day_of_each_covered_month() -> None:
    months = month_starts(at("2024-01-15"), at("2024-04-02"))

    assert [instant.isoformat()[:10] for instant in months] == [
        "2024-02-01",
        "2024-03-01",
        "2024-04-01",
    ]


def test_month_starts_roll_over_a_year_boundary() -> None:
    months = month_starts(at("2023-11-01"), at("2024-02-01"))

    assert [instant.isoformat()[:7] for instant in months] == [
        "2023-11",
        "2023-12",
        "2024-01",
        "2024-02",
    ]


def test_the_observed_window_spans_every_series(tmp_path: Path) -> None:
    subject = dataset(tmp_path)

    window = observed_window(subject)

    assert window is not None
    assert window[0] == at("2024-01-01")
    assert window[1] == at("2025-02-03")
    assert days_of_history(subject) == 399


# -- policies and measurement ------------------------------------------------


def test_the_four_policies_carry_the_rules_decision_d1_assigns_them(tmp_path: Path) -> None:
    """Rules 5 and 6 describe our wallet, so they appear only in ``executable``."""
    subject = dataset(tmp_path)
    liquidity, research, with_spread, executable = policies(
        frozenset({"EUR"}), frozenset(), histories(subject), account(), {}
    )

    assert liquidity.rule_names == ("quote_currency", "median_quote_volume", "asset_class")
    assert "listing_age" in research.rule_names
    assert with_spread.rule_names[-1] == "median_spread"
    assert executable.rule_names[-2:] == ("min_notional_feasible", "lot_size_feasible")
    assert set(research.rule_names) < set(executable.rule_names)


def test_measurement_produces_one_row_per_refresh_with_per_rule_counts(
    tmp_path: Path,
) -> None:
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(subject, live_statuses=LIVE)
    months = month_starts(at("2024-01-01"), at("2025-02-01"))

    rows = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)

    assert len(rows) == len(months)
    assert [row.month for row in rows][:2] == ["2024-01", "2024-02"]
    # 180 days of listing age from 2024-01-01 falls inside 2024-07.
    admitted = {row.month: row.research.size for row in rows}
    assert admitted["2024-01"] == 0
    assert admitted["2024-08"] == 1
    serialised = rows[0].as_json()
    assert serialised["candidates"] == 2
    assert "listing_age" in serialised["research_rejected"]


def test_an_excluded_base_asset_is_rejected_by_rule_seven(tmp_path: Path) -> None:
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(subject, live_statuses=LIVE)
    months = month_starts(at("2024-08-01"), at("2024-09-01"))

    kept = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)
    dropped = measure(subject, calendar, frozenset({"EUR"}), frozenset({"BTC"}), account(), months)

    assert kept[0].research.size == 1
    assert dropped[0].research.size == 0
    assert dropped[0].research.rejection_counts()["asset_class"] == 1


def test_the_spread_rule_is_evaluable_only_at_the_measured_instant(tmp_path: Path) -> None:
    """A snapshot is not a history, and the table must show that rather than hide it."""
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(subject, live_statuses=LIVE)
    months = month_starts(at("2024-08-01"), at("2025-02-01"))

    rows = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)

    earlier, latest = rows[0], rows[-1]
    assert earlier.research_with_spread.size == 0
    assert earlier.research_with_spread.not_evaluable_counts()["median_spread"] == 2
    assert latest.research_with_spread.size == 1


def test_measurement_is_deterministic(tmp_path: Path) -> None:
    """Same files, byte-identical membership. Otherwise no table can be diffed."""
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(subject, live_statuses=LIVE)
    months = month_starts(at("2024-08-01"), at("2024-10-01"))

    first = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)
    second = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)

    assert [row.as_json() for row in first] == [row.as_json() for row in second]


# -- rendering ---------------------------------------------------------------


def test_a_table_renders_one_markdown_row_per_month(tmp_path: Path) -> None:
    subject = dataset(tmp_path)
    calendar = reconstruct_calendar(subject, live_statuses=LIVE)
    months = month_starts(at("2024-08-01"), at("2024-10-01"))
    rows = measure(subject, calendar, frozenset({"EUR"}), frozenset(), account(), months)

    rendered = render_markdown(VENUE, "EUR", rows)

    assert rendered.startswith("#### testvenue - quote policy `EUR`")
    assert rendered.count("| 2024-") == 3
    assert rendered.endswith("\n")


def test_the_account_thresholds_actually_applied_are_stated_in_words() -> None:
    note = target_position_note(account())

    assert "1500" in note
    assert "187.5" in note
    assert "46.875" in note
    assert "1.875" in note


def test_the_reported_thresholds_are_the_ones_the_rules_use() -> None:
    """The prose and the constants cannot drift apart without this failing."""
    assert Notional(Decimal(250_000)) == MIN_MEDIAN_QUOTE_VOLUME
    assert Decimal(25) == MAX_MEDIAN_SPREAD_BPS

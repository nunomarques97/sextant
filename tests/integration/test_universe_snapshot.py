"""``sextant snapshot-universe`` and the membership store beneath it.

This is the permanent fix, so the properties that matter are the boring ones: it
records what the venue said, it records the venue's own jurisdiction answer
alongside it, it is idempotent per UTC day, and two recorded days diff into
brackets of exactly the same grade as the archive calendar's.

The venue sits behind ``httpx.MockTransport``, so the real error-envelope
classification and the real ``AssetPairs`` parser both execute.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from sextant.adapters.clocks import SimulatedClock
from sextant.adapters.exchanges.http import HttpTransport, RateLimiter, RequestJournal
from sextant.adapters.exchanges.kraken.client import KrakenClient
from sextant.adapters.storage.membership import (
    MembershipRow,
    MembershipStore,
    UniverseSnapshot,
)
from sextant.app import universe_snapshot
from sextant.domain.capability import Capability
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

VENUE = Venue("kraken")
TUESDAY = Timestamp(datetime(2026, 9, 8, 9, 0, tzinfo=UTC))
TUESDAY_LATER = Timestamp(datetime(2026, 9, 8, 23, 59, tzinfo=UTC))
WEDNESDAY = Timestamp(datetime(2026, 9, 9, 9, 0, tzinfo=UTC))

ALL_PAIRS: dict[str, object] = {
    "XXBTZEUR": {
        "altname": "XBTEUR",
        "wsname": "XBT/EUR",
        "status": "online",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
    "ANTEUR": {
        "altname": "ANTEUR",
        "wsname": "ANT/EUR",
        "status": "online",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
    "RESTRICTEDEUR": {
        "altname": "RESTRICTEDEUR",
        "wsname": "RES/EUR",
        "status": "online",
        "tick_size": "0.1",
        "lot_decimals": 8,
        "costmin": "0.45",
    },
}

#: What the venue returns once ANT has been delisted.
AFTER_DELISTING = {key: value for key, value in ALL_PAIRS.items() if key != "ANTEUR"}


def handler_for(pairs: dict[str, object]) -> object:
    """A stub venue that applies its own jurisdiction filter when asked."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("country_code") == "PT":
            visible = {k: v for k, v in pairs.items() if k != "RESTRICTEDEUR"}
            return httpx.Response(200, json={"error": [], "result": visible})
        return httpx.Response(200, json={"error": [], "result": pairs})

    return handle


def client_for(pairs: dict[str, object], country_code: str | None = None) -> KrakenClient:
    """A public read-only client over the stub venue."""
    public = frozenset({Capability.PUBLIC_MARKET_DATA, Capability.HISTORICAL_OHLCV})
    transport = HttpTransport(
        venue=VENUE,
        base_url="https://api.kraken.test",
        limiter=RateLimiter(rate_per_second=100_000.0, burst=100_000.0),
        journal=RequestJournal(),
        client=httpx.Client(transport=httpx.MockTransport(handler_for(pairs))),  # type: ignore[arg-type]
    )
    return KrakenClient(
        account_permitted=public,
        jurisdiction_eligible=public,
        transport=transport,
        country_code=country_code,
    )


def take(store: MembershipStore, when: Timestamp, pairs: dict[str, object]) -> object:
    """Run one snapshot against the stub venue."""
    return universe_snapshot.snapshot_universe(
        store,
        SimulatedClock(when),
        client=client_for(pairs),
        restricted_client=client_for(pairs, country_code="PT"),
    )


# -- the store ---------------------------------------------------------------


def test_a_snapshot_round_trips_through_disk(tmp_path: Path) -> None:
    store = MembershipStore(tmp_path)
    snapshot = UniverseSnapshot.of(
        VENUE,
        TUESDAY,
        [
            MembershipRow("XBTEUR", "XBT", "EUR", "online", True),
            MembershipRow("ANTEUR", "ANT", "EUR", "online", False),
        ],
    )

    written = store.append(snapshot)
    restored = store.read(VENUE, date(2026, 9, 8))

    assert written == 2
    assert restored == snapshot
    assert restored.symbols == frozenset({"XBTEUR", "ANTEUR"})
    assert restored.jurisdiction_visible == frozenset({"XBTEUR"})


def test_reading_a_day_that_was_never_recorded_raises(tmp_path: Path) -> None:
    store = MembershipStore(tmp_path)

    with pytest.raises(FileNotFoundError, match="No membership snapshot"):
        store.read(VENUE, date(2026, 9, 8))


def test_an_empty_store_lists_no_days(tmp_path: Path) -> None:
    assert MembershipStore(tmp_path).days(VENUE) == ()


# -- the command -------------------------------------------------------------


def test_a_snapshot_records_what_the_venue_listed_and_what_it_lets_pt_trade(
    tmp_path: Path,
) -> None:
    """The jurisdiction layer sourced from the venue rather than from our config."""
    store = MembershipStore(tmp_path)

    result = take(store, TUESDAY, ALL_PAIRS)

    assert result.written
    assert result.listed == 3
    assert result.jurisdiction_visible == 2
    assert result.observed_on == "2026-09-08"
    recorded = store.read(VENUE, date(2026, 9, 8))
    assert "RESTRICTEDEUR" in recorded.symbols
    assert "RESTRICTEDEUR" not in recorded.jurisdiction_visible


def test_a_second_run_on_the_same_day_writes_nothing(tmp_path: Path) -> None:
    """Idempotent per UTC day.

    A re-run at 23:59 must not replace what we saw at 09:00. The record is of
    what the venue listed, not of how often somebody ran a command.
    """
    store = MembershipStore(tmp_path)
    take(store, TUESDAY, ALL_PAIRS)

    second = take(store, TUESDAY_LATER, AFTER_DELISTING)

    assert not second.written
    assert store.days(VENUE) == (date(2026, 9, 8),)
    assert "ANTEUR" in store.read(VENUE, date(2026, 9, 8)).symbols


def test_overwriting_a_day_has_to_be_asked_for(tmp_path: Path) -> None:
    store = MembershipStore(tmp_path)
    take(store, TUESDAY, ALL_PAIRS)

    universe_snapshot.snapshot_universe(
        store,
        SimulatedClock(TUESDAY_LATER),
        client=client_for(AFTER_DELISTING),
        restricted_client=client_for(AFTER_DELISTING, country_code="PT"),
        overwrite=True,
    )

    assert "ANTEUR" not in store.read(VENUE, date(2026, 9, 8)).symbols


def test_two_recorded_days_diff_into_a_bracketed_delisting(tmp_path: Path) -> None:
    """The permanent fix, working. No date is invented and no price is consulted."""
    store = MembershipStore(tmp_path)
    take(store, TUESDAY, ALL_PAIRS)
    take(store, WEDNESDAY, AFTER_DELISTING)

    changes = store.diff(VENUE, date(2026, 9, 8), date(2026, 9, 9))

    assert changes.delisted == ("ANTEUR",)
    assert changes.listed == ()
    assert changes.as_json()["earlier"] == "2026-09-08"
    assert changes.as_json()["later"] == "2026-09-09"
    assert store.days(VENUE) == (date(2026, 9, 8), date(2026, 9, 9))


def test_a_new_listing_is_bracketed_the_same_way(tmp_path: Path) -> None:
    store = MembershipStore(tmp_path)
    take(store, TUESDAY, AFTER_DELISTING)
    take(store, WEDNESDAY, ALL_PAIRS)

    changes = store.diff(VENUE, date(2026, 9, 8), date(2026, 9, 9))

    assert changes.listed == ("ANTEUR",)
    assert changes.delisted == ()


def test_the_index_lists_every_day_recorded(tmp_path: Path) -> None:
    import json

    store = MembershipStore(tmp_path)
    take(store, TUESDAY, ALL_PAIRS)
    take(store, WEDNESDAY, ALL_PAIRS)

    count = store.write_index(VENUE, tmp_path / "index.json")

    assert count == 2
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["first"] == "2026-09-08"
    assert index["last"] == "2026-09-09"


def test_the_unremediable_survivorship_window_is_stated_in_words() -> None:
    """R6 requires this said wherever results covering that window are reported."""
    note = universe_snapshot.SURVIVORSHIP_WINDOW_NOTE

    assert "2026-04-01" in note
    assert "no available remedy" in note
    assert "snapshot-universe" in note


def test_the_operator_line_says_which_case_it_took(tmp_path: Path) -> None:
    store = MembershipStore(tmp_path)

    first = take(store, TUESDAY, ALL_PAIRS)
    second = take(store, TUESDAY_LATER, ALL_PAIRS)

    assert "3 pairs listed" in first.describe()
    assert "already recorded, nothing written" in second.describe()

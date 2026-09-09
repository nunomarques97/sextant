"""``sextant snapshot-universe``: recording today's membership, for good.

The archive we can obtain stops at Q1 2026. Today is later than that, and the
gap between the two has no delisting record and no available remedy: a pair that
listed and died inside it left nothing behind that any endpoint will still
serve. That window is survivorship-biased, permanently, and it must be named
wherever a result covers it.

This command is what stops the same hole appearing again. It asks the venue what
it lists today, both unfiltered and filtered by the account's country, and
appends a dated record. Two such records bracket every listing and delisting
between them, at exactly the grade of evidence the archive calendar uses.

It is idempotent per UTC day. The record is of what the venue listed, not of how
often somebody ran a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sextant.adapters.exchanges.kraken.capabilities import VENUE
from sextant.adapters.exchanges.kraken.client import KrakenClient, PairMetadata
from sextant.adapters.storage.membership import (
    MembershipRow,
    MembershipStore,
    UniverseSnapshot,
)
from sextant.domain.capability import Capability
from sextant.domain.venue import Venue
from sextant.ports.clock import Clock

#: The account's country of residence, as the venue's own filter spells it.
JURISDICTION_COUNTRY = "PT"

INDEX_NAME = "membership_index.json"

#: Stated wherever a result covers it, per R6. Not a caveat that can be dropped
#: once the tables look tidy: it is a property of the data that no later work
#: can remove.
SURVIVORSHIP_WINDOW_NOTE = (
    "The venue's quarterly archive ends at the close of Q1 2026. Membership "
    "between 2026-04-01 and the first snapshot recorded by `sextant "
    "snapshot-universe` rests on no source at all: pairs that listed and "
    "delisted inside that window are absent from the archive and absent from "
    "AssetPairs, and no endpoint will still serve them. Results covering that "
    "window are survivorship-biased with no available remedy."
)


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """What one snapshot run did, including doing nothing."""

    venue: Venue
    observed_on: str
    listed: int
    jurisdiction_visible: int
    written: bool

    def describe(self) -> str:
        """One line for the operator."""
        if not self.written:
            return (
                f"[snapshot] {self.venue.name} {self.observed_on}: already recorded, "
                "nothing written"
            )
        return (
            f"[snapshot] {self.venue.name} {self.observed_on}: {self.listed} pairs listed, "
            f"{self.jurisdiction_visible} visible to {JURISDICTION_COUNTRY}"
        )


def snapshot_universe(
    store: MembershipStore,
    clock: Clock,
    *,
    client: KrakenClient | None = None,
    restricted_client: KrakenClient | None = None,
    overwrite: bool = False,
) -> SnapshotResult:
    """Fetch today's membership, unfiltered and jurisdiction-filtered, and record it.

    Both calls are made even when the day is already recorded, so that a re-run
    still verifies the venue is answering. What it will not do is overwrite the
    day: a second observation is a different fact from the first, and silently
    replacing one with the other would hide a membership change rather than
    record it.
    """
    unrestricted = client or _client()
    restricted = restricted_client or _client(country_code=JURISDICTION_COUNTRY)

    listed = unrestricted.pair_metadata()
    visible = set(restricted.pair_metadata())
    observed_at = clock.now()

    snapshot = UniverseSnapshot.of(
        VENUE,
        observed_at,
        (_row(item, item.symbol in visible) for item in listed.values()),
    )
    written = store.append(snapshot, overwrite=overwrite)
    return SnapshotResult(
        venue=VENUE,
        observed_on=snapshot.observed_on.isoformat(),
        listed=len(snapshot.rows),
        jurisdiction_visible=len(snapshot.jurisdiction_visible),
        written=written >= 0,
    )


def run(store_root: Path, clock: Clock, *, overwrite: bool = False) -> SnapshotResult:
    """Record one day and refresh the index. The CLI entrypoint's whole body."""
    store = MembershipStore(store_root)
    result = snapshot_universe(store, clock, overwrite=overwrite)
    recorded = store.write_index(VENUE, store_root / INDEX_NAME)
    print(result.describe())
    print(f"[snapshot] {recorded} day(s) recorded so far in {store_root}")
    print(f"[snapshot] {SURVIVORSHIP_WINDOW_NOTE}")
    return result


def _row(item: PairMetadata, visible: bool) -> MembershipRow:
    return MembershipRow(
        symbol=item.symbol,
        base=item.base,
        quote=item.quote,
        status=item.status,
        jurisdiction_visible=visible,
    )


def _client(country_code: str | None = None) -> KrakenClient:
    """A public read-only client. No credential is attached to any request."""
    public = frozenset({Capability.PUBLIC_MARKET_DATA, Capability.HISTORICAL_OHLCV})
    return KrakenClient(
        account_permitted=public,
        jurisdiction_eligible=public,
        country_code=country_code,
    )

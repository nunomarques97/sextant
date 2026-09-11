"""The venue's own instrument metadata for the USD-margined perpetual venue.

Rule T1, amendment 13. One question: **what is the price increment**, and the answer
comes from the venue rather than from arithmetic on the prices it published.

Why this module exists at all
-----------------------------

Rule B1 cut the liquidity bands on relative tick size, and the tick it correlated
against was derived as the greatest common divisor of the stored high, low and close.
That derivation can only **overestimate**: every published price is a whole number of
ticks, so their divisor is a *multiple* of the increment and equals it only when the
sample happens to use every one. On 5 of 11 sampled symbols it produced a tick wider
than that symbol's own measured quoted spread, which is impossible.

The correction is not a longer window. It is to read the quantity the venue publishes.

What this is, and what it is not
--------------------------------

**It is an assumption with a point-in-time limitation, not a measurement.** The endpoint
serves the venue's *current* instrument metadata and no history of it. A tick read here
is today's tick applied to a historical window. Tick changes are rare but real, and a
symbol whose increment moved inside the window carries the wrong figure for part of it.
Invariant 12 requires that label to travel with the number, so :class:`TickSnapshot`
carries it and the file it writes repeats it.

**Nothing downstream reads the endpoint.** The response is snapshotted with its own
SHA-256 and committed; every later computation reads the snapshot. A result that can
only be reproduced by asking a live service again is not reproducible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sextant.adapters.exchanges.http import (
    HttpTransport,
    JsonValue,
    MalformedVenuePayload,
    RateLimiter,
    RequestJournal,
    as_mapping,
    as_sequence,
    as_str,
    field_of,
)
from sextant.domain.venue import Venue

#: The venue's public instrument endpoint for the USD-margined futures venue. The same
#: venue the ``bookTicker`` archive the spread was measured from belongs to.
FUTURES_API_BASE = "https://fapi.binance.com"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
EXCHANGE_INFO_URL = f"{FUTURES_API_BASE}{EXCHANGE_INFO_PATH}"

#: Where the increment lives inside one symbol's filter list.
PRICE_FILTER = "PRICE_FILTER"
TICK_SIZE_FIELD = "tickSize"

#: What the snapshot is, under invariant 12, repeated into the file it writes.
TICK_LEVEL = "registered-assumption"

NEWLINE = chr(10)


class TickMetadataUnavailable(MalformedVenuePayload):
    """The venue answered, and its answer does not carry a usable increment.

    A distinct type rather than an empty mapping, because invariant 10 forbids the
    collapse: a symbol the venue does not list and a symbol whose filter could not be
    parsed are different facts and neither one is "the tick is zero".
    """


@dataclass(frozen=True, slots=True)
class SymbolTick:
    """One instrument's published price increment, exactly as the venue stated it."""

    symbol: str
    tick_size: Decimal
    status: str
    contract_type: str

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise TickMetadataUnavailable(
                f"{self.symbol}: the venue published a tick size of {self.tick_size}, "
                "which cannot be an increment."
            )


@dataclass(frozen=True, slots=True)
class TickSnapshot:
    """Every increment the venue published, at the instant it was asked.

    ``digest`` is the SHA-256 of the canonical bytes of the raw response, so a reader
    can establish that the snapshot on disk is the one that was fetched without holding
    the whole response.
    """

    fetched_at: str
    digest: str
    symbols: Mapping[str, SymbolTick]

    def tick(self, symbol: str) -> Decimal | None:
        """One symbol's increment, or nothing when the venue does not list it."""
        entry = self.symbols.get(symbol)
        return None if entry is None else entry.tick_size

    def as_json(self) -> dict[str, object]:
        return {
            "rule": "T1",
            "source": EXCHANGE_INFO_URL,
            "field": f"{PRICE_FILTER}.{TICK_SIZE_FIELD}",
            "fetched_at": self.fetched_at,
            "response_sha256": self.digest,
            "what_it_is": TICK_LEVEL,
            "point_in_time_limitation": (
                "This is TODAY'S tick applied to a historical window. The endpoint serves "
                "the venue's current instrument metadata and publishes no history of it. "
                "Tick changes are rare but real, and a symbol whose increment moved inside "
                "the evaluation window carries the wrong figure for part of it. Registered "
                "as an ASSUMPTION with that label under invariant 12, never as a "
                "measurement."
            ),
            "why_not_the_divisor": (
                "Every published price is a whole number of ticks, so the greatest common "
                "divisor of a sample of them is a MULTIPLE of the increment and equals it "
                "only when the sample happens to use every one. It can only overestimate, "
                "and on 5 of 11 sampled symbols it produced a tick wider than that "
                "symbol's own measured quoted spread, which is impossible. The endpoint "
                "publishes the quantity itself, so the derivation is removed rather than "
                "improved."
            ),
            "symbols": len(self.symbols),
            "tick_size_by_symbol": {
                symbol: str(entry.tick_size) for symbol, entry in sorted(self.symbols.items())
            },
            "status_by_symbol": {
                symbol: entry.status for symbol, entry in sorted(self.symbols.items())
            },
            "contract_type_by_symbol": {
                symbol: entry.contract_type for symbol, entry in sorted(self.symbols.items())
            },
        }


def parse(payload: JsonValue) -> Mapping[str, SymbolTick]:
    """Every symbol's increment out of one ``exchangeInfo`` response.

    A symbol whose ``PRICE_FILTER`` is missing is **skipped and not defaulted**: the
    venue occasionally lists an instrument with an incomplete filter set, and inventing
    an increment for it would put a fabricated number on the same footing as the ones
    the venue actually published.
    """
    body = as_mapping(payload, context="exchangeInfo")
    rows = as_sequence(field_of(body, "symbols", context="exchangeInfo"), context="symbols")
    out: dict[str, SymbolTick] = {}
    for row in rows:
        entry = as_mapping(row, context="exchangeInfo.symbols[]")
        symbol = as_str(field_of(entry, "symbol", context="symbol"), context="symbol")
        increment = _tick_of(entry, symbol)
        if increment is None:
            continue
        out[symbol] = SymbolTick(
            symbol=symbol,
            tick_size=increment,
            status=_optional(entry, "status"),
            contract_type=_optional(entry, "contractType"),
        )
    if not out:
        raise TickMetadataUnavailable(
            "the venue's instrument endpoint carried no symbol with a usable price "
            "increment, so rule T1 has nothing to read and nothing is substituted."
        )
    return out


def _tick_of(entry: Mapping[str, JsonValue], symbol: str) -> Decimal | None:
    """One symbol's increment, or nothing when its filter list does not carry one."""
    raw = entry.get("filters")
    if raw is None:
        return None
    for item in as_sequence(raw, context=f"{symbol}.filters"):
        parsed = as_mapping(item, context=f"{symbol}.filters[]")
        if parsed.get("filterType") != PRICE_FILTER:
            continue
        value = parsed.get(TICK_SIZE_FIELD)
        if value is None:
            return None
        return Decimal(as_str(value, context=f"{symbol}.{PRICE_FILTER}.{TICK_SIZE_FIELD}"))
    return None


def _optional(entry: Mapping[str, JsonValue], key: str) -> str:
    """A descriptive field the venue may or may not carry, as text."""
    value = entry.get(key)
    return "" if value is None else str(value)


def fetch(*, transport: HttpTransport | None = None, timeout: float = 30.0) -> TickSnapshot:
    """Ask the venue once, and hash exactly what it answered.

    The digest is taken over the **raw bytes**, before any parsing, because a digest of
    a parsed structure attests to the parser rather than to the response.
    """
    owned = transport is None
    client = transport or HttpTransport(
        venue=Venue("binance"),
        base_url=FUTURES_API_BASE,
        limiter=RateLimiter(rate_per_second=4.0, burst=4.0),
        journal=RequestJournal(),
        timeout_seconds=timeout,
    )
    try:
        body = client.get_bytes(EXCHANGE_INFO_PATH)
    finally:
        if owned:
            client.close()
    return TickSnapshot(
        fetched_at=datetime.now(UTC).isoformat(),
        digest=hashlib.sha256(body).hexdigest(),
        symbols=parse(json.loads(body.decode("utf-8"))),
    )


def write(snapshot: TickSnapshot, destination: Path) -> Path:
    """Write the snapshot. The path is a parameter with no default pointing anywhere real."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline=NEWLINE) as handle:
        json.dump(snapshot.as_json(), handle, indent=2, sort_keys=True)
        handle.write(NEWLINE)
    return destination


def read(source: Path) -> TickSnapshot:
    """Read a committed snapshot back, which is the only path anything downstream takes."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TickMetadataUnavailable(f"{source} does not hold a mapping.")
    ticks = payload.get("tick_size_by_symbol")
    statuses = payload.get("status_by_symbol")
    contracts = payload.get("contract_type_by_symbol")
    if not isinstance(ticks, dict):
        raise TickMetadataUnavailable(f"{source} carries no tick_size_by_symbol.")
    return TickSnapshot(
        fetched_at=str(payload.get("fetched_at", "")),
        digest=str(payload.get("response_sha256", "")),
        symbols={
            str(symbol): SymbolTick(
                symbol=str(symbol),
                tick_size=Decimal(str(value)),
                status=_from(statuses, symbol),
                contract_type=_from(contracts, symbol),
            )
            for symbol, value in ticks.items()
        },
    )


def _from(block: object, symbol: object) -> str:
    """One descriptive field out of a snapshot that may predate it."""
    if not isinstance(block, dict):
        return ""
    return str(block.get(symbol, ""))


def measured_symbols(snapshot: TickSnapshot, wanted: Sequence[str]) -> tuple[str, ...]:
    """Which of the wanted symbols the snapshot actually carries, named rather than counted."""
    return tuple(symbol for symbol in wanted if symbol in snapshot.symbols)


__all__ = [
    "EXCHANGE_INFO_URL",
    "FUTURES_API_BASE",
    "PRICE_FILTER",
    "TICK_LEVEL",
    "TICK_SIZE_FIELD",
    "SymbolTick",
    "TickMetadataUnavailable",
    "TickSnapshot",
    "fetch",
    "measured_symbols",
    "parse",
    "read",
    "write",
]

"""Kraken ExchangeClient: the public, read-only market-data path.

Only the three public port methods are wired: ``health``, ``instruments`` and
``get_bars``. Nothing here signs a request, reads a credential or places an
order.

Three measured facts about this venue shape the design, all recorded in
``docs/DATA-AVAILABILITY.md``:

* ``AssetPairs`` returns *currently listed* pairs only. A delisted pair is gone
  from it entirely, and ``OHLC`` answers ``EQuery:Invalid asset pair`` for one.
  The venue therefore supplies no evidence at all about pairs it has removed;
* ``OHLC`` returns roughly 720 candles per interval whatever ``since`` asks for,
  so the daily series reaches back about two years and no further;
* errors arrive inside a ``200 OK`` body, in an ``error`` array. Treating that
  body as a successful empty result would be exactly the silent-empty failure
  this project refuses to allow, so the envelope is classified explicitly.

Consequently ``instruments(at)`` for a past date is not answerable from this
venue's public API, and this module raises rather than answering with today's
survivors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.http import (
    HttpTransport,
    JsonValue,
    MalformedVenuePayload,
    RateLimiter,
    RequestJournal,
    VenueRequestRejected,
    as_int,
    as_mapping,
    as_sequence,
    as_str,
    field_of,
    utc_now,
)
from sextant.adapters.exchanges.kraken.capabilities import VENUE, VENUE_CAPABILITIES
from sextant.adapters.exchanges.listing_calendar import ListingCalendar
from sextant.domain.availability import VenueHealth, VenueStatus, VenueUnavailable
from sextant.domain.capability import Capability
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import PointInTimeUnavailable
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

API_BASE = "https://api.kraken.com"
"""Public REST host. No credential is ever attached to a request to it."""

BASIS_POINTS = Decimal(10_000)
"""One basis point is one ten-thousandth. Spreads are reported in them."""

OHLC_MAX_CANDLES = 720
"""Documented ceiling on candles per OHLC call. Measured at 721 for daily."""

_INTERVAL_MINUTES: Mapping[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}

_TRANSIENT_ERROR_PREFIXES: tuple[str, ...] = (
    "EAPI:Rate limit exceeded",
    "EGeneral:Temporary lockout",
    "EService:Unavailable",
    "EService:Busy",
    "EGeneral:Internal error",
)

_ONLINE_STATUSES: frozenset[str] = frozenset({"online"})
_DEGRADED_STATUSES: frozenset[str] = frozenset({"cancel_only", "post_only", "limit_only"})


@dataclass(frozen=True, slots=True)
class PairMetadata:
    """One pair's current trading constraints, exactly as the venue states them."""

    symbol: str
    canonical: str
    base: str
    quote: str
    status: str
    tick_size: Price
    lot_size: Quantity
    min_notional: Notional

    @property
    def is_trading(self) -> bool:
        """Whether the venue currently accepts new orders on this pair."""
        return self.status in _ONLINE_STATUSES


def default_transport(journal: RequestJournal | None = None) -> HttpTransport:
    """A transport limited to one public call per second.

    The venue's public counter decays slowly and punishes bursts with a
    temporary lockout, which would look like an outage and poison a long fetch.
    One per second is the documented safe rate for an unauthenticated caller.
    """
    return HttpTransport(
        venue=VENUE,
        base_url=API_BASE,
        limiter=RateLimiter(rate_per_second=1.0, burst=1.0),
        journal=journal or RequestJournal(),
    )


class KrakenClient(BaseExchangeClient):
    """Kraken, as an ExchangeClient. Public market data only."""

    VENUE: Venue = VENUE
    VENUE_CAPABILITIES: frozenset[Capability] = VENUE_CAPABILITIES

    def __init__(
        self,
        account_permitted: frozenset[Capability],
        jurisdiction_eligible: frozenset[Capability],
        *,
        transport: HttpTransport | None = None,
        calendar: ListingCalendar | None = None,
        country_code: str | None = None,
    ) -> None:
        super().__init__(account_permitted, jurisdiction_eligible)
        self._transport = transport or default_transport()
        self._calendar = calendar
        self._country_code = country_code
        self._metadata: Mapping[str, PairMetadata] | None = None

    # -- availability --------------------------------------------------------

    def health(self) -> VenueHealth:
        """Probe the venue's published system status. Never raises."""
        try:
            payload = self._result(
                self._transport.get_json("/0/public/SystemStatus"),
                context="SystemStatus",
            )
        except (VenueUnavailable, VenueRequestRejected) as exc:
            return VenueHealth(
                venue=self.VENUE,
                status=VenueStatus.UNAVAILABLE,
                observed_at=utc_now(),
                detail=str(exc),
            )
        body = as_mapping(payload, context="SystemStatus")
        reported = as_str(field_of(body, "status", context="SystemStatus"), context="status")
        if reported in _ONLINE_STATUSES:
            status = VenueStatus.OPERATIONAL
        elif reported in _DEGRADED_STATUSES:
            status = VenueStatus.DEGRADED
        else:
            status = VenueStatus.UNAVAILABLE
        return VenueHealth(
            venue=self.VENUE,
            status=status,
            observed_at=utc_now(),
            detail=f"status={reported}",
        )

    # -- instrument metadata -------------------------------------------------

    def pair_metadata(self, *, refresh: bool = False) -> Mapping[str, PairMetadata]:
        """Every pair the venue currently lists, keyed by its short name.

        When the client was constructed with a ``country_code``, the venue
        applies its own jurisdictional filter and returns only what that country
        may trade. That is the jurisdiction layer sourced from the venue itself
        rather than from our configuration, and the two are worth comparing.
        """
        if self._metadata is not None and not refresh:
            return self._metadata
        params: dict[str, str] = {}
        if self._country_code is not None:
            params["country_code"] = self._country_code
        payload = self._result(
            self._transport.get_json("/0/public/AssetPairs", params),
            context="AssetPairs",
        )
        body = as_mapping(payload, context="AssetPairs")
        metadata: dict[str, PairMetadata] = {}
        for canonical, raw in body.items():
            parsed = _parse_pair(canonical, raw)
            if parsed is not None:
                metadata[parsed.symbol] = parsed
        self._metadata = metadata
        return metadata

    def instruments(self, at: Timestamp) -> Sequence[Instrument]:
        """Instruments this venue listed as of ``at``.

        Requires a listing calendar, and the calendar cannot be built from this
        venue's public API alone: ``AssetPairs`` forgets delisted pairs and
        publishes no listing dates. Rather than return today's survivors dressed
        as history, this raises and names what would resolve it.
        """
        if self._calendar is None:
            raise PointInTimeUnavailable(
                venue=self.VENUE,
                at=at,
                missing=(
                    "AssetPairs returns only currently listed pairs and carries no listing "
                    "or delisting date, so the set of pairs trading at a past instant "
                    "cannot be derived from the public API"
                ),
                remedy=(
                    "supply a ListingCalendar seeded from the venue's published delisting "
                    "announcements and its downloadable historical OHLCVT dataset"
                ),
            )
        metadata = self.pair_metadata()
        return tuple(
            _to_instrument(metadata[symbol], self._calendar, at)
            for symbol in sorted(self._calendar.symbols_at(at))
            if symbol in metadata
        )

    # -- bars ----------------------------------------------------------------

    def get_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        include_open: bool = False,
    ) -> Sequence[Bar]:
        """Bars for an explicit collection of instruments over ``[start, end)``."""
        collected: list[Bar] = []
        for instrument in instruments:
            collected.extend(
                self.bars_for(instrument, timeframe, start, end, include_open=include_open)
            )
        return tuple(collected)

    def bars_for(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: Timestamp,
        end: Timestamp,
        *,
        include_open: bool = False,
    ) -> Sequence[Bar]:
        """Bars for one instrument.

        The venue caps the response at roughly 720 candles and offers no way to
        page further back, so a window longer than that returns the recent end
        of it. The shortfall is visible to the caller as a first bar later than
        ``start``; it is never padded, and never silently reported as complete.
        """
        payload = self._result(
            self._transport.get_json(
                "/0/public/OHLC",
                {
                    "pair": instrument.symbol,
                    "interval": str(_INTERVAL_MINUTES[timeframe]),
                    "since": str(start.epoch_millis // 1000),
                },
            ),
            context=f"OHLC {instrument.symbol}",
        )
        observed_at = utc_now()
        body = as_mapping(payload, context=f"OHLC {instrument.symbol}")
        rows: Sequence[JsonValue] = ()
        for key, value in body.items():
            if key != "last":
                rows = as_sequence(value, context=f"OHLC {instrument.symbol}")
                break
        bars: list[Bar] = []
        for row in rows:
            bar = _parse_candle(row, instrument, timeframe, observed_at)
            if not start <= bar.open_time < end:
                continue
            if include_open or bar.is_closed:
                bars.append(bar)
        return tuple(bars)

    def top_of_book_spreads_bps(self, symbols: Sequence[str]) -> Mapping[str, Decimal]:
        """Current best-bid/ask spread in basis points, for the named pairs.

        A snapshot of one instant, batched because the venue accepts a comma
        separated pair list. The venue's ``Spread`` endpoint returns only recent
        quotes and offers no ``since`` reaching further back, so this is the
        only spread measurement available and it dates from today alone.
        """
        spreads: dict[str, Decimal] = {}
        canonical_to_symbol = {
            item.canonical: item.symbol for item in self.pair_metadata().values()
        }
        batch_size = 100
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset : offset + batch_size]
            payload = self._result(
                self._transport.get_json("/0/public/Ticker", {"pair": ",".join(batch)}),
                context="Ticker",
            )
            body = as_mapping(payload, context="Ticker")
            for canonical, raw in body.items():
                entry = as_mapping(raw, context=f"Ticker.{canonical}")
                bid = _first_level(entry, "b", canonical)
                ask = _first_level(entry, "a", canonical)
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                mid = (bid + ask) / Decimal(2)
                spreads[canonical_to_symbol.get(canonical, canonical)] = (
                    (ask - bid) / mid * BASIS_POINTS
                )
        return spreads

    # -- error envelope ------------------------------------------------------

    def _result(self, payload: JsonValue, *, context: str) -> JsonValue:
        """Classify the venue's ``{"error": [...], "result": {...}}`` envelope.

        A transient error becomes ``VenueUnavailable`` and a refusal becomes
        ``VenueRequestRejected``. Neither becomes an empty result, which is the
        only outcome that would be genuinely dangerous here.
        """
        body = as_mapping(payload, context=context)
        errors = [
            as_str(item, context=f"{context}.error")
            for item in as_sequence(field_of(body, "error", context=context), context=context)
        ]
        if errors:
            joined = "; ".join(errors)
            if any(error.startswith(_TRANSIENT_ERROR_PREFIXES) for error in errors):
                raise VenueUnavailable(self.VENUE, VenueStatus.UNAVAILABLE, joined)
            raise VenueRequestRejected(self.VENUE, context, 200, joined)
        if "result" not in body:
            raise MalformedVenuePayload(f"{context}: envelope carried neither error nor result")
        return body["result"]


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------


def _first_level(entry: Mapping[str, JsonValue], key: str, canonical: str) -> Decimal:
    """Read the price out of a Ticker best-bid or best-ask array."""
    level = as_sequence(field_of(entry, key, context=canonical), context=f"{canonical}.{key}")
    return Decimal(as_str(level[0], context=f"{canonical}.{key}[0]"))


def _parse_pair(canonical: str, row: JsonValue) -> PairMetadata | None:
    """Turn one ``AssetPairs`` entry into typed metadata.

    Entries without a ``wsname`` are skipped: that field is the only place the
    venue states the pair in unambiguous ``BASE/QUOTE`` form, and guessing the
    split out of the concatenated name is how ``XBT`` and ``XXBT`` become two
    different assets.
    """
    entry = as_mapping(row, context=f"AssetPairs.{canonical}")
    if "wsname" not in entry:
        return None
    wsname = as_str(entry["wsname"], context=f"{canonical}.wsname")
    if wsname.count("/") != 1:
        return None
    base, quote = wsname.split("/")
    lot_decimals = as_int(
        field_of(entry, "lot_decimals", context=canonical), context="lot_decimals"
    )
    return PairMetadata(
        symbol=as_str(field_of(entry, "altname", context=canonical), context="altname"),
        canonical=canonical,
        base=base,
        quote=quote,
        status=as_str(field_of(entry, "status", context=canonical), context="status"),
        tick_size=Price(
            Decimal(as_str(field_of(entry, "tick_size", context=canonical), context="tick_size"))
        ),
        lot_size=Quantity(Decimal(1).scaleb(-lot_decimals)),
        min_notional=Notional(
            Decimal(as_str(field_of(entry, "costmin", context=canonical), context="costmin"))
        ),
    )


def _parse_candle(
    row: JsonValue,
    instrument: Instrument,
    timeframe: Timeframe,
    observed_at: Timestamp,
) -> Bar:
    """Turn one OHLC array into a Bar.

    Quote turnover is ``vwap * volume``. That is not an approximation: the
    venue's vwap is defined as quote turnover divided by base volume over the
    same interval, so the product recovers the turnover exactly.
    """
    values = as_sequence(row, context=f"OHLC {instrument.symbol}")
    if len(values) < 8:
        raise MalformedVenuePayload(
            f"OHLC {instrument.symbol}: expected at least 8 fields, got {len(values)}"
        )
    context = f"OHLC {instrument.symbol}"
    open_time = Timestamp.from_epoch_millis(as_int(values[0], context=context) * 1000)
    vwap = Decimal(as_str(values[5], context=context))
    volume = Decimal(as_str(values[6], context=context))
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        open_time=open_time,
        open=Price(Decimal(as_str(values[1], context=context))),
        high=Price(Decimal(as_str(values[2], context=context))),
        low=Price(Decimal(as_str(values[3], context=context))),
        close=Price(Decimal(as_str(values[4], context=context))),
        volume=Quantity(volume),
        is_closed=open_time.plus(timeframe.duration) <= observed_at,
        quote_volume=Notional(vwap * volume),
    )


def _to_instrument(
    metadata: PairMetadata,
    calendar: ListingCalendar,
    at: Timestamp,
) -> Instrument:
    """Combine current venue constraints with a sourced listing window."""
    entry = calendar.entry_for(metadata.symbol)
    if entry is None:
        raise PointInTimeUnavailable(
            venue=VENUE,
            at=at,
            missing=f"no calendar entry for {metadata.symbol}",
            remedy="rebuild the listing calendar so it covers this pair",
        )
    return Instrument(
        venue=VENUE,
        symbol=metadata.symbol,
        base=metadata.base,
        quote=metadata.quote,
        listed_at=entry.window.listed_at,
        tick_size=metadata.tick_size,
        lot_size=metadata.lot_size,
        min_notional=metadata.min_notional,
        delisted_at=entry.window.delisted_at,
        provenance=entry.window.provenance,
    )

"""Binance ExchangeClient: the public, read-only market-data path.

Only three port methods are wired here, and all three are public: ``health``,
``instruments`` and ``get_bars``. Nothing in this module signs a request, reads
a credential or places an order.

Two facts about this venue shape the design, and both were measured rather than
assumed (see ``docs/DATA-AVAILABILITY.md``):

* ``/api/v3/exchangeInfo`` retains symbols that have stopped trading, marked
  ``BREAK``. The venue itself therefore tells us which pairs are dead - that is
  directly verified metadata, not an inference;
* ``/api/v3/klines`` continues to serve the full history of those dead symbols.

What the venue does *not* publish is a listing date or a delisting date for a
spot symbol. Those have to be reconstructed, and this module will not invent
them: ``instruments(at)`` requires a :class:`ListingCalendar` and raises
``PointInTimeUnavailable`` without one, rather than quietly answering a
historical question with today's survivors.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sextant.adapters.exchanges.base import BaseExchangeClient
from sextant.adapters.exchanges.binance.capabilities import VENUE, VENUE_CAPABILITIES
from sextant.adapters.exchanges.http import (
    HttpTransport,
    JsonValue,
    MalformedVenuePayload,
    RateLimiter,
    RequestJournal,
    as_int,
    as_mapping,
    as_sequence,
    as_str,
    field_of,
    utc_now,
)
from sextant.adapters.exchanges.listing_calendar import ListingCalendar
from sextant.domain.availability import VenueHealth, VenueStatus, VenueUnavailable
from sextant.domain.capability import Capability
from sextant.domain.instrument import Instrument
from sextant.domain.market_data import Bar
from sextant.domain.money import Notional, Price, Quantity
from sextant.domain.provenance import PointInTimeUnavailable
from sextant.domain.time import Timeframe, Timestamp
from sextant.domain.venue import Venue

API_BASE = "https://api.binance.com"
"""Public REST host. No credential is ever attached to a request to it."""

ARCHIVE_BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
"""The venue's official public data archive, addressed as a plain S3 bucket."""

KLINE_PAGE_LIMIT = 1000
"""Maximum bars per klines call, per the venue's published documentation."""

TRADING_STATUS = "TRADING"
"""Symbol status meaning the pair is currently live."""

HALTED_STATUS = "BREAK"
"""Symbol status the venue uses for pairs that have stopped trading."""

_INTERVALS: Mapping[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}

_COMMON_PREFIX = re.compile(r"<Prefix>(.*?)</Prefix>")
_IS_TRUNCATED = re.compile(r"<IsTruncated>true</IsTruncated>")
_NEXT_MARKER = re.compile(r"<NextMarker>(.*?)</NextMarker>")


@dataclass(frozen=True, slots=True)
class SymbolMetadata:
    """One symbol's current trading constraints, exactly as the venue states them.

    These are *current* values. The venue publishes no history of them, so
    applying them to a past date is itself a reconstruction, and the report says
    so rather than pretending otherwise.
    """

    symbol: str
    base: str
    quote: str
    status: str
    tick_size: Price
    lot_size: Quantity
    min_notional: Notional

    @property
    def is_trading(self) -> bool:
        """Whether the venue currently accepts orders on this symbol."""
        return self.status == TRADING_STATUS


def default_transport(journal: RequestJournal | None = None) -> HttpTransport:
    """A transport for the REST host, limited well inside the published budget.

    The documented allowance is 6,000 request weight per minute and a klines
    call costs 2. Ten requests per second is a fifth of that, which leaves room
    for the venue to be having a bad day without us making it worse.
    """
    return HttpTransport(
        venue=VENUE,
        base_url=API_BASE,
        limiter=RateLimiter(rate_per_second=10.0, burst=10.0),
        journal=journal or RequestJournal(),
    )


def default_archive_transport(journal: RequestJournal | None = None) -> HttpTransport:
    """A transport for the public data archive. A different host, so its own budget."""
    return HttpTransport(
        venue=VENUE,
        base_url=ARCHIVE_BASE,
        limiter=RateLimiter(rate_per_second=8.0, burst=8.0),
        journal=journal or RequestJournal(),
    )


class BinanceClient(BaseExchangeClient):
    """Binance, as an ExchangeClient. Public market data only."""

    VENUE: Venue = VENUE
    VENUE_CAPABILITIES: frozenset[Capability] = VENUE_CAPABILITIES

    def __init__(
        self,
        account_permitted: frozenset[Capability],
        jurisdiction_eligible: frozenset[Capability],
        *,
        transport: HttpTransport | None = None,
        archive_transport: HttpTransport | None = None,
        calendar: ListingCalendar | None = None,
    ) -> None:
        super().__init__(account_permitted, jurisdiction_eligible)
        self._transport = transport or default_transport()
        self._archive = archive_transport
        self._calendar = calendar
        self._metadata: Mapping[str, SymbolMetadata] | None = None

    # -- availability --------------------------------------------------------

    def health(self) -> VenueHealth:
        """Probe the venue. Never raises: an outage is a value, not an exception.

        This is the one place that swallows ``VenueUnavailable``, because
        reporting unavailability *is* the method's job. Everywhere else the
        exception must propagate.
        """
        try:
            payload = self._transport.get_json("/api/v3/time")
        except VenueUnavailable as exc:
            return VenueHealth(
                venue=self.VENUE,
                status=VenueStatus.UNAVAILABLE,
                observed_at=utc_now(),
                detail=str(exc),
            )
        server_time = as_int(
            field_of(as_mapping(payload, context="serverTime"), "serverTime", context="serverTime"),
            context="serverTime",
        )
        return VenueHealth(
            venue=self.VENUE,
            status=VenueStatus.OPERATIONAL,
            observed_at=utc_now(),
            detail=f"serverTime={Timestamp.from_epoch_millis(server_time).isoformat()}",
        )

    # -- instrument metadata -------------------------------------------------

    def symbol_metadata(self, *, refresh: bool = False) -> Mapping[str, SymbolMetadata]:
        """Every symbol the venue currently describes, trading or halted.

        Includes ``BREAK`` symbols. That is the point: the venue's own endpoint
        is the strongest available evidence that a pair once existed.
        """
        if self._metadata is not None and not refresh:
            return self._metadata
        payload = as_mapping(
            self._transport.get_json("/api/v3/exchangeInfo", cost=2.0),
            context="exchangeInfo",
        )
        rows = as_sequence(field_of(payload, "symbols", context="exchangeInfo"), context="symbols")
        metadata = {parsed.symbol: parsed for parsed in (_parse_symbol(row) for row in rows)}
        self._metadata = metadata
        return metadata

    def instruments(self, at: Timestamp) -> Sequence[Instrument]:
        """Instruments this venue listed as of ``at``.

        Requires a listing calendar. The venue publishes no spot listing dates,
        so without one there is no honest answer to give and none is invented.
        """
        if self._calendar is None:
            raise PointInTimeUnavailable(
                venue=self.VENUE,
                at=at,
                missing=(
                    "the spot API publishes no listing or delisting date, so the set of "
                    "symbols trading at a past instant cannot be derived from it"
                ),
                remedy=(
                    "supply a ListingCalendar built from the public data archive at "
                    f"{ARCHIVE_BASE} or from first and last observed bars"
                ),
            )
        metadata = self.symbol_metadata()
        listed = self._calendar.symbols_at(at)
        return tuple(
            _to_instrument(metadata[symbol], self._calendar, at)
            for symbol in sorted(listed)
            if symbol in metadata
        )

    def archive_symbols(self, prefix: str = "data/spot/monthly/klines/") -> frozenset[str]:
        """Every symbol directory in the public data archive.

        An independent check on ``exchangeInfo``: two official sources that
        disagree about which symbols ever existed is something we want to see.
        """
        archive = self._archive or default_archive_transport(self._transport.journal)
        symbols: set[str] = set()
        marker = ""
        while True:
            params = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
            if marker:
                params["marker"] = marker
            body = archive.get_text("", params)
            for found in _COMMON_PREFIX.findall(body):
                name = found[len(prefix) :].strip("/")
                if name and found.startswith(prefix):
                    symbols.add(name)
            if not _IS_TRUNCATED.search(body):
                break
            next_marker = _NEXT_MARKER.search(body)
            if next_marker is None:
                break
            marker = next_marker.group(1)
        return frozenset(symbols)

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
        """Bars for an explicit collection of instruments over ``[start, end)``.

        Closed bars only unless ``include_open`` is asked for at the call site.
        A venue failure propagates; an instrument that genuinely has no bars in
        the window contributes nothing and does not fail the batch. Those two
        outcomes are never confused, because the failure raises.
        """
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
        """Bars for one instrument, paging until the window is covered."""
        interval = _INTERVALS[timeframe]
        step_millis = int(timeframe.duration.total_seconds() * 1000)
        cursor = start.epoch_millis
        end_millis = end.epoch_millis
        bars: list[Bar] = []
        while cursor < end_millis:
            payload = self._transport.get_json(
                "/api/v3/klines",
                {
                    "symbol": instrument.symbol,
                    "interval": interval,
                    "startTime": str(cursor),
                    "endTime": str(end_millis - 1),
                    "limit": str(KLINE_PAGE_LIMIT),
                },
                cost=2.0,
            )
            observed_at = utc_now()
            rows = as_sequence(payload, context=f"klines {instrument.symbol}")
            if not rows:
                break
            for row in rows:
                bar = _parse_kline(row, instrument, timeframe, observed_at)
                if bar.open_time.epoch_millis >= end_millis:
                    continue
                if include_open or bar.is_closed:
                    bars.append(bar)
            last_open = as_int(
                as_sequence(rows[-1], context="kline row")[0], context="kline openTime"
            )
            cursor = last_open + step_millis
            if len(rows) < KLINE_PAGE_LIMIT:
                break
        return tuple(bars)

    def earliest_bar_open_time(self, symbol: str, timeframe: Timeframe) -> Timestamp | None:
        """The first bar the venue will serve for ``symbol``, or None if it serves none.

        A lower bound on the listing date, not the listing date. The distinction
        is recorded as ``Provenance.RECONSTRUCTED`` wherever this is used.
        """
        payload = self._transport.get_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": _INTERVALS[timeframe],
                "startTime": "0",
                "limit": "1",
            },
            cost=2.0,
        )
        rows = as_sequence(payload, context=f"klines {symbol}")
        if not rows:
            return None
        first = as_sequence(rows[0], context="kline row")
        return Timestamp.from_epoch_millis(as_int(first[0], context="kline openTime"))


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------


def _parse_symbol(row: JsonValue) -> SymbolMetadata:
    """Turn one ``exchangeInfo`` entry into typed metadata."""
    entry = as_mapping(row, context="exchangeInfo.symbols[]")
    symbol = as_str(field_of(entry, "symbol", context="symbol"), context="symbol")
    filters = as_sequence(field_of(entry, "filters", context=symbol), context=f"{symbol}.filters")
    by_type: dict[str, Mapping[str, JsonValue]] = {}
    for raw in filters:
        parsed = as_mapping(raw, context=f"{symbol}.filters[]")
        by_type[as_str(field_of(parsed, "filterType", context=symbol), context=symbol)] = parsed
    return SymbolMetadata(
        symbol=symbol,
        base=as_str(field_of(entry, "baseAsset", context=symbol), context=symbol),
        quote=as_str(field_of(entry, "quoteAsset", context=symbol), context=symbol),
        status=as_str(field_of(entry, "status", context=symbol), context=symbol),
        tick_size=Price(_decimal_filter(by_type, "PRICE_FILTER", "tickSize", symbol)),
        lot_size=Quantity(_decimal_filter(by_type, "LOT_SIZE", "stepSize", symbol)),
        min_notional=Notional(_min_notional(by_type, symbol)),
    )


def _decimal_filter(
    filters: Mapping[str, Mapping[str, JsonValue]],
    filter_type: str,
    key: str,
    symbol: str,
) -> Decimal:
    """Read one exact decimal out of a named symbol filter."""
    entry = filters.get(filter_type)
    if entry is None:
        raise MalformedVenuePayload(f"{symbol}: missing {filter_type} filter")
    return Decimal(as_str(field_of(entry, key, context=f"{symbol}.{filter_type}"), context=key))


def _min_notional(filters: Mapping[str, Mapping[str, JsonValue]], symbol: str) -> Decimal:
    """The minimum order value, under whichever of the two filter names is present.

    The venue renamed ``MIN_NOTIONAL`` to ``NOTIONAL`` and both still appear
    across symbols. A symbol carrying neither has no stated minimum, which is a
    real answer, so it is reported as zero rather than as a parse failure.
    """
    for filter_type, key in (("NOTIONAL", "minNotional"), ("MIN_NOTIONAL", "minNotional")):
        entry = filters.get(filter_type)
        if entry is not None and key in entry:
            return Decimal(as_str(entry[key], context=f"{symbol}.{filter_type}"))
    return Decimal(0)


def _parse_kline(
    row: JsonValue,
    instrument: Instrument,
    timeframe: Timeframe,
    observed_at: Timestamp,
) -> Bar:
    """Turn one kline array into a Bar, deciding closed-ness against the clock."""
    values = as_sequence(row, context=f"kline {instrument.symbol}")
    if len(values) < 8:
        raise MalformedVenuePayload(
            f"kline {instrument.symbol}: expected at least 8 fields, got {len(values)}"
        )
    context = f"kline {instrument.symbol}"
    open_time = Timestamp.from_epoch_millis(as_int(values[0], context=context))
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        open_time=open_time,
        open=Price(Decimal(as_str(values[1], context=context))),
        high=Price(Decimal(as_str(values[2], context=context))),
        low=Price(Decimal(as_str(values[3], context=context))),
        close=Price(Decimal(as_str(values[4], context=context))),
        volume=Quantity(Decimal(as_str(values[5], context=context))),
        is_closed=open_time.plus(timeframe.duration) <= observed_at,
        quote_volume=Notional(Decimal(as_str(values[7], context=context))),
    )


def _to_instrument(
    metadata: SymbolMetadata,
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
            remedy="rebuild the listing calendar so it covers this symbol",
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

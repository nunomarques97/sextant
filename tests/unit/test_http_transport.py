"""The HTTP transport: the query string survives, and a wrong answer is refused.

Nothing here reaches the network. Both tests exist because of one defect found
during SEXTANT-006, and the defect is worth stating in full because its shape is
the shape invariant 10 exists to forbid.

``HttpTransport.get_bytes`` accepted an optional parameter mapping and passed
``params or {}`` down to httpx. httpx treats an explicit ``params=`` argument as
*the* query and discards whatever the URL already carried, so a caller that built
a complete URL and supplied no parameters had its query string deleted on the
way to the wire. Every archive listing in this project is such a caller.

The consequence was not an error. An S3 listing with no ``prefix`` is a valid
request that answers with the top of the bucket, so
``BinanceDataArchive.symbols()`` returned an empty tuple - the same value it
would return for a tree the archive does not carry. A failed request arriving as
an empty result, indistinguishable from a genuinely empty answer, is the exact
collapse the project refuses.

Two guards, at two layers, because either alone would leave the other class of
this bug live: the transport keeps the query, and the archive reader refuses a
listing that echoes a prefix it did not ask for.
"""

from __future__ import annotations

import httpx
import pytest

from sextant.adapters.exchanges.binance.archive import ArchiveError, BinanceDataArchive
from sextant.adapters.exchanges.http import HttpTransport, RateLimiter, RequestJournal
from sextant.domain.venue import Venue

VENUE = Venue("binance")

_LISTING = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    "<Prefix>{prefix}</Prefix><IsTruncated>false</IsTruncated>"
    "<CommonPrefixes><Prefix>data/spot/monthly/klines/BTCUSDT/</Prefix></CommonPrefixes>"
    "</ListBucketResult>"
)


def _transport(handler: object) -> HttpTransport:
    return HttpTransport(
        venue=VENUE,
        base_url="https://example.invalid",
        limiter=RateLimiter(rate_per_second=1000.0, burst=1000.0),
        journal=RequestJournal(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


def test_a_url_carrying_its_own_query_keeps_it_when_no_parameters_are_given() -> None:
    """The regression. Without the guard the query string reached the wire empty."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    with _transport(handler) as transport:
        transport.get_bytes("https://example.invalid/bucket?prefix=a%2Fb%2F&max-keys=1000")

    assert seen == ["https://example.invalid/bucket?prefix=a%2Fb%2F&max-keys=1000"]


def test_explicit_parameters_still_reach_the_wire() -> None:
    """The fix must not stop a caller that does supply parameters from using them."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    with _transport(handler) as transport:
        transport.get_bytes("https://example.invalid/klines", {"symbol": "BTCUSDT"})

    assert seen == ["https://example.invalid/klines?symbol=BTCUSDT"]


def test_a_listing_that_echoes_a_different_prefix_is_refused() -> None:
    """The second guard. A wrong answer is not an empty one, and must not read as one."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=_LISTING.format(prefix="").encode())

    archive = BinanceDataArchive(transport=_transport(handler))
    with pytest.raises(ArchiveError, match="answered about"):
        archive.symbols()


def test_a_listing_that_echoes_the_prefix_asked_for_is_accepted() -> None:
    """And the guard must not refuse the ordinary case."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, content=_LISTING.format(prefix="data/spot/monthly/klines/").encode()
        )

    archive = BinanceDataArchive(transport=_transport(handler))
    assert archive.symbols() == ("BTCUSDT",)

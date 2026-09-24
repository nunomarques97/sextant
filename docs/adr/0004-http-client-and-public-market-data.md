# ADR 0004 - httpx as the HTTP client, and how public market data is read

**Status:** accepted
**Date:** 2026-09-09
**Scope:** data-availability spike

## Context

The data-availability spike has to answer, by fetching, whether a point-in-time universe
including delisted instruments can be reconstructed on Binance and Kraken.
That requires real requests against public REST endpoints and against Binance's
public S3 data archive. Phase 0 deliberately shipped with no HTTP client at all.

## Decision

Add exactly one runtime dependency: **httpx**.

Rejected alternatives:

| Option | Why not |
|---|---|
| `urllib.request` (stdlib, zero dependencies) | No connection pooling, no timeout defaults worth the name, no typed response object, and its exception surface (`URLError`, `HTTPError`, `socket.timeout`) has to be re-normalised by hand at every call site. The bandit rule `S310` also flags every use, which would mean either suppressions we have banned or a wrapper that is most of httpx anyway. |
| `requests` | Unmaintained-adjacent, no timeout by default, no HTTP/2, no async path. If we later stream websockets or fetch venues concurrently, we replace it. |
| `aiohttp` | Async-only. The engine is synchronous and the `Clock` port exists precisely so backtest, paper and live share one execution model. Introducing async now would fork that model for no measured benefit. |
| `ccxt` | Explicitly out of scope, and it is the opposite of this architecture: it normalises venues behind one interface, which is exactly the venue-specific reasoning we have quarantined into adapters. |

httpx gives one timeout policy, one exception hierarchy, a sync client today and
an async client if we ever need one, without changing the calling code's shape.

## Quarantine

`httpx` is added to the `exchange-sdks-are-quarantined` contract in
`.importlinter`, alongside `ccxt` and the venue SDKs. `domain`, `ports`,
`engine`, `app`, `adapters.storage`, `adapters.llm` and `adapters.clocks` may
not import it, directly or indirectly. Only `adapters.exchanges` may.

## Consequences

**A failed request is never an empty result.** `HttpTransport` returns a parsed
payload or raises `VenueUnavailable`. There is no code path that turns a 429, a
5xx, a timeout or a venue-level error envelope into an empty sequence. This is
not defensive style: a silent empty result on a delisted instrument is how a
survivorship-biased dataset gets built without anybody noticing.

**Every request is journalled.** `RequestJournal` records endpoint, parameters,
request and response timestamps, HTTP status and payload size for every call.
Each claim in `docs/DATA-AVAILABILITY.md` is traceable to a journal line.

**Rate limits are respected by construction.** A token-bucket limiter is
configured per venue from that venue's published limits, and `Retry-After` is
honoured on 429. Backoff is exponential with a bounded number of attempts;
exhausting them raises `VenueUnavailable`, which is retryable by contract.

**No private endpoint.** The transport signs nothing and carries no credential.
`SPOT_TRADING` and `withdrawal_permission()` are untouched by this task.

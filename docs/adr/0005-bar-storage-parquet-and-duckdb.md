# ADR 0005 - parquet and duckdb for the bar store

**Status:** accepted
**Date:** 2026-09-09
**Task:** SEXTANT-003

## Context

SEXTANT-003 ingests Kraken's quarterly OHLCVT archives. Each quarterly ZIP holds
roughly 6,000 CSV files across eight granularities, and thirteen quarters is
about 4.5 GB compressed. The daily timeframe alone is small, but the brief
requires hourly to be ingested at the same time, because re-acquiring the
archive later means downloading 4.5 GB again through a Google Drive quota wall
that has already refused us twice.

The store has to satisfy four things at once:

* **point-in-time queries over a wide, sparse key space.** 762 pairs in one
  quarter, most of them present in only some quarters;
* **exact decimals.** Invariant 4 makes float prices a defect, not a preference;
* **idempotent re-ingestion.** Running the ingest twice must produce the same
  store, asserted by a test, because the archive will be re-read every time a
  quarter arrives by hand;
* **the closed/open flag preserved per bar**, per the bar convention.

CSV cannot do the third or fourth without hand-rolled bookkeeping, and JSON -
what the SEXTANT-002 spike used - costs about 8x the space and parses an order
of magnitude slower. The spike's `data/spike` tree is already 105 MB for two
years of daily bars on two venues. Hourly bars over thirteen quarters are
roughly two orders of magnitude more rows than that.

## Decision

Add exactly two runtime dependencies: **pyarrow** and **duckdb**.

* **pyarrow** writes and reads the parquet files. Bars are stored one file per
  `(venue, symbol, timeframe)`, which is the key the brief specifies and also
  the natural read unit: a universe measurement reads whole series, never
  individual bars;
* **duckdb** queries across those files. Scanning 762 parquet files to answer
  "which pairs had turnover above X in this month" is a query, and writing that
  query by hand in Python is how a report becomes an overnight job.

Prices, quantities and turnover are stored as **strings**, not as parquet
decimals or doubles. This is deliberate and it is the one place this ADR departs
from what the format offers. Parquet's `DECIMAL` carries a fixed precision and
scale, and the archive mixes scales freely - `1E+1` appears verbatim in
`ANTEUR_60.csv`. Round-tripping through `Decimal(str)` is exact for every value
the venue can emit; committing to a precision now would silently truncate a
value we have not seen yet. The cost is storage we can afford and a parse on
read that is still far cheaper than JSON.

Rejected alternatives:

| Option | Why not |
|---|---|
| Keep the spike's JSON tree | 8x the size, and the daily+hourly ingest across thirteen quarters would be tens of gigabytes of JSON. Also no schema: a shape change is discovered by a crash three modules downstream. |
| SQLite (stdlib, zero dependencies) | Would work, and was the closest call. Rejected because it stores one row at a time in a single file, so the natural read unit becomes a query rather than a file, and because the columnar scan that the universe measurement actually performs is the case SQLite is worst at. It also gives no free answer to "read every parquet under this directory as one table". |
| pandas | Far larger dependency surface, and it would arrive with a float64 default that fights invariant 4 on every column. We need storage, not a dataframe library. |
| duckdb alone, no pyarrow | duckdb can write parquet, but then the store's file format is defined by a query engine's dialect rather than by an explicit schema we control. pyarrow makes the schema a declared object that a test can assert on. |
| pyarrow alone, no duckdb | Possible, and the ingest path does not need duckdb. The measurement path does: it is the difference between one SQL statement and a hand-written scan over 6,000 files. |

## Quarantine

Both are added to the `exchange-sdks-are-quarantined` contract in
`.importlinter` and to a new `storage-engines-unreachable-from-the-core`
contract. `domain`, `ports` and `engine` cannot reach either by any path, which
is the acceptance criterion; `adapters.storage` may import both, and `app` may
hold a store without importing the engines itself. This mirrors exactly how
`httpx` is quarantined in ADR 0004, and for the same reason: the engine must
stay runnable with no store, no network and no file system.

## Consequences

**Ingestion is idempotent by construction.** A `(venue, symbol, timeframe)`
series is written whole, to a deterministic path, sorted by open time and
de-duplicated on it. Re-running the ingest over the same archives overwrites
each file with identical bytes. A test asserts the second run changes nothing.

**Absent and empty stay distinguishable.** A pair present in a quarter with zero
rows is stored as a parquet file with zero rows, which is not the same thing as
no file. That distinction is the whole of R3's listed-and-untraded state and it
must survive the storage layer rather than being flattened by it.

**The store is git-ignored.** `*.parquet` is already in `.gitignore`, and
`data/` with it. No bar, no CSV and no ZIP is ever committed.

**Neither dependency reaches a strategy.** When a backtester exists it will read
bars through a port, not through duckdb. The layering contract is what keeps
that true after this ADR stops being read.

# SEXTANT-006 stage 0: what data each strategy family actually has

**Committed before any SEXTANT-006 backtest was run. No strategy result existed when this
document was written, and nothing in it was informed by one.**

This document answers one question per family, and answers it before the family is
pre-registered: *does point-in-time data exist, from an official source, with timestamps
that prove availability before the decision instant?* A family whose data does not exist is
reported here as untestable with the reason. It is not simulated, approximated, or filled
with a proxy.

Everything below was established by listing and reading the sources themselves. Object
counts, byte counts, month spans and universe sizes are measurements taken from the sources
during stage 0, not estimates.

---

## Summary

| | family | data verdict | why |
|---|---|---|---|
| F1 | funding / basis / carry | **testable** | the venue publishes realised funding, perpetual klines and the premium index per symbol per month, back to 2020-01, delisted perpetuals retained |
| F2 | long-short cross-sectional | **testable** | the short leg is a native perpetual, so its cost is published funding rather than an unrecoverable borrow rate |
| F3 | long-short trend following | **testable** | same data as F2 |
| F4 | long-only timing / regime | **testable** | needs nothing new; the SEXTANT-005 spot archive is on disk and unchanged |
| F5 | news / event driven | **testable only in a narrow, exchange-announcement form** | no point-in-time general news archive is accessible; the venue's own announcement archive is official, timestamped and reaches back to 2017, and is the only event source that qualifies |
| F6 | combinations | **testable** | union of the above, no new data |

**Nothing needs the Sponsor.** Every source is public, unauthenticated, and free. No browser
session, no key, no manual download, no spend.

**One acquisition covers F1, F2, F3 and F6:** 67,496 objects, 83.4 MB compressed, from the
same static archive SEXTANT-005 used. A second, much smaller one covers F5.

---

## 1. The source, and why this one

The venue publishes a static, unauthenticated archive at `data.binance.vision`, backed by a
public S3 bucket whose listing endpoint is also public. SEXTANT-005 used its spot half. This
task uses its futures half, which was not touched before.

```
data/futures/um/monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-<YYYY-MM>.zip
data/futures/um/monthly/klines/<SYMBOL>/1d/<SYMBOL>-1d-<YYYY-MM>.zip
data/futures/um/monthly/premiumIndexKlines/<SYMBOL>/1d/<SYMBOL>-1d-<YYYY-MM>.zip
data/futures/um/monthly/markPriceKlines/<SYMBOL>/1d/...
```

The bucket also holds `aggTrades`, `trades`, `bookTicker` and `indexPriceKlines`, and a
coin-margined tree under `data/futures/cm/`. What was taken and what was left is in
section 7.

**Why the archive rather than the REST endpoint, again.** `/fapi/v1/fundingRate` answers only
for symbols the venue lists *today*. 151 of the 865 USDT-quoted perpetuals in this archive
stopped publishing before the archive's final month; a dataset built from the REST endpoint
would delete all 151 from history along with whatever their funding did on the way out. That
is invariant 9, and it is the same reason SEXTANT-005 used the archive for spot.

**The archive agrees with the venue's own live endpoint.** `BTCUSDT-fundingRate-2024-03.zip`
was compared row by row against `/fapi/v1/fundingRate` over the same window: **10 of 10
settlement instants matched to the last decimal, 0 mismatches.** That establishes that
`calc_time` is the settlement instant and `last_funding_rate` is the rate realised at it —
not a forecast, not the following interval's rate. An off-by-one-interval reading of this
file would have shifted every carry result by one settlement, so the check was run before
anything was downloaded in bulk.

**Published checksums.** Unlike the spot tree, every futures object is accompanied by a
`.zip.CHECKSUM` holding the venue's own SHA-256. SEXTANT-005 had to compute its own digests
with no publisher-side value to compare them against. Here there is one, so a download can be
verified against what the publisher says it published rather than only against itself.
Coverage will be reported at acquisition.

---

## 2. F1 — funding / basis / carry

### What the family requires

Per symbol, per day, all point-in-time: the realised funding rate and its settlement instant;
the funding interval, because it is not constant across symbols or time; the perpetual's
price; the spot price of the same asset, for the hedge leg; and the basis between them.

### What exists

| | |
|---|---:|
| USD-M perpetual symbols with a funding history | **952** |
| of those, USDT-quoted | **865** |
| monthly funding objects | **22,205** |
| bytes | 22,484,881 |
| span | **2020-01 to 2026-08** (80 months) |
| perpetual daily kline objects | 23,219 (39,781,650 bytes) |
| premium-index daily kline objects | 22,072 (21,136,439 bytes) |
| symbols with a 1d kline history | 951 of 952 |
| symbols with a 1d premium-index history | 947 of 952 |

The funding file's columns are `calc_time`, `funding_interval_hours`, `last_funding_rate`.
The interval is carried **per row**, so the fact that the venue moved some symbols from an
eight-hour to a four-hour cycle is a fact in the data rather than an assumption in the code.
Majors ran at eight hours throughout the window; this was checked on BTCUSDT, ETHUSDT,
SOLUSDT and DOGEUSDT at 2021-06, 2023-06, 2025-06 and 2026-06, and every month returned 90
rows at `funding_interval_hours = 8`.

### Does the timestamp prove availability before the decision instant

Yes, and more strongly than for any price signal in this project. Funding is not a
prediction: `calc_time` is the instant at which the payment is made between the long and the
short. A holder at that instant receives or pays it. A strategy that ranks on funding
*settled at or before* the decision instant is using a realised cash flow, not a forecast,
and the file's own timestamp is what bounds it.

### The window, and the breadth in it

The cross-section is measured, not assumed:

| month | USDT perpetuals live | of those, with a spot series already on disk |
|---|---:|---:|
| 2021-01 | 87 | 85 |
| 2022-01 | 138 | 133 |
| 2023-01 | 157 | 149 |
| 2024-01 | 260 | 242 |
| 2025-01 | 393 | 336 |
| 2026-01 | 563 | 385 |
| 2026-07 | 683 | 368 |

The second column is what a **cash-and-carry** construction can reach, because it needs both
legs of the same asset. 394 of the 865 USDT perpetuals have no spot pair on this venue at
all: a mix of scaled-unit contracts (`1000PEPEUSDT`, `1000SHIBUSDT`), perpetual-only crypto
listings, and, latterly, tokenised-equity perpetuals (`AAPLUSDT`, `AMZNUSDT`, `ASMLUSDT`).
The carry universe is therefore the point-in-time intersection of spot and perpetual, it is
reported at every rebalance, and no proxy is substituted for a missing leg.

### The delisting record

| | |
|---|---:|
| USDT perpetuals in the archive | 865 |
| whose funding history ends before the archive's last month | **151** |
| delistings bracketed inside 2022-02 to 2026-06 | **146** |

Delisted perpetuals are retained with partial final months. `1000BTTCUSDT`, dead in 2022-04,
still serves 41 funding rows and 11 daily bars for that month. The data records the death
rather than hiding it.

### The gap, stated rather than patched

**1,135 perpetual kline-months have no corresponding funding object.** Those months are
`not evaluable` for any carry construction: a missing funding file is not zero funding, and
treating it as zero would fabricate the exact quantity the family is about. This is
invariant 10, a failure is never an empty result, and the months will be counted and
reported, not filled.

### Costs, capacity and liquidation: what the data can and cannot support

- **Fees.** The venue's published futures schedule is a fact about a tariff, not about this
  dataset. Carried as a configured assumption exactly as in SEXTANT-005.
- **Spread.** `bookTicker` exists, and would allow spread to be *measured* rather than
  assumed, but only from **2023-05-16 to 2024-03-30**, at roughly 50 to 90 MB per symbol per
  day. Measuring it for even ten symbols across that range is several terabytes. The window
  also covers 11 of the candidate scored months and none of 2021, 2022, 2025 or 2026.
  **A stratified sample is affordable and is worth taking**, because carry is a small edge
  and spread is the cost most likely to eat it. It will be pre-registered as a sample fixed
  by rule before any byte is read, reported as a measurement over a stated slice, and it will
  **not** replace the configured assumption that invariant 12 requires. It will sit beside it
  and say whether the assumption is generous or mean.
- **Capacity.** Daily quote volume is in every kline row, so capacity can be expressed
  against realised traded notional. True order-book depth cannot be reconstructed outside the
  `bookTicker` slice, so a depth-based capacity number is not available and will not be
  invented.
- **Liquidation.** Mark-price klines are published, so the distance from a position to a
  liquidation price is computable *given a maintenance-margin ratio*. Historical margin tiers
  are **not** published; the venue's live bracket endpoint describes survivors only. The
  maintenance margin is therefore a stated assumption, labelled as one, and reported at more
  than one setting.

### Verdict

**Testable.** This is the family with the best data in the project: an official, realised,
exactly-timestamped cash flow, on a wide and growing cross-section, with the delisted names
retained.

---

## 3. F2 — long-short cross-sectional, and F3 — long-short trend following

### What the families require

Prices for a long leg and a short leg, and a **real** cost for the short.

### The short-leg cost problem, and why it does not arise here

The brief anticipates that borrow availability cannot be established historically and asks
which way the omission biases the result. On this dataset the question does not need to be
answered, because **the short leg is not a borrow**. A USD-M perpetual is a native instrument
in which a short is a position, not a loan, and the cost of holding it is the funding
payment, which this archive publishes, per symbol, per settlement, for 80 months.

That converts the single largest unmeasurable in the brief into a measured quantity. It is
worth being precise about what it does *not* solve:

- **Position limits and margin tiers** are not published historically, so a very large short
  is not constrained by anything in the data. At the account size in play this is not
  binding, and it is stated rather than relied on.
- **Perpetual-only universe.** A long-short built on perpetuals can only trade the 865 USDT
  perpetuals, not the 754-symbol spot universe SEXTANT-005 used. The cross-section is
  narrower and skewed towards names liquid enough for the venue to list a contract on. That
  biases *towards* tradability and away from the thin end where a spurious cross-sectional
  signal is most likely to appear, which is the conservative direction, and it is why the
  cross-section is reported at every rebalance.
- **Auto-deleveraging**, the venue closing a profitable position to cover a bankrupt
  counterparty, is a real risk with no historical record. Not modellable, stated as a known
  omission that biases results *optimistically*.

### What exists

The same acquisition as F1: perpetual daily klines for prices, funding for the carrying cost
of both legs. Spot bars are already on disk for the passive and long-only benchmarks. EUR
conversion uses `EURUSDT` spot, published from 2020-01 and already ingested. There is no
EUR-quoted perpetual, so the FX leg is unchanged from SEXTANT-005 and needs no
USDT-equals-USD assumption.

### Verdict

**Both testable.** F2 and F3 differ in signal, not in data.

---

## 4. F4 — long-only timing / regime exposure

### What the family requires

Nothing new. Daily spot bars over a point-in-time universe with a sourced delisting record,
and a regime label computable from information available at the decision instant.

### What exists

On disk, unchanged from SEXTANT-005 and covered by its committed checksums: 754 symbol
series, 856,601 daily bars, an 807-entry listing calendar with 269 delistings bracketed
inside the window, a 66-month window with 52 scored months, and the point-in-time regime
cascade already implemented and already used.

**Dataset fingerprint, unchanged:**
`1d4d21f2f631272c6b18fa6f471bd3929d5e8473677ea130e73e15bd7953ebaa`

### Verdict

**Testable, with zero acquisition.** The one caveat is that this family overlaps SEXTANT-005:
its time-series long/flat variants already *were* a timing test, and 13 of 16 of them lost to
their own timing null. F4 is worth running only in the forms SEXTANT-005 did not cover, a
regime-conditioned exposure rule rather than a per-instrument trend filter, and its
pre-registration must say plainly which part is new. Re-running the old variants and
reporting them as a new result would be dishonest arithmetic on the trial count.

---

## 5. F5 — news / event driven

This section records every source considered, including the rejected ones, and the temporal
availability of each, because the brief requires it.

### The general-news question, answered first

**A reliable point-in-time general news archive with trustworthy publication timestamps,
covering crypto over several years, is not accessible.** Checked:

| source | what it is | temporal availability | verdict |
|---|---|---|---|
| **GDELT 2.0 GKG** | global news knowledge graph, 15-minute files from 2015-02-18 | timestamp is GDELT's **observation** time, a legitimate lower bound on availability | **rejected on scale, not on integrity.** The master file list alone is 127 MB; GKG files run about 10 MB compressed every 15 minutes, roughly 350 GB per year. Ten years of crypto-relevant coverage cannot be extracted without ingesting the whole corpus |
| **GDELT DOC 2.0 API** | queryable article index | returns `seendate`, again observation time | **rejected on throughput.** Rate-limited to one request per five seconds and 250 records per query; reconstructing a decade of crypto coverage at that rate is not a research task, it is a scraping project |
| **CryptoPanic** | crypto news aggregator with an API | unknown; not reachable | **rejected.** The public endpoint returns a Cloudflare challenge, HTTP 403 |
| **Kaggle and HuggingFace crypto-news corpora** | assembled datasets | collection timestamps, hindsight selection, and in several cases sentiment labels applied after the fact | **rejected on principle.** The brief forbids exactly this: a timestamp that reflects collection rather than publication, and a classification made with knowledge of the outcome |
| **Publisher RSS and archive pages** | CoinDesk, Cointelegraph and similar | no historical archive endpoint; present-day pages only | **rejected.** A present-day article is not evidence of what was knowable in 2022 |
| **Reddit / Pushshift, X / Twitter** | social corpora | historical bulk access withdrawn | **rejected.** Not accessible |

There is no honest way to construct a general crypto news signal over this window from
accessible data, and the family would stop here were it not for one source that does qualify.

### The venue's own announcement archive, which does qualify

The venue publishes its announcements through a public, unauthenticated CMS endpoint that
carries a `releaseDate` per article. Enumerated in full during stage 0:

| catalog | articles | span |
|---|---:|---|
| **New Cryptocurrency Listing** | **2,253** | 2017-07-21 to 2026-09-09 |
| **Delisting** | **431** | 2022-02-17 to 2026-09-10 |
| Latest Binance News | 4,408 | not enumerated |
| Maintenance Updates | 589 | not enumerated |
| API Updates | 81 | not enumerated |

Listing announcements by year: 107 (2017), 86 (2018), 103 (2019), 348 (2020), 253 (2021),
170 (2022), 272 (2023), 297 (2024), 401 (2025), 216 (2026 to date).

This is an **official source**, the timestamp is the **publisher's own**, and the event is one
whose economic content is not in dispute: the venue announcing that it will list an asset is
public information with a known and immediate price consequence, and the announcement
precedes the listing by hours to days.

### The two things that could still make it unusable, and how each will be settled

Both are stated now, before the family is pre-registered, and both are **gates**: if either
fails, F5 is closed and reported as untestable rather than run on a compromised dataset.

1. **Is `releaseDate` publication or last edit?** The venue edits announcements. If the field
   carried the edit time, the dataset would be contaminated by hindsight in the worst
   possible direction. **Test:** for every listing announcement that names a symbol which
   exists in the listing calendar, compare `releaseDate` against that symbol's first observed
   trading day. A publication timestamp precedes first trade essentially always. A field that
   is materially often *after* first trade is an edit timestamp, and the family closes.
2. **Is the archive complete, or a present-day survivor view?** Announcements published and
   later deleted are invisible to a present-day API, and a deleted announcement is most
   likely to be one about an asset that went badly, the exact direction that flatters.
   **Test:** cross-check the announcement set against the file-presence listing calendar,
   which was built from a static archive and cannot have been retrospectively edited. Count
   the calendar's listings inside the window that have **no** announcement within a
   pre-registered lookback. That fraction is a direct measurement of the archive's
   completeness, and a pre-registered ceiling on it decides whether the family runs.

### Verdict

**Testable in one narrow form only: exchange listing and delisting announcements.** The
general news and sentiment family is **untestable** on accessible data and is closed here
with the sources and reasons above. Whatever F5 reports will be a statement about
announcement events, and the report will say so in those words rather than claiming anything
about news.

---

## 6. F6 — combinations

No new data. A combination is only registered where a clear economic hypothesis motivates it,
and it consumes trial budget from a declared allowance like any other variant.

---

## 7. What is being acquired, and what is deliberately not

### Taken

| tree | interval | objects | bytes |
|---|---|---:|---:|
| `futures/um/monthly/fundingRate` | n/a | 22,205 | 22,484,881 |
| `futures/um/monthly/klines` | 1d | 23,219 | 39,781,650 |
| `futures/um/monthly/premiumIndexKlines` | 1d | 22,072 | 21,136,439 |
| **total** | | **67,496** | **83,402,970 (83.4 MB)** |

Plus the announcement index, which is a few hundred kilobytes of JSON.

Projected wall clock, scaled from SEXTANT-005's measured rate of 28,577 objects in 17.8
minutes: **roughly 45 minutes**, unattended.

### Not taken, and why

| tree | why not |
|---|---|
| `aggTrades`, `trades` | tick data, terabytes. Nothing in any pre-registered variant reads an intraday print |
| `bookTicker` in bulk | 50 to 90 MB per symbol per day, and only 2023-05 to 2024-03 exists. A **stratified sample** is pre-registered separately; the bulk is not affordable and would not cover the window anyway |
| `markPriceKlines` | needed only for the liquidation study in stage 3, and only for families that reach it. Deferred until a family clears 1x, so that a family which never gets there does not pay for it |
| `indexPriceKlines` | the premium index already expresses the perpetual against the index; the index level itself adds nothing a variant reads |
| `futures/cm/`, coin-margined | 49 symbols against 952. A coin-margined carry is a different instrument with a different collateral currency and would need its own pre-registration. Out of scope, recorded as untested rather than unavailable |
| intraday intervals of any tree | every registered signal reads daily closes, as in SEXTANT-005. Keeping the grain identical is what makes the two spikes comparable |
| option data | no family reads it |

---

## 8. The priority order

Unchanged from the brief: F1, F2, F3, F4, F5, F6. Nothing in the data gives an objective
reason to reorder. F1 keeps its place on its own merits: it has the best-timestamped data in
the project and its return does not depend on predicting direction.

The one adjustment the data does justify is a note rather than a reorder. **F4 is
substantially pre-answered by SEXTANT-005**, whose time-series long/flat family was already an
exposure-timing test and lost to its own timing null in 13 of 16 cells. F4 will be
pre-registered narrowly, on the regime-conditioned construction SEXTANT-005 did not run, and
its trial budget will be small in proportion.

---

## 9. What this document confirmed by running, and what it assumes

**Confirmed by running.** Every count in this document — 952 perpetual symbols, 865
USDT-quoted, 22,205 funding objects and their 22,484,881 bytes, 23,219 kline objects, 22,072
premium-index objects, the 80-month span, the per-month universe sizes, the 151 dead
perpetuals, the 1,135 funding-less kline months, the 2,253 listing and 431 delisting
announcements and their spans, the absence of any EUR-quoted perpetual, the `bookTicker` range
and file sizes — came from listing or reading the source during stage 0. The funding file's
agreement with the venue's live endpoint was verified row by row. The GDELT, CryptoPanic and
announcement endpoints were called, and their status codes are what is reported.

**Assumed.** That the archive will continue to serve these objects during acquisition, and
that objects not yet downloaded are readable; SEXTANT-005 met exactly one zero-byte object in
28,578, and the same class of failure will be reported here rather than patched. That the
venue's published `.CHECKSUM` values are the digests of the objects as published; this will be
verified at download and the coverage reported. That the announcement `releaseDate` is a
publication timestamp — **this is a gate, not an assumption**, and section 5 states the test
that decides it.

**Not established, and not to be treated as though it were.** Historical maintenance-margin
tiers, historical order-book depth outside the `bookTicker` slice, borrow availability of any
kind, and auto-deleveraging events. Each is named where it bites, and each is carried as a
labelled assumption or as a stated omission with its direction of bias.

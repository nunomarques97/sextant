# Data availability - what each venue can actually supply

**Tasks:** SEXTANT-002, revised by SEXTANT-003. **Measured:** 2026-09-09.
**Method:** real requests to public endpoints, journalled; plus the venue's own
downloadable quarterly archives, checksummed and read.

**What SEXTANT-003 changed.** SEXTANT-002 recorded a NO-GO on Kraken as a
backtest data source. That verdict was correct about the public API and wrong
about Kraken, and the difference was a dataset that could not be downloaded at
the time. It has since been opened. **The NO-GO is superseded; §3 and §6 below
are rewritten, and §9 states exactly what was true, what changed, and what is
still unresolved.** Nothing in this document was deleted to make the correction
tidy.

Everything below is either backed by a request recorded in
`data/spike/<venue>/requests.jsonl`, by a page from the venue's own support
site, or is explicitly marked **unverified**. Where a question could not be
answered, the section says so rather than substituting a question that could.

---

## 0. The answer

**The project's data premise holds on Binance, and holds on Kraken through the
venue's downloadable quarterly archives rather than through its API.**

On Binance a point-in-time universe *including delisted instruments* is
reconstructible back to **August 2017**, from two independent official sources
that agree with each other.

On Kraken it is not reconstructible from the public API at all - delisted pairs
are absent from the instrument endpoint, their history is refused, and the daily
series for surviving pairs is capped at about two years. It **is** reconstructible
from the venue's quarterly OHLCVT archives, which retain delisted pairs. Of the
762 pairs in `Kraken_OHLCVT_Q3_2024.zip`, **154 no longer appear in `AssetPairs`
today**; in EUR alone, 55 of 288. The `EQuery:Invalid asset pair` finding was a
fact about the REST API, not about Kraken's data.

This matters more than it sounds. On Binance, at 1 January 2021, **238 of the
in-scope instruments were listed and 119 of them - exactly half - no longer
trade today**. A backtest built from today's instrument list would silently drop
half its 2021 universe, and drop it non-randomly: the half that died.

Go / no-go: **go on Binance. Go on Kraken via the archives, conditionally** -
see §9 for the two conditions, of which one is currently unmet.

**Kraken is the current leading candidate for both research and execution.**
That is provisional until the archives are shown sufficient to reconstruct the
required point-in-time universe over the intended backtest window, which
SEXTANT-003 could not establish because 11 of the 13 quarterly files remain
undownloadable. Binance stays a first-class independent adapter and historical
cross-check.

## 1. What was fetched, and from where

| Venue | Endpoint or source | Calls | Purpose |
|---|---|---:|---|
| Binance | `GET https://api.binance.com/api/v3/exchangeInfo` | 1 | Symbol metadata and status for all 3,696 symbols |
| Binance | `GET https://api.binance.com/api/v3/klines` | 1,386 | Daily bars for 818 in-scope symbols, plus the delisted probe |
| Binance | `GET https://api.binance.com/api/v3/ticker/bookTicker` | 1 | One top-of-book snapshot, 525 symbols |
| Binance | `GET https://api.binance.com/api/v3/time` | 1 | Health |
| Binance | `GET https://s3-ap-northeast-1.amazonaws.com/data.binance.vision` | 4 | Full listing of archive symbol directories |
| Kraken | `GET https://api.kraken.com/0/public/AssetPairs` | 2 | Pair metadata, unfiltered and filtered by `country_code=PT` |
| Kraken | `GET https://api.kraken.com/0/public/OHLC` | ~1,270 | Daily candles for in-scope pairs, plus the delisted probe |
| Kraken | `GET https://api.kraken.com/0/public/Ticker` | 13 | Top-of-book snapshot, batched |
| Kraken | `GET https://api.kraken.com/0/public/SystemStatus` | 1 | Health |
| Kraken (web) | `support.kraken.com/sections/delistings` and the individual notices | - | Official delisting dates and pair names |
| Kraken (web) | The downloadable historical OHLCVT article and its Google Drive links | - | Establishing what the bulk dataset is and whether it can be obtained |

The Binance run made **1,393 calls, all of which returned 200**, transferring
172.7 MB, with zero failures and zero retries. No credential was attached to any
request, no private endpoint was called, and no order path exists in this commit
range.

**Traceability.** Every call is journalled with its URL, parameters, request and
response timestamps, HTTP status, response size and attempt number. Any figure
in this document can be traced to the line that produced it.

---

## 2. Three grades of evidence, and why the distinction is enforced

Historical instrument metadata is the part of a dataset most easily invented, so
the code refuses to blur these:

| Grade | Meaning | Where it appears below |
|---|---|---|
| **Directly verified** | The venue itself asserted it, through its API, its published archive or a dated announcement | Binance symbol status; every Kraken delisting date; the Kraken jurisdiction filter |
| **Reconstructed** | Derived from venue data by a stated methodology. A bound, not a fact | Binance listing and delisting *instants*, inferred from first and last observed bar |
| **Unverified** | Nothing established it | Every Kraken listing date; the contents of Kraken's bulk dataset |

`Provenance` is a domain type carried on every `Instrument`, and the code acts on
it rather than merely recording it: the listing-age rule returns
*not evaluable* - never admit, never reject - for an instrument whose listing
window is unverified. That single rule is what stops a truncated history from
giving every instrument the same fabricated birthday.

### The reconstruction methodology, stated

- **Listing instant** = open time of the first daily bar the venue will serve.
  This is a *lower bound*: a pair listed on a day with no trades has no bar.
- **Delisting instant** = close of the last observed bar, and only for symbols
  the venue's own metadata marks as no longer trading. The *fact* of being
  delisted is the venue's statement; the *instant* is ours.
- **Exception**: where the first observed bar sits on the edge of the venue's
  servable window, it dates the truncation and not the listing, so the window is
  recorded as unverified instead. This is what happens to every Kraken pair that
  was already trading when the two-year window opens.
- **Trading constraints** (tick size, lot size, minimum notional) are the
  *current* published values. Neither venue publishes a history of them, so
  applying them to a past date is itself a reconstruction. It weakens the
  executable universe specifically, and §3 says by how much.

---

## 3. Can the delisted history be retrieved? Per venue, with evidence

### Binance: yes

Two independent official sources, and they agree.

1. **`exchangeInfo` retains delisted symbols.** Of 3,696 symbols returned,
   **1,366 are `TRADING` and 2,330 are `BREAK`**. The venue is telling us
   directly which pairs are dead. That is directly verified metadata, not an
   inference from a gap.
2. **The public data archive lists 3,710 symbol directories** under
   `data/spot/monthly/klines/`, obtained by paging the bucket listing. All 2,330
   `BREAK` symbols appear in it.

The two sources differ by 25 symbols present in the archive but absent from
`exchangeInfo`, and 11 present in `exchangeInfo` but not yet in the archive
(recent listings; the archive lags). **Of the 25, exactly one - `NBTUSDT` - has
an in-scope quote currency**, and `klines` still serves it. So a candidate set
built from `exchangeInfo` misses at most one instrument in 818, and the fix is
known: build the candidate set from the archive listing instead.

#### The probe: seven named delisted symbols, all retrieved

Each was chosen from outside the current trading list, and each returned its
complete trading life.

| Symbol | Outcome | First bar | Last bar | Bars | Retrieved by |
|---|---|---|---|---:|---|
| BCCBTC | found | 2017-08-02 | 2018-11-20 | 472 | `api/v3/klines` |
| VENBTC | found | 2017-11-07 | 2018-10-19 | 260 | `api/v3/klines` |
| SALTBTC | found | 2017-09-30 | 2019-02-22 | 511 | `api/v3/klines` |
| MITHBTC | found | 2018-11-15 | 2022-11-28 | 1,475 | `api/v3/klines` |
| ERDBTC | found | 2019-07-04 | 2020-08-31 | 425 | `api/v3/klines` |
| LENDBTC | found | 2017-12-12 | 2020-10-12 | 1,036 | `api/v3/klines` |
| HCBTC | found | 2018-08-21 | 2020-12-30 | 863 | `api/v3/klines` |

Cross-checked in the archive: `BCCBTC` has 16 monthly daily-kline files spanning
`2017-08` to `2018-11`, each with a published checksum, which matches the API
answer independently.

#### How far back, and how much survivorship is at stake

Across the 818 in-scope symbols fetched, the earliest bar obtained is
**2017-08-17** and the deepest series spans **9 years**. Rebuilding the
point-in-time membership from the reconstructed calendar gives:

| As of | Listed then | Still listed today | Later delisted | Survivorship loss |
|---|---:|---:|---:|---:|
| 2018-01-01 | 6 | 5 | 1 | 17% |
| 2019-01-01 | 22 | 16 | 6 | 27% |
| 2020-01-01 | 87 | 51 | 36 | 41% |
| 2021-01-01 | 238 | 119 | 119 | 50% |
| 2022-01-01 | 369 | 190 | 179 | 49% |
| 2023-01-01 | 381 | 216 | 165 | 43% |
| 2024-01-01 | 406 | 253 | 153 | 38% |
| 2025-01-01 | 426 | 315 | 111 | 26% |
| 2026-01-01 | 478 | 420 | 58 | 12% |

This is the size of the error we would be making if we skipped this work. It is
not a rounding effect.

### Kraken: not from the API, yes from the archives

The API findings from SEXTANT-002 stand exactly as measured. They were simply
not the whole venue.

#### What the API cannot do, and this has not changed

1. **`AssetPairs` returns currently listed pairs only.** 1,449 pairs, and not
   one of the delisted pairs named in Kraken's own announcements appears in it.
2. **`OHLC` refuses delisted pairs** with `EQuery:Invalid asset pair`, delivered
   inside a `200 OK` body. The adapter classifies that envelope explicitly as a
   refusal rather than an empty result; treating it as "no data" is precisely how
   a survivorship-biased dataset gets built without anybody noticing.
3. **`OHLC` caps at about 720 candles** whatever `since` asks for. Requesting
   `XBTEUR` daily with `since=0` returned 721 rows beginning **2024-09-19**. So
   even for surviving pairs, the daily series reaches back about two years.

The seven-pair delisted probe was refused seven times out of seven. Those
figures are unrevised. **What was wrong was the inference drawn from them**: that
Kraken has no delisted history. It has, and publishes it, just not through this
endpoint.

#### What the archives do supply

Kraken publishes quarterly OHLCVT archives: a flat set of
`<PAIR>_<MINUTES>.csv` files, eight granularities per pair, thirteen quarterly
increments covering Q1 2023 to Q1 2026.

**Each quarterly file contains the pairs that were listed at the end of that
quarter.** That single property makes the diff between consecutive quarters a
directly-sourced listing calendar. It is membership derived from *file
presence*, not inferred from a gap in a price series, which is what lets every
entry carry `VENUE_ARCHIVE` rather than `RECONSTRUCTED`.

Measured on the two quarters currently held:

| | Q2 2024 | Q3 2024 |
|---|---:|---:|
| Pairs listed at quarter end | 700 | 762 |
| Delisted during the quarter | - | 13 |
| Listed during the quarter | - | 75 |
| Pairs listed and untraded | 8 | 9 |

The 13 that die during Q3 2024 are `ANT` in four quotes, `WAVES` in four,
`RNDR` in two, and three `AED` pairs. Kraken's own announcements put WAVES at
2024-07-08 and ANT at 2024-09-25, both inside Q3 - and the calendar derives that
bracket without ever seeing the announcements, which is the cross-check.

`Kraken_OHLCVT_Q3_2024.zip` holds 762 pairs of which **154 no longer appear in
`AssetPairs` today**, 55 of 288 in EUR alone.

#### The two defects in this dataset, measured

Both are properties of a quarterly archive, both are handled in code, and
neither is recoverable from the data.

**1. The final partial quarter of every delisted instrument is lost.** ANT
traded from 2024-07-01 to 2024-09-25 and that data exists nowhere: dropped from
Q3, wrong quarter for Q2. `ANTEUR_1440.csv` in Q2 holds 91 daily bars ending
2024-06-30, and the hourly file ends 2024-06-30 23:00. So a delisted
instrument's series ends at a quarter boundary, **up to three months before it
actually stopped trading**.

The bias direction is **optimistic**: a position is marked out at the
quarter-end price rather than at the post-announcement price, and delisting
announcements usually crater the price. It is bounded at one quarter per
instrument. It is modelled by an explicit, configurable mark-out haircut,
default 20%, reported at 0%, 20% and 50% in
[`docs/kraken-archive-tables.md`](kraken-archive-tables.md).

**2. Presence and tradability are different.** `WAVESEUR` is present in Q2 2024
with **zero rows at every one of the eight granularities**, while the official
announcement says trading stopped 2024-07-08. An empty file means *listed but
not traded*, which is information rather than an absence of it. The data is
authoritative for "was this tradable"; announcements are context only.
Listed-and-untraded is carried as its own state from the archive reader through
the calendar to the parquet store, where it is a real zero-row series rather
than an absent one.

#### What is still missing, precisely

**Eleven of the thirteen quarterly files could not be obtained.** Programmatic
download was attempted on 2026-09-09: the Drive folder listing was fetched and
all thirteen file identifiers extracted, and every one of the thirteen returned
`Google Drive - Quota exceeded` at the confirmation step. The quota is
folder-wide, not per file, and it also blocks the two files we do hold - which
were fetched by hand from a signed-in browser. That is the workaround.

| Quarter | Held | Quarter | Held |
|---|---|---|---|
| Q1 2023 | **missing** | Q3 2024 | yes |
| Q2 2023 | **missing** | Q4 2024 | **missing** |
| Q3 2023 | **missing** | Q1 2025 | **missing** |
| Q4 2023 | **missing** | Q2 2025 | **missing** |
| Q1 2024 | **missing** | Q3 2025 | **missing** |
| Q2 2024 | yes | Q4 2025 | **missing** |
| | | Q1 2026 | **missing** |

The two held files are checksummed in `data/kraken-archive/manifest.json` and
re-verified on every read.

**The consequence is not cosmetic.** With two adjacent quarters the archive
covers 2024-04-01 to 2024-10-01, and the earliest instant any pair is
*certainly* listed by is 2024-07-01, the end of the first held quarter. Rule 2
requires 180 days from there, which is 2024-12-28, past the end of the data. So
the point-in-time research universe is **zero in every month**, for arithmetic
reasons rather than empirical ones, and it stays that way until earlier quarters
are acquired. §6 gives the tables.

### What `instruments(at)` can honestly answer

| Venue | `instruments(at)` for a past date | Why |
|---|---|---|
| Binance | Yes, with a reconstructed calendar, back to 2017-08 | Delisted symbols and their bars are both served |
| Kraken, from the API | Refused. Raises `PointInTimeUnavailable`, naming the missing data and the smallest remedy | No delisted pairs, no listing dates, two-year cap |
| Kraken, from the archives | Yes, three-valued, over the quarters held | Membership from file presence; an instant inside a bracket answers *undetermined* |

Neither adapter answers a historical question without a calendar. Without one
both raise rather than returning today's survivors, and a test asserts that.

**The archive calendar answers in three values, not two.** A delisting is an
interval, so an instant falling inside one - 15 August 2024 for ANT, say - is
`UNDETERMINED`. That is neither an admission nor a rejection, it propagates as
*not evaluable* through the universe rules, and it is counted in its own column
in every table. Rounding it to "not listed" would delete instruments from
history; rounding it to "listed" would invent them.

**A quarter we do not hold widens every bracket that spans it**, and a pair the
held archives never mention is `UNDETERMINED` rather than absent, because
absence from an incomplete download is a fact about the download.

---

## 4. Universe sizes, measured

Full month-by-month tables with per-rule exclusion counts, for both venues and
all three quote policies, are in
[`docs/universe-tables.md`](universe-tables.md).

Account applied to the executable universe, per PO decision D2: 1,500 EUR over
at most 8 concurrent positions, so a target position of 187.50 EUR, a minimum
notional ceiling of 46.875 EUR and a lot-step ceiling of 1.875 EUR.

### Reading the columns

- **liquidity** - rules 1, 3 and 7 (quote currency, median volume, asset class).
  Not point-in-time complete; reported because on a venue that cannot date its
  listings it is the only number with content left in it.
- **research** - rules 1, 2, 3 and 7. The number to use.
- **+spread** - rule 4 applied on top. It is *not evaluable* at any past
  instant, so this column equals research only in the single month where a live
  spread snapshot exists, and is zero everywhere else. That is the correct
  behaviour of an unevaluable rule, and it is the measurement of what the
  missing spread dataset costs us.
- **executable** - research intersected with rules 5 and 6.

### Binance

| Quote policy | Peak research universe | Months "ok" (>25) | Months "too thin" (<15) | Executable at peak |
|---|---:|---:|---:|---:|
| EUR | 32 (2021-12) | 4 of 109 | 83 of 109 | 31 |
| EUR+USD | 32 (2021-12) | 4 of 109 | 83 of 109 | 31 |
| EUR+USD+USDT | 384 (2025-12) | 84 of 109 | 16 of 109 | 384 |

Three things fall out of this.

**Binance has no USD spot pairs at all.** The quote breakdown of the 818 in-scope
symbols is 741 USDT, 70 EUR, 7 USD. Adding USD to the EUR policy changes nothing
measurable. On this venue the dollar rails are USDT, USDC, FDUSD and TUSD, not
USD.

**EUR-only on Binance is not viable for a cross-sectional strategy.** 83 of 109
months fall below 15 instruments, and the current membership is six names:
ADAEUR, BTCEUR, DOGEEUR, ETHEUR, SOLEUR, XRPEUR. Ranking six correlated
large-caps against each other is not a cross-sectional strategy.

**The account barely binds, but not never.** On the EUR policy the largest gap
between the research and executable universes across 109 months is **one**
instrument. On the full EUR+USD+USDT policy it reaches **11** out of 384, in
December 2021, and the account rules reject at most 18 instruments in any single
month (April 2021). So the split matters at the margin and in the frothiest
periods, which is where it would matter most, and it is nowhere near large
enough to be the reason a strategy does or does not work. PO decision D1 is
right architecturally and the measured cost of ignoring it would have been
around 3% of the universe at its worst.

At the one instant where spread is evaluable, 298 of the research members clear
the 25 bps cap and 29 do not, so roughly one in ten of the current universe is
too wide today.

### Kraken

**These are the API-only numbers, kept because they are a correct measurement of
the API and because the comparison in §6 is against them. They are no longer the
best available picture of Kraken.** One sentence below is withdrawn outright and
marked where it appears; the rest stands as measured.

Only 24 monthly refreshes exist, because the venue serves only ~720 daily
candles. The observed window is **2024-09-19 to 2026-09-08**, across 1,260
in-scope pairs.

The listing-date problem dominates everything. Of the 1,260 pairs, **489 were
already trading when the window opened**, so their first observed bar dates the
truncation rather than the listing and their windows are recorded as
*unverified*. The listing-age rule is therefore not evaluable for them and they
can never enter a point-in-time universe. Only the **759** pairs that listed
inside the window can be dated at all.

| Quote policy | Peak research | Peak liquidity | Months "ok" (>25) | Months "too thin" (<15) |
|---|---:|---:|---:|---:|
| EUR | 4 (2025-08) | 33 (2024-12) | 0 of 24 | 24 of 24 |
| EUR+USD | 27 (2026-07) | 133 (2025-01) | 1 of 24 | 12 of 24 |
| EUR+USD+USDT | 27 (2026-07) | 147 (2025-01) | 1 of 24 | 12 of 24 |

Read that gap between "liquidity" and "research" carefully. It is not a
liquidity finding. 147 pairs pass the quote, volume and asset-class rules in
January 2025; **zero** of them survive the point-in-time universe, because at
that date every pair old enough to matter has an unverifiable listing date. The
research column only becomes non-zero once pairs that listed inside the window
have aged past 180 days. Kraken's research universe is not small because Kraken
is illiquid. It is small because Kraken will not tell us when anything listed.

**The last sentence is withdrawn (SEXTANT-003).** Kraken does tell us, in its
quarterly archives, and against those the count of pairs with an unverifiable
listing date is zero rather than 489. What is true is narrower: *this endpoint*
will not tell us. See §3 and §6.

**The known-missing column reads zero, and that is misleading rather than
reassuring.** The four pairs whose delisting instants we sourced from Kraken's
own notices - WAVES and ANT in EUR and USD - all stopped trading before October
2024, so they fall outside every measurable month. Meanwhile Kraken's December
2025 notice delisted **UST, LUNA2, NODL, PDA, ETHW, TVK, TUSD, MOVE and BRICK**,
squarely inside the window, and all nine are now entirely absent from
`AssetPairs`. Their pairs cannot be enumerated from the API and the notice names
assets rather than pairs, so they are reported here in prose rather than
fabricated into the table. The true deficit for Kraken months in 2024-2025 is
therefore materially larger than zero and was not quantified.

**The account does not bind at all.** Across all 24 months and all three
policies the research and executable universes are identical, and neither
account rule rejects a single pair. Kraken's `costmin` is uniformly 0.45 EUR
against our 46.875 EUR ceiling.

---

## 5. What a cost model could honestly be calibrated from

Fees are published and exact. Everything else is the problem.

| Input | Binance | Kraken |
|---|---|---|
| Fee schedule | Published, exact | Published, exact |
| Historical quoted spread | **Not published** for spot, at any granularity, free or paid | **Not published**. `Spread` with `since=0` returned 250 rows spanning 12 seconds |
| Historical top-of-book files | Futures only (`bookTicker`), and futures are out of scope under D4 | None |
| Historical trade prints | **Yes.** The archive carries `trades` and `aggTrades` per symbol, monthly, including for delisted symbols | In principle via `Trades`, in practice not - see below |
| Historical order-book depth | None free | None free |

**Binance is the only venue where an effective-spread estimate is buildable.**
The archive holds per-symbol tick data, and it holds it for dead symbols too: the
`aggTrades` directory for the delisted `BCCBTC` exists. Volume is real but
manageable - `BTCEUR` has 80 monthly `aggTrades` archives totalling 2.58 GB, so a
50-instrument universe is on the order of 100 GB compressed. That is a storage
decision, not a blocker.

**The archives do not help here.** They carry OHLCVT - open, high, low, close,
volume, trade count - and no quotes at any granularity. A cost model calibrated
from them is a cost model calibrated from bar data, with the error direction
stated below. Nothing in SEXTANT-003 changes §5.

**On Kraken the same reconstruction is infeasible.** `Trades` pages 1,000 prints
per call and the public rate limit is about one call per second. `XBTEUR` alone
recorded **20,075,939 trades in the last 720 days**, which is 20,075 paged calls,
or about **5.6 hours of continuous paging for one pair over two years**. A
50-pair universe over five years is not a job anyone runs.

**Error direction, stated plainly.** A cost model calibrated from bar data alone
**understates** cost, and understates it most on thin instruments - which is
exactly where a cross-sectional strategy appears to find its edge. Until an
effective-spread estimate exists, every backtest must carry an explicit,
deliberately pessimistic spread assumption. No model should be fitted to bar
data and then described as calibrated.

---

## 6. Which venue should lead ingestion

**This section is rewritten. SEXTANT-002 answered "Binance, and it is not
close." That was right on the evidence then available and is no longer right.**

| | Binance | Kraken (API) | Kraken (archives) |
|---|---|---|---|
| Delisted instrument metadata | Directly verified, 2,330 symbols | Absent | Directly verified, 154 dead pairs in one quarter |
| Delisted price history | Retrieved, 7 of 7 probed | Refused, 0 of 7 probed | Present, to a quarter boundary |
| Depth of daily history | Back to 2017-08 | ~2 years, capped | Q1 2023 onward, when all 13 files are held |
| Listing dates | Reconstructible from first bar | Unverifiable | Bracketed to a quarter, sourced from presence |
| Tick data for cost calibration | Published, including dead symbols | Infeasible to page | Not published |
| In-scope instruments | 818 | ~1,260 | 775 across the two quarters held |
| EUR pairs | 70 | 546 | 288 in Q3 2024 alone |

Two facts settle the direction.

**Kraken has 288 EUR pairs in a single 2024 quarter against Binance's 70
today.** For an account funded in EUR that is the difference between a real EUR
universe and six coins. SEXTANT-002 already flagged this as the thing that would
flip the recommendation if the archives turned out to contain delisted pairs.
They do.

**Kraken answers the jurisdiction question directly.** `AssetPairs` accepts a
`country_code` parameter: unfiltered it returns 1,449 pairs, with
`country_code=PT` it returns 1,283. That is the jurisdictional eligibility layer
sourced from the venue itself rather than from our configuration. Binance offers
no equivalent, and whether a Portuguese retail account may trade on Binance at
all is still Phase 0 risk 4, untouched.

**So: Kraken is the current leading candidate for both research and execution,
provisionally.** The "research on Binance, execute on Kraken" split is withdrawn
before it was tested, because it is no longer needed: it is the venue we can
actually trade from Portugal, and it now has the deeper EUR history.

The provisionality is not a formality. It rests on one unmet condition:

1. **The archives must cover the intended backtest window.** Two of thirteen
   quarters are held. At two quarters the research universe is arithmetically
   zero, so nothing about the venue's suitability has actually been
   demonstrated - only that the method works. **Unmet.**
2. **The method must be sound.** Demonstrated: the calendar reproduces both
   facts verified by hand against the real files, and the ANT delisting instant
   Kraken announced falls inside a bracket derived without reference to it.
   **Met.**

**Binance stays a first-class independent adapter and historical cross-check**,
and it remains the only venue where an effective-spread estimate is buildable at
all. Nothing about the capability model changes, and the two adapters remain
peers that cannot import each other.

### The archive tables

Full month-by-month tables over the archive window, with per-rule exclusion
counts and the haircut sensitivity, are in
[`docs/kraken-archive-tables.md`](kraken-archive-tables.md).

Read against the SEXTANT-002 API-only Kraken tables, the comparison is:

| | API-only (SEXTANT-002) | Archives (SEXTANT-003) |
|---|---|---|
| Window | 2024-09-19 to 2026-09-08 | 2024-04-01 to 2024-10-01 |
| Monthly refreshes | 24 | 7 |
| Candidate pairs | 1,260 | 775 |
| Pairs with an unverifiable listing date | 489 of 1,260 | **0 of 775** |
| Delisted pairs in the candidate set | 0 | 13 in one quarter |
| Peak EUR research universe | 4 | 0, for want of window length |

**How much of the previous Kraken picture was an artefact of the truncation?**
The listing-date part, entirely. SEXTANT-002 reported that 489 of 1,260 pairs
could never enter a point-in-time universe because their first observed bar
dated the endpoint's truncation rather than a listing. Against the archives that
number is zero: every pair's listing is bracketed from file presence and carries
`VENUE_ARCHIVE`. The claim that "Kraken's research universe is small because
Kraken will not tell us when anything listed" is withdrawn - Kraken does tell
us, in the archives.

**What has not been demonstrated is the opposite claim.** The research universe
is still zero here, for a different and purely arithmetic reason: 180 days of
required listing age do not fit inside a two-quarter window. Whether Kraken's
EUR research universe is actually large enough is **not answered by this task**
and cannot be until more quarters are downloaded. The liquidity column - rules
1, 3 and 7, no listing age - reaches 16 pairs on EUR and 64 on EUR+USD, which is
a floor rather than a finding.

## 7. What remains unverified

Stated so that none of it is mistaken for a finding.

1. **Whether Kraken's quarterly archives cover the intended backtest window.**
   Two of thirteen files are held; the other eleven are named in §3. This is the
   one unmet condition behind the Kraken recommendation, and it is a download
   rather than a code change.
2. **The size of Kraken's EUR research universe.** Not answered. The archive
   window currently held is shorter than the listing-age rule, so every research
   number is zero for arithmetic reasons. Nothing about the venue's suitability
   was established either way.
3. **Membership between 2026-04-01 and today.** The archive ends at the close of
   Q1 2026. A pair that listed and delisted inside that window is absent from the
   archive and absent from `AssetPairs`, and no endpoint will still serve it.
   **This one has no available remedy**, and §10 states where it must be
   declared.
4. **The full count of Kraken pairs delisted during any window.** Now sourced
   from the archives for the quarters held - 13 during Q3 2024 - and unknown
   for the quarters not held.
5. **Binance listing and delisting instants.** Reconstructed from first and last
   observed bar, never published by the venue. Bounds, not facts.
6. **Historical trading constraints on both venues.** Current values applied to
   past dates. On Kraken this now also affects pairs that no longer appear in
   `AssetPairs` at all: the archive carries their bars but not their tick size,
   lot size or minimum notional, so the executable universe is weaker than the
   research one for exactly the dead names.
7. **Spread at any past instant, on either venue.** One live snapshot exists per
   venue and nothing else. Unchanged, and the archives do not help: they carry
   OHLCVT, not quotes.
8. **Whether a Portuguese retail account may actually trade on Binance.** Phase 0
   risk 4 is untouched by this task; nothing here required or used a credential.
9. **The magnitude of the lost-final-quarter bias.** The 20% haircut is a
   placeholder chosen to be pessimistic, not a calibration. Nothing in this
   dataset can calibrate it, because the prices it stands in for are the ones the
   archive does not contain.

---

## 8. Standing fallback

Per PO decision D7, and recorded here so it is not re-litigated: **where delisted
history cannot be obtained, restrict the backtest window rather than accept the
bias.** A survivorship-biased backtest that clears the live gates has cleared
nothing.

On Binance no restriction is needed - the history is there from 2017. On Kraken
the position has changed: delisted history *is* obtainable, from the archives,
so the window to restrict to is the window the acquired archives cover, plus the
forward window that `sextant snapshot-universe` records from now on. The gap
between the two - see §10 - is the part that must be excluded rather than
qualified.

---

## 9. What was true, what changed, what is still open

Kept as a single paragraph each, so the correction stands beside the thing it
corrects rather than replacing it silently.

**What SEXTANT-002 got right, and remains right.** Every API measurement.
`AssetPairs` really does return only current pairs. `OHLC` really does refuse
delisted pairs with `EQuery:Invalid asset pair` inside a 200 body, and really is
capped at about 720 candles. Seven of seven probed delistings really were
refused. The three-outcome classification built on those findings is what the
archive pipeline now relies on, and none of it is revised.

**What changed.** The inference, not the measurements. "Kraken cannot supply
delisted history" was drawn from an endpoint and applied to a venue. The
quarterly archives supply it, retain 154 pairs in one quarter that `AssetPairs`
has since forgotten, and support a listing calendar sourced from file presence
rather than from any price series. The blocker in SEXTANT-002 was a Google Drive
quota, and the smallest step named there - download one quarterly file and check
whether `WAVESEUR` and `ANTEUR` appear - was the right step and produced the
right answer.

**What is still open.** Eleven of thirteen quarters. Until they are downloaded,
the method is demonstrated and the venue is not. No backtest window on Kraken
should be treated as reconstructed on the strength of what is held today.

---

## 10. The unremediable window

**Between 2026-04-01 and the first `sextant snapshot-universe` record, Kraken
membership rests on no source at all.** The quarterly archive ends at the close
of Q1 2026. A pair that listed and delisted inside that window left nothing
behind: it is absent from the archive because the archive stops before it, and
absent from `AssetPairs` because the venue removed it. No endpoint will still
serve it and no download will recover it.

**This is survivorship bias with no available remedy**, and it must be stated
wherever a result covering that window is reported. It is carried in code as
`sextant.app.universe_snapshot.SURVIVORSHIP_WINDOW_NOTE` and written into every
generated table, so it travels with the numbers rather than living only here.

`sextant snapshot-universe` is the permanent fix and it fixes only the future.
It fetches `AssetPairs` with and without `country_code=PT` and appends a dated
membership record, idempotent per UTC day. Two such records bracket every
listing and delisting between them at exactly the grade of evidence the archive
calendar uses. From the first run onwards, no later version of this project has
to reconstruct anything.

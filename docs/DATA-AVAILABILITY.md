# Data availability - what each venue can actually supply

**Scope:** data-availability spike. **Measured:** 2026-09-09. **Method:** real requests to
public endpoints, journalled.

Everything below is either backed by a request recorded in
`data/spike/<venue>/requests.jsonl`, by a page from the venue's own support
site, or is explicitly marked **unverified**. Where a question could not be
answered, the section says so rather than substituting a question that could.

---

## 0. The answer

**The project's data premise holds on Binance and fails on Kraken.**

A point-in-time universe *including delisted instruments* is reconstructible on
Binance back to **August 2017**, from two independent official sources that
agree with each other. It is **not** reconstructible on Kraken from public API
data at all: delisted pairs are absent from the instrument endpoint, their
history is refused, and the daily series for surviving pairs is capped at about
two years.

This matters more than it sounds. On Binance, at 1 January 2021, **238 of the
in-scope instruments were listed and 119 of them - exactly half - no longer
trade today**. A backtest built from today's instrument list would silently drop
half its 2021 universe, and drop it non-randomly: the half that died.

Go / no-go: **go, on Binance, for the research universe.** Not on Kraken, and
not on EUR alone (see §3).

---

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

### Kraken: no

Every attempt failed, and the failures are the finding.

1. **`AssetPairs` returns currently listed pairs only.** 1,449 pairs, and not
   one of the delisted pairs named in Kraken's own announcements appears in it.
2. **`OHLC` refuses delisted pairs** with `EQuery:Invalid asset pair`, delivered
   inside a `200 OK` body. The adapter classifies that envelope explicitly as a
   refusal rather than an empty result; treating it as "no data" is precisely how
   a survivorship-biased dataset gets built without anybody noticing.
3. **`OHLC` caps at about 720 candles** whatever `since` asks for. Requesting
   `XBTEUR` daily with `since=0` returned 721 rows beginning **2024-09-19**. So
   even for surviving pairs, the daily series reaches back about two years.

#### The probe: seven pairs Kraken itself announced delisting, none retrievable

| Pair | Official source | Trading stopped | Outcome |
|---|---|---|---|
| WAVESEUR | `support.kraken.com/articles/asset-delisting-for-waves` | 2024-07-08 12:00 UTC | rejected: `EQuery:Invalid asset pair` |
| WAVESUSD | same notice | 2024-07-08 12:00 UTC | rejected |
| ANTEUR | `support.kraken.com/articles/delisting-of-aragon-ant` | 2024-09-25 14:00 UTC | rejected |
| ANTUSD | same notice | 2024-09-25 14:00 UTC | rejected |
| REPXBT | `support.kraken.com/articles/notice-of-spot-trading-pairs-removal` | 2024-04-05 10:00 UTC | rejected |
| GNOXBT | same notice | 2024-04-05 10:00 UTC | rejected |
| USTEUR | `support.kraken.com/articles/notice-of-scheduled-delistings-dec-2025` | 2025-12-12 14:00 UTC | rejected |

The delisting dates above are **directly verified** - Kraken published them. The
price history behind them is simply not obtainable from the public API.

The venue's delistings section carries roughly 39 further notices, so the true
number of pairs missing from any Kraken universe we can build is larger than
seven. Enumerating it fully means parsing every announcement, which was not
done and is named here as an unresolved gap rather than estimated.

#### The bulk dataset: exists, blocked, contents unverified

Kraken publishes downloadable historical OHLCVT CSVs covering "each of our
currency pairs, from the start of each market to present", as a single
**7.3 GB** Google Drive file (complete history through Q3 2024) plus quarterly
increments Q4 2024 through Q1 2026.

**Every download attempt returned `Google Drive - Quota exceeded`** - the full
dump and two quarterly files alike, on 2026-09-09. The article does not state
whether delisted pairs are included, and the file could not be opened to check.

So: it may contain exactly what is missing, and that is **unverified**. The
smallest step that would resolve it is a single manual download of one quarterly
increment - `Kraken_OHLCVT_Q3_2024.zip` - and a check of whether `WAVESEUR.csv`
and `ANTEUR.csv` appear in it. Both pairs were trading during that quarter, so
their presence or absence settles the question outright. This is a manual
action, not a code change, and it is the only thing that could move Kraken from
"no" to "yes".

### What `instruments(at)` can honestly answer

| Venue | `instruments(at)` for a past date | Why |
|---|---|---|
| Binance | Yes, with a reconstructed calendar, back to 2017-08 | Delisted symbols and their bars are both served |
| Kraken | Refused. Raises `PointInTimeUnavailable`, naming the missing data and the smallest remedy | No delisted pairs, no listing dates, two-year cap |

Neither adapter answers a historical question without a calendar. Without one
both raise rather than returning today's survivors, and a test asserts that.

---

## 4. Universe sizes, measured

Full month-by-month tables with per-rule exclusion counts, for both venues and
all three quote policies, are in
[`docs/universe-tables.md`](universe-tables.md).

Example account applied to the executable universe, per decision D2: 1,500 EUR over
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
enough to be the reason a strategy does or does not work. Decision D1 is
right architecturally and the measured cost of ignoring it would have been
around 3% of the universe at its worst.

At the one instant where spread is evaluable, 298 of the research members clear
the 25 bps cap and 29 do not, so roughly one in ten of the current universe is
too wide today.

### Kraken

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

**Binance**, on the data question alone, and it is not close.

| | Binance | Kraken |
|---|---|---|
| Delisted instrument metadata | Directly verified, 2,330 symbols | Absent |
| Delisted price history | Retrieved, 7 of 7 probed | Refused, 0 of 7 probed |
| Depth of daily history | Back to 2017-08 | ~2 years, capped |
| Listing dates | Reconstructible from first bar | Unverifiable |
| Tick data for cost calibration | Published, including dead symbols | Infeasible to page |
| In-scope instruments | 818 | ~1,260 |
| EUR pairs | 70 | 546 |

This is a recommendation about **where the first backtest gets its data**, and
it is not a ranking of the venues as places to trade. Two facts pull the other
way and should not be lost:

- **Kraken has 546 EUR pairs against Binance's 70.** For an account funded in
  EUR that is the difference between a real EUR universe and six coins. If
  Kraken's bulk dataset turns out to contain delisted pairs, Kraken becomes the
  better *research* venue for EUR, not the worse one.
- **Kraken answers the jurisdiction question directly.** `AssetPairs` accepts a
  `country_code` parameter: unfiltered it returns 1,449 pairs, with
  `country_code=PT` it returns 1,283, and of the 1,260 in-scope pairs 1,127 are
  visible to Portugal. That is the jurisdictional eligibility layer sourced from
  the venue itself rather than from our configuration, which is strictly better
  than the configured guess Phase 0 assumed we would be stuck with. Binance
  offers no equivalent.

So: ingest and backtest on Binance first, on the EUR+USD+USDT research universe.
Keep Kraken as a first-class peer, and revisit it the moment the bulk dataset
question is settled.

---

## 7. What remains unverified

Stated so that none of it is mistaken for a finding.

1. **Whether Kraken's 7.3 GB bulk OHLCVT dataset contains delisted pairs.**
   Blocked by a Google Drive quota on every attempt. Resolvable by one manual
   download; the check is named in §3.
2. **The full count of Kraken pairs delisted during any window.** Seven are
   sourced; roughly 39 announcements exist. The rest were not enumerated.
3. **Binance listing and delisting instants.** Reconstructed from first and last
   observed bar, never published by the venue. Bounds, not facts.
4. **Historical trading constraints on both venues.** Current values applied to
   past dates.
5. **Spread at any past instant, on either venue.** One live snapshot exists per
   venue and nothing else.
6. **Whether a retail account in the configured jurisdiction (PT) may actually
   trade on Binance.** Phase 0 risk 4 is untouched by the spike; nothing here required or used a credential.

---

## 8. Standing fallback

Per decision D7, and recorded here so it is not re-litigated: **where delisted
history cannot be obtained, restrict the backtest window rather than accept the
bias.** A survivorship-biased backtest that clears the live gates has cleared
nothing.

On Binance no restriction is needed - the history is there from 2017. On Kraken,
until the bulk dataset is checked, no backtest window can be honestly
reconstructed at all, and the venue should not be used as a backtest data source.

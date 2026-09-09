# SEXTANT-005 pre-registration

**Version `v1`. Part 1 committed 2026-09-09, before any Binance datum was downloaded.**

This document is written in two parts, committed separately, because the brief contains a
genuine ordering conflict and this is its resolution. Acquisition cannot be scoped until the
variants are known, since the minimum dataset is derived from what the variants need. The
variants cannot be finalised against real data without risking their being fitted to it.
So **part 1 is the variant specification and needs no data at all**, and **part 2 is the
dataset section**, filled in with the acquisition's real facts. Both are committed before any
strategy result is produced.

`config/spike-005.yaml` is the machine-readable half of this document. The runner asserts the
two agree with the code and refuses to start if they have drifted, the same guard
`config/benchmarks.yaml` carries for SEXTANT-004.

**What was consulted before this part was written.** The set of symbol directories the venue
publishes under `data/spot/monthly/klines/` — 3,710 of them — and the layout of the archive's
paths. That is listing metadata and nothing else. No price, no volume and no return was read,
and no file was downloaded. The counts appear here only to justify choosing a quote policy
that has a cross-section in it.

---

## Part 1 — the specification

### 1. The hypothesis, stated so it can be refuted

> Over the evaluation window and across the regimes it contains, at least one of the sixteen
> variants specified below earns an out-of-sample, net-of-cost return in EUR that is not
> explained by chance selection, not explained by reduced market exposure, and survives
> deflation by the honest count of every trial run in this spike.

The hypothesis is **rejected** when no variant clears criterion 1 of section 9. That is a
complete answer, and it is recorded as verdict (B). It is **undetermined** when a variant
clears criterion 1 but the statistics cannot separate edge from noise at the available
effective sample size; that is verdict (C), and (C) is never presented as a softened (A).

Momentum is not assumed to work. The single most likely way to fail this task is to keep
adjusting until something looks good, and the defence against that is this file's commit date.

### 2. The research boundary

Binance is the research venue of this spike and nothing else. No result computed here is
evidence that any strategy is executable on Kraken, or that this account may trade on Binance
at all. That question is separate and remains open. The `kraken_reality` cost regime in
section 6 exists so that a result which lives only because Binance is cheap is visible as
such; it is a sensitivity column and it is not a claim about executability either.

### 3. The account

| | |
|---|---|
| currency | EUR |
| equity | 1,500 |
| maximum concurrent positions | 10 |
| minimum-notional fraction | 0.25 |
| lot-rounding fraction | 0.01 |

Unchanged from SEXTANT-004 except for the position count, which rises from 8 to 10 because
the Binance cross-section is an order of magnitude wider than Kraken's and eight names out of
several hundred is a concentration choice rather than a capacity constraint. The change is
made here, before any data, and applies to every variant and every null equally.

### 4. Universe construction and quote policy

**Primary quote policy: USDT.** It is the venue's dominant quote, it carries by far the
longest and widest cross-section, and the account's own currency has a venue-native
conversion pair. **Secondary quote policy: EUR**, reported only if the EUR-quoted executable
universe is non-empty for at least 24 of the window's months and its median size is at least
5. Otherwise it is reported as *not evaluable* with the measured sizes, and no strategy is
run on it.

The admission rules, in order, are the Phase-0 rules already implemented in
`engine/universe/rules.py`, unchanged:

1. **quote currency** — the quote asset is in the policy set;
2. **sourced membership** — membership at the decision instant is established by the listing
   calendar. `UNDETERMINED` is neither admitted nor rejected; it is counted and reported;
3. **listing age** — at least 180 days;
4. **median quote volume** — trailing 30-day median turnover at least 250,000 quote units,
   over at least 20 observations;
5. **excluded asset class** — stablecoin bases, wrapped duplicates and staked derivatives, by
   the frozen list in `app/spike.py`, plus the leveraged-token rule below;
6. **minimum-notional feasibility** and **lot-size feasibility** against the account above.

**The leveraged-token rule.** Binance listed tokens whose return process is a daily-rebalanced
derivative of an underlying rather than an asset. A base asset whose ticker ends in `UP`,
`DOWN`, `BULL` or `BEAR` is excluded **only where the remaining stem is itself a base asset
present in the archive**, so that a genuine asset whose ticker happens to end in those letters
is not silently deleted. The stem test is what makes this a rule rather than a hand-curated
list.

**Membership comes from monthly file presence**, which is the same class of evidence
SEXTANT-003 used for Kraken and a considerably tighter one, because the venue publishes
monthly rather than quarterly. A symbol whose monthly file exists for month *M* was listed
during *M*. A symbol present in *M* and absent in *M+1* stopped trading strictly after the end
of *M* and at or before the end of *M+1*, recorded as the interval `(M.ends_at, M+1.ends_at]`
and never narrowed to a date. Listing is the same rule with presence reversed. Present, absent,
then present again is two spells, not one. A symbol present in the earliest held month is
unbounded before it; present in the latest, unbounded after it. **No instant is inferred from
the shape of a price series.** Provenance is `VENUE_ARCHIVE` throughout.

### 5. The window and the walk-forward structure

The window is fixed here as a **rule**, not as a pair of dates, because the dates are facts
about a dataset that does not exist yet. The rule consults listing metadata and FX
availability only; no return is read to resolve it, and the resolution is recorded in part 2
before any strategy runs.

**First usable month** is the earliest month start *T* such that a venue-sourced EUR
conversion rate exists for every day from *T* onward; *T* is at least 360 days after the first
day the archive holds any bar for the FX pair, so the longest lookback is satisfiable; and the
point-in-time executable universe is non-empty at *T* and at every subsequent rebalance.

**Last usable month end** is the end of the last month *M* for which the archive holds a
complete monthly file set for both *M* and *M+1*. *M+1* is required because a symbol absent
from the final published month cannot be distinguished from a symbol whose final file has not
been published yet, and treating the archive's edge as a delisting would turn a fact about the
download into a fact about the venue. That is the same discipline that excluded the
April-to-September 2026 tail from the Kraken window.

**Below 36 usable months this spike reports verdict (C) on sample size alone and runs no
strategy.** Twenty-four months gave a Sharpe standard error of 0.709 on Kraken, at which
nothing could be demonstrated at any realistic effect size. Repeating that would waste the
budget, and the threshold is set here so that the decision to stop cannot be talked out of
later.

Rebalances happen at the first instant of each calendar month, 00:00:00 UTC. The primary plan
is **walk-forward with 12 in-sample months and 4 non-overlapping out-of-sample folds**, each
fitted on everything before its own window and scored on nothing else. Fold length is
`(usable_months - 12) / 4` floored, with the remainder added to the final fold. The
`full_window` plan — 12 in-sample months, one fold — is reported alongside. There is no code
path anywhere in this system that scores a strategy on its fitting data.

### 6. Costs

Two cost regimes, both reported for every variant.

| regime | maker | taker | fill mixes | what it is |
|---|---:|---:|---|---|
| `binance_vip0` | 10 bps | 10 bps | one | the research venue's own published spot schedule, VIP 0, no BNB discount, no volume tier |
| `kraken_reality` | 40 bps | 80 bps | 100/0, 50/50, 0/100 | the schedule SEXTANT-004 used, carried as a sensitivity |

Maker and taker are equal at Binance VIP 0, so the fill mix has no effect on fees under the
primary regime and one cell is reported rather than three. Using the research venue's own
published schedule is not weakening a cost assumption; the other venue's schedule is carried
alongside precisely so that no result can hide behind the cheaper one.

**Spread and slippage are unchanged from SEXTANT-004**, on the same liquidity bands, cut at
5,000,000 and 1,000,000 quote units of trailing median daily turnover:

| band | spread (bps) | slippage (bps) |
|---|---:|---:|
| deep | 10 | 5 |
| mid | 25 | 10 |
| thin | 60 | 25 |
| unknown | 60 | 25 |

Both are **assumptions, not measurements**, and every report line says so. No historical quote
data is recoverable for either venue, and slippage is an intraday quantity that daily bars
cannot calibrate. Carrying the identical numbers across venues is deliberate: it removes any
question of the assumption having been retuned to suit a new dataset. Spread is charged on
every trade including those assumed to be maker fills.

Funding is 0 bps per day; this is spot and nothing is financed. The delisting haircut is
**20 per cent** of the position's value, charged to its own cost line.

**FX.** The foreign currency is USDT and the pair is `EURUSDT`, which is EUR priced in USDT on
the venue itself. The conversion is therefore EUR to USDT directly and **no
USDT-equals-USD assumption is needed anywhere** — a genuine improvement over the Kraken
EUR/USD leg. The rate the engine wants, EUR per USDT, is the reciprocal of the pair's daily
close, dated by bar close so that a rate is knowable only once the bar carrying it has
finished forming. Conversion is charged at 10 bps, once in and once out; the applicable
schedule is an assumption and is labelled as one. The `ignored` policy — foreign returns
credited as domestic with no conversion fee — is reported as a **counterfactual** and labelled
as intermediate wherever it appears, exactly as in `docs/NULL-BASELINE.md`.

### 7. Regime segmentation, computable point-in-time

Evaluated at each rebalance instant *R* on BTCUSDT converted to EUR, from bars closed at or
before *R*:

- `r30 = P(R) / P(R − 30d) − 1`
- `r90 = P(R) / P(R − 90d) − 1`
- `drawdown = 1 − P(R) / max(P over the trailing 365 days ending at R)`

Then, as a cascade, first match wins:

| | condition | label |
|---|---|---|
| 1 | `r30 ≤ −0.25` | **crash** |
| 2 | `drawdown ≥ 0.30` | **bear** |
| 3 | `drawdown ≥ 0.10` and `r90 > 0` | **recovery** |
| 4 | `r90 > 0` | **bull** |
| 5 | otherwise | **bear** |

Total and deterministic: every month gets exactly one label from information available at the
instant it is assigned, and a month's label is never revised by anything that happens
afterwards. Every headline result is reported per regime as well as overall, with the month
count and the effective independent observation count in each. A regime carrying fewer than 6
months is reported with its result and an explicit statement that nothing can be concluded
from it. A strategy that works in one regime has not been demonstrated; it has been fitted to
a market that happened.

### 8. The variants

Sixteen, in two shapes. Everything a reimplementer would otherwise have to decide is fixed
here. **Anything tested that is not in this list is a post-hoc trial**: it counts in full in
the registry, it requires a new pre-registration version committed before it is considered,
and the report lists it separately with the reason it was added.

#### 8.1 Fixed for every variant

| choice | value |
|---|---|
| signal instant | the rebalance instant *R*, first instant of the month, 00:00 UTC. Signal computed from daily bars whose `close_time ≤ R` — in practice the last day of the previous month |
| entry price | the last daily close that had finished forming at or before *R* |
| exit price | the same rule at the next rebalance instant |
| rebalance frequency | monthly |
| weighting | equal weight across the selected names |
| tie handling | ranks on the signal descending; ties broken by venue-native symbol ascending, byte-wise |
| insufficient data | no closed daily bar at both ends of the lookback, or fewer than 90 per cent of the daily bars in between, means no signal. Neither ranked nor held. Counted and reported as not-evaluable, never silently dropped |
| delisted instruments | a held instrument whose calendar says `NOT_LISTED` at the next rebalance is marked out at its last observed daily close **less a 20 per cent haircut**, charged to the delisting line. `UNDETERMINED` takes no haircut and is reported separately |
| staying in a position | positions carry across rebalances and across fold boundaries; only the difference between held book and target book is traded, and only that difference pays cost |

A holding period's return is therefore the move between two closes that were both observable
when the respective decisions were made, and the engine has no path to a price it could not
have seen.

#### 8.2 Cross-sectional (8 variants)

Signal, for instrument *i* at rebalance *R*:

```
s(i, R) = P(i, R) / P(i, R − L days) − 1
```

both endpoints taken from that instrument's own series of daily closes. Rank the whole
point-in-time executable universe by *s* descending and hold the top *N*, equally weighted. If
fewer than *N* instruments have an evaluable signal, hold all of them and leave the remainder
in cash. Fully invested whenever at least *N* signals exist; residual cash earns 0 per cent.

| | L (days) | N |
|---|---:|---:|
| XS-30-5 | 30 | 5 |
| XS-30-10 | 30 | 10 |
| XS-90-5 | 90 | 5 |
| XS-90-10 | 90 | 10 |
| XS-180-5 | 180 | 5 |
| XS-180-10 | 180 | 10 |
| XS-360-5 | 360 | 5 |
| XS-360-10 | 360 | 10 |

#### 8.3 Time-series long/flat (8 variants)

Each instrument is in or out on its own trend. Two signal rules:

```
return_sign:  s(i, R) = P(i, R) / P(i, R − L days) − 1
above_sma:    s(i, R) = P(i, R) / SMA(i, R, L) − 1
```

where `SMA(i, R, L)` is the arithmetic mean of the last *L* closed daily closes at or before
*R*. The instrument is held when `s > 0` and is flat otherwise. Of those passing, hold the
*N = 10* with the largest *s*, equally weighted; if fewer than 10 pass, hold all of them.
**The invested fraction is (number held) / 10 and the remainder is EUR cash earning 0 per
cent.** This is the family whose exposure varies, and it is the reason the decomposition in
the next section is mandatory rather than decorative.

| | L (days) | rule |
|---|---:|---|
| TS-30-ret | 30 | return_sign |
| TS-30-sma | 30 | above_sma |
| TS-90-ret | 90 | return_sign |
| TS-90-sma | 90 | above_sma |
| TS-180-ret | 180 | return_sign |
| TS-180-sma | 180 | above_sma |
| TS-360-ret | 360 | return_sign |
| TS-360-sma | 360 | above_sma |

### 9. Benchmarks, nulls and the decomposition

**All six benchmarks, for every strategy, always in EUR.** No table mixes currencies.

| id | definition |
|---|---|
| `eur_cash` | zero return, zero cost — the thing that beat everything on Kraken |
| `btc_buy_and_hold` | BTCUSDT held across the window in EUR, rebalanced monthly so it pays the same cost regime as everything else |
| `equal_weight_passive` | the entire point-in-time executable universe, equally weighted |
| `random_selection_fully_invested` | *N* names drawn uniformly without replacement at every rebalance, always fully invested — the outer sanity check |
| `exposure_matched_selection_null` | **per variant.** Draws uniformly without replacement exactly as many names as the variant held at that rebalance, leaving the same fraction in cash. The variant's exposure path, none of its selection |
| `timing_null` | **per variant.** Holds the equal-weight passive universe scaled to the variant's realised invested fraction, remainder in cash. The variant's timing, none of its selection |

Nulls are strategy-specific and **are themselves trials**. They are counted in
`research/trial-registry.jsonl` in full, because leaving them out is what makes a Deflated
Sharpe count dishonest in exactly the direction that flatters us.

Sampling is uniform **without replacement** within each rebalance, mirroring the constraint a
real strategy operates under: ten positions means ten distinct names. Seeds are consecutive
integers from 1. The exposure-matched null runs **500 seeds per cell**, the fully-invested null
**2,000 per cell**, in two cells: `binance_vip0`, and `kraken_reality` at the 50/50 fill mix.
If measured wall clock projects beyond the compute budget, the exposure-matched count steps
**down** the ladder 500 → 250 → 100 and the reduction is reported with the projection that
caused it. It is never stepped up. A smaller seed count only widens the confidence interval on
the threshold; it cannot flatter a result.

**The decomposition, three distinct numbers per variant per cell, never one:**

- **combined** — the variant's own net return series;
- **timing effect** — the timing null's series: the variant's realised invested fraction
  applied to the equal-weight passive universe;
- **selection effect** — the variant's own selected names, scaled to full investment. Same
  selection, no timing.

A win caused only by being less exposed to a falling market is not a win and must not be
reported as one. Where the combined result beats EUR cash and the selection effect does not,
the report says the variant timed the market and did not select.

### 10. Statistics

Walk-forward, out-of-sample only. Sharpe is annualised from monthly net returns in EUR.

**Deflated Sharpe** uses `bailey-lopez-de-prado-2014-ssrn-2460551` with the trial count read
from the registry **including null constructs**. That raises *K* and lowers every DSR, which is
the conservative direction and the one the brief requires; the strategy-only count is reported
beside it so the effect of the choice is visible. Trial variance is measured from the
random-selection distribution in the same cell rather than assumed.

**Bootstrap confidence intervals on every headline**: 10,000 resamples, 95 per cent two-sided,
generator seeded at 987654321.

**Effective observations, not the month count.** Both are reported, beside the raw count:

```
N_eff(time)  = N · (1 − ρ₁) / (1 + ρ₁),  ρ₁ = lag-1 autocorrelation of monthly net returns,
                                          clipped to [1, N]
k_eff(cross) = k / (1 + (k − 1) · ρ̄),    k = median positions held,
                                          ρ̄ = mean pairwise correlation of the daily EUR
                                          returns of the held names over the OOS window
```

The Kraken cross-section correlated 0.804 with BTC, so breadth never bought the independence
it was supposed to. A month count is not an observation count, and reporting it as one is how
a 24-month window came to look adequate.

**Where the statistics cannot distinguish edge from noise, the report says so and quantifies
how far short they fall**: the effect size that *would* have been detectable at the available
`N_eff`, and the `N_eff` that would have been needed for the observed effect. That is the most
likely outcome of this spike and it is a real answer.

### 11. Success criteria, numerically

All five must hold for at least one variant for verdict **(A)**.

| | criterion | test |
|---|---|---|
| 1 | beats its own exposure-matched null | OOS annualised net Sharpe in EUR, `binance_vip0` cell, **strictly greater** than the 95th percentile of that variant's own exposure-matched selection null in the same cell |
| 2 | survives deflation | **DSR ≥ 0.95** at the full registry trial count, same cell |
| 3 | the win is selection, not exposure | the selection effect's net return exceeds EUR cash **and** accounts for **at least 50 per cent** of the combined excess over EUR cash |
| 4 | regime stability | positive OOS net return in **at least 3 of the 4 regimes** carrying at least 6 months each, and in no regime a net return worse than `equal_weight_passive` over the same months |
| 5 | sign stability across cost regimes | net return keeps its sign across `binance_vip0` and all three fill mixes of `kraken_reality` |

**Verdict (B)** — insufficient evidence, close the trading project — when **no variant
satisfies criterion 1**. That is the cheapest filter and failing it is a complete answer.

**Verdict (C)** — undetermined — when at least one variant satisfies criterion 1 but not all
five; or the resolved window carries fewer than 36 months; or `N_eff` falls below 24 for every
variant that cleared criterion 1. (C) states precisely what is missing and what it would take
to resolve it.

### 12. The trial count this specification implies

| | count |
|---|---:|
| strategy variants | 16 |
| cost cells per variant | 4 |
| **strategy trials** | **64** |
| deterministic benchmark cells | 16 |
| fully-invested null cells | 2 |
| exposure-matched null cells | 32 |
| timing null cells | 32 |
| **expected total** | **146** |

A seeded null distribution is **one** trial carrying many seeds, recorded with its seed count,
exactly as SEXTANT-004 recorded it. The registry's own count is authoritative at report time;
this figure is the pre-registered expectation, stated now so that a divergence is visible.

### 13. What would make this specification wrong

Stated in advance, so that the response to it is a procedure and not a judgement call. If an
anomaly, a data limitation or a hypothesis emerges that forces a design change — a cost
assumption that is genuinely wrong, a universe rule that admits something it should not, an FX
pair that turns out not to cover the window — the change gets a **new pre-registered version**
with a new commit, everything affected is rerun, and **both results are retained**. A
pre-registration is never edited in place after a number has been seen.

---

## Part 2 — the dataset

*Not yet written. Committed separately, after acquisition, with the archive's real facts in
it: the exact public source, the period covered, the timeframes, the symbols, the size, the
minimum set needed to run every variant above and nothing beyond it, the checksums, the usable
window after the listing-age cold start, and the state of the delisting record over it.*

*No strategy result is produced before this section exists.*

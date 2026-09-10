# SEXTANT-006 F1 pre-registration: funding, basis and carry

**Version `v1`. Part 1, the specification, committed before any funding number was
computed and before a single object of the futures archive had finished downloading.**

Two parts, committed separately, for the same reason SEXTANT-005 split its own: the
specification needs no data and must be fixed before any is seen, while the dataset section
can only be written once acquisition has happened. **Part 1 is below and needs no data at
all. Part 2 is the dataset section**, filled in with the acquisition's real facts and
committed before any strategy result exists.

`config/spike-006-f1.yaml` is the machine-readable half of this document. The runner asserts
that the two agree with the code and refuses to start if they have drifted, the same guard
`config/spike-005.yaml` carries.

## What was consulted before this part was written

Stated because a pre-registration's credibility rests on it, and because the honest answer is
not "nothing".

**Listing metadata, in full.** The set of symbol directories the futures archive publishes,
the months each one published in each of three trees, and the byte sizes of those objects.
That is `docs/DATA-FEASIBILITY-006.md` and every count in it.

**One integrity check, on one symbol, on one month.** `BTCUSDT-fundingRate-2024-03.zip` was
read and compared row by row against the venue's own `/fapi/v1/fundingRate` endpoint, to
establish that `calc_time` is the settlement instant rather than the start of the interval.
That check required looking at ten funding rates, and their values were seen.

**Four symbols, four months, one summary statistic each.** While confirming that the
`funding_interval_hours` column is not constant, the *mean* funding rate over the month was
printed for BTCUSDT, ETHUSDT, SOLUSDT and DOGEUSDT at 2021-06, 2023-06, 2025-06 and 2026-06.
Sixteen numbers, each a monthly mean, each between −0.000043 and +0.000067 per settlement.
**This is prior exposure to the dependent variable and it is disclosed rather than
minimised.** What it establishes is an order of magnitude: raw funding on majors is single
digit percent annualised. What it cannot establish, and what nothing below was chosen
against, is any variant's return, any cross-sectional ranking, or any month outside those
four.

No backtest was run, no equity curve existed, and no return of any construction below was
computed before this file was committed.

---

## Part 1 — the specification

### 1. The hypothesis, stated so it can be refuted

> Over the evaluation window and across the regimes it contains, at least one of the eight
> variants specified below earns an out-of-sample, net-of-cost return in EUR that is
> **positive**, that exceeds what choosing carry pairs at random would have earned at the same
> exposure, that is attributable to selection rather than to exposure, and that survives
> deflation by the honest count of every trial run in this task.

**The economic reason to expect anything at all.** A perpetual future has no expiry, so
nothing forces it to converge on the spot price. The funding payment is what does: when the
perpetual trades above the venue's index the longs pay the shorts, and when it trades below
the shorts pay the longs. Crypto has a structural excess of leveraged long demand, so funding
is expected to be positive on average. A position that is long the spot asset and short the
perpetual on the same asset in the same base quantity has approximately no exposure to the
asset's price: what it holds is the *difference* between the two prices, plus the funding
stream. If the stream is positive on average and larger than the cost of opening, holding and
closing both legs, the construction earns without predicting direction.

That is a real hypothesis with a real mechanism, and it is also a well-known one, which cuts
both ways: a widely known trade is a trade whose edge has had years of capital pointed at it.

The hypothesis is **rejected** when no variant clears criterion 1 of section 11. That is a
complete answer and is recorded as verdict (B) for this family. It is **undetermined** when a
variant clears criterion 1 but the statistics cannot separate edge from noise at the available
effective sample size; that is (C), and (C) is never presented as a softened (A).

### 2. The research boundary

Binance is the research venue and nothing else. No result computed here is evidence that any
construction is executable on Kraken, or that this account may trade futures on Binance at
all. Kraken publishes no comparable funding archive; that is a fact about Kraken's data, not
about its markets, and it is recorded rather than worked around.

### 3. The trial budget, declared before anything runs

**F1's budget is 8 variants across 4 cost cells: 32 strategy trials. That is the whole
allowance and it is also the whole registration.** Every variant is listed in section 9. If
none of the eight clears the criteria in section 11, **F1 is closed**: no ninth variant, no
adjusted parameter, no additional lookback, no "one more cell to check something".

Registering exactly the budget rather than registering fewer and reserving the rest is
deliberate. A reserve is an invitation to spend it after seeing a result, which is the
behaviour the budget exists to prevent. Anything tested in this family beyond the eight is
post-hoc by construction, counts in full in the registry, requires a new pre-registered
version committed before it is considered, and is reported in its own section with the reason
it was added.

Nulls and benchmarks are **not** charged against the variant budget, because they are not
attempts at finding an edge. They are counted in full in the trial registry, which is what
the Deflated Sharpe Ratio reads.

### 4. The account

| | |
|---|---|
| currency | EUR |
| equity | 1,500 |
| maximum concurrent carry pairs | 10 |
| initial margin fraction on the futures leg | 0.20 |
| maintenance margin fraction, for the liquidation study only | 0.005 |

Equity is unchanged from SEXTANT-004 and SEXTANT-005 so that a return in this task and a
return in that one are returns on the same account.

**The margin fraction is an assumption and is labelled as one everywhere.** 0.20 means the
futures leg is opened at five times leverage against the capital set aside for it, which is
far inside what the venue permits on a major and is chosen to be conservative rather than
representative. The venue does not publish historical margin tiers, so no measured value
exists; today's bracket endpoint describes survivors only, and using it would apply live
contracts' limits to dead ones. 0.005 for maintenance is the figure the venue publishes today
for its lowest tier on majors, carried as a stated assumption for the liquidation arithmetic
in section 13 and used for nothing else.

### 5. What a carry position is, exactly

This section exists so that a competent stranger reimplements the same thing.

**A carry position in asset A at rebalance R is a pair:** long `q` units of the USDT-quoted
**spot** pair for A, and short `q` units of the USDT-quoted **perpetual** for A. The same
`q`, in the same base asset, on both legs.

Equal base quantity and not equal notional. With equal quantity the pair's profit is

```
q · [ (S₁ − P₁) − (S₀ − P₀) ]  +  Σ funding received
```

which is the change in the basis plus the funding stream, and has no term in the asset's own
price. Equal notional would leave a residual delta of exactly the size of the basis, which is
the quantity the construction is supposed to be isolating. At entry the two prices differ by
a fraction of a percent, so the two sizings are nearly identical in practice; they are not
identical in principle, and the principle is what is registered.

**Capital.** Let `s` be the notional of one leg at entry. The pair consumes `s` of cash for
the spot leg and `0.20 · s` of margin for the futures leg, so it consumes `1.20 · s` of the
account. At **1x**, which is what every variant in section 9 is run at, the sum of `1.20 · sᵢ`
across held pairs is at most the account's equity. With `N` pairs held that gives each leg a
notional of `equity / (1.20 · N)`, and the residue earns nothing.

**Funding accrual.** The short perpetual leg receives `notional × rate` at every settlement
instant strictly inside the holding period, where `notional` is the leg's notional marked at
the most recent daily close at or before that instant, and `rate` is the venue's published
`last_funding_rate` at that instant. A positive rate is a receipt to a short. There is no
scaling by the interval: the rate published for a settlement is the rate applied at that
settlement, whatever interval it covered.

**A settlement with no published rate is not a zero.** If any settlement instant inside a
holding period has no funding object covering it, the pair is **not evaluable** for that
period: it is not opened, or if already open it is closed at the start of the period. The
count of such refusals is reported. Filling a missing funding file with zero would fabricate
exactly the quantity this family measures.

**Delisting.** If either leg's calendar says the instrument is gone at the next rebalance,
the pair is closed at each leg's last observed daily close. The **spot leg takes the 20 per
cent haircut** carried unchanged from SEXTANT-005 and charged to its own cost line; the
**futures leg takes none**, because the venue settles a delisted perpetual against an index
rather than dumping it. That combination is the pessimistic one for this family — the two
haircuts would otherwise cancel across the pair — and it is registered because it is
pessimistic, not despite it. The number of pairs it strikes is reported.

### 6. Universe construction

**Quote policy: USDT on both legs.** There is no EUR-quoted perpetual on this venue, so the
secondary EUR policy SEXTANT-005 ran has no counterpart here and is not registered. The
account's currency is still EUR and every reported figure is still in EUR; the conversion is
the same `EURUSDT` leg described in section 8.

An asset A is in the **carry universe at rebalance R** when every one of the following holds,
evaluated only on information available at R:

1. **paired symbols exist** — the venue publishes both `AUSDT` spot and `AUSDT` perpetual, by
   **exact base-asset match**. A scaled-unit perpetual such as `1000PEPEUSDT` is **excluded**,
   because pairing it with `PEPEUSDT` spot requires a unit-conversion rule, and a rule
   invented here is a degree of freedom exercised after the data was seen. The count of
   assets lost to this is reported;
2. **sourced membership on both legs** — the spot listing calendar and the perpetual listing
   calendar both say `LISTED` at R. `UNDETERMINED` is neither admitted nor rejected; it is
   counted and reported;
3. **listing age** — the perpetual's earliest sourced listing instant is at least 180 days
   before R;
4. **funding evaluability** — the perpetual has a published funding settlement for **every**
   settlement instant in the trailing 30 days. A gap disqualifies;
5. **median quote volume** — the perpetual's trailing 30-day median daily quote turnover is
   at least 250,000 USDT, over at least 20 observations;
6. **bar coverage** — both legs have a closed daily bar at both ends of the longest lookback
   the variant uses, and at least 90 per cent of the daily bars in between;
7. **excluded asset class** — stablecoin bases, wrapped duplicates and staked derivatives, by
   the frozen list already in `app/spike.py`, plus the `UP`/`DOWN`/`BULL`/`BEAR` stem rule.

Rules 3, 5 and 7 are carried across from SEXTANT-005 **unchanged and at the same thresholds**,
so that no result here can be attributed to a universe screen that was retuned for a new
dataset. Rules 1 and 4 are new and exist because a paired construction and a funding stream
are new.

### 7. The window and the walk-forward structure

Fixed as a **rule**, not as dates, because the dates are facts about a dataset that had not
finished downloading when this was written. The rule consults listing metadata and FX
availability only.

**First usable month** is the earliest month start *T* such that a venue-sourced EUR
conversion rate exists for every day from *T* onward; *T* is at least 300 days after the first
day the archive holds a bar for the FX pair, so that the 180-day listing age, the 90-day
longest lookback and the 30-day turnover window are all satisfiable with a ragged-edge month
to spare; and the carry universe is non-empty at *T* and at every subsequent rebalance.

**Last usable month end** is the end of the last month *M* for which the archive holds a
complete monthly object set for both *M* and *M+1*, in every tree the family reads. *M+1* is
required because a symbol absent from the final published month cannot be distinguished from
a symbol whose final object has not been published yet.

**Below 36 usable months this family reports verdict (C) on sample size alone and runs no
variant.** Same floor as SEXTANT-005, and set here before the window was resolved so the
decision to stop cannot be talked out of later.

Rebalances happen at the first instant of each calendar month, 00:00:00 UTC. The plan is
**walk-forward with 12 in-sample months and 4 non-overlapping out-of-sample folds**, fold
length `(usable_months − 12) / 4` floored, remainder left off the end — which is what the
engine's shared plan builder actually does, recorded here rather than discovered as a
deviation later. Positions carry across folds; only the last fold liquidates.

**Nothing is fitted.** Every variant's parameters are fixed in section 9 and no procedure
chooses among them. The walk-forward structure is kept anyway, unchanged, because the
benchmarks and nulls run through it and a construct measured over a different window from the
thing it benchmarks is not a benchmark. The first twelve months are held back and never
scored.

### 8. Costs

Four cells, every variant reported in all four. Fees differ by leg, which is new: the venue
charges spot and futures on different schedules.

| cell | spot maker/taker | futures maker/taker | fill mix | what it is |
|---|---|---|---|---|
| `vip0_maker` | 10 / 10 bps | 2 / 5 bps | 100 / 0 | the venue's published VIP 0 schedules, both legs assumed to rest |
| `vip0_even` | 10 / 10 bps | 2 / 5 bps | 50 / 50 | **the headline cell.** Criteria are judged here |
| `vip0_taker` | 10 / 10 bps | 2 / 5 bps | 0 / 100 | both legs assumed to cross |
| `stress` | 10 / 10 bps | 2 / 5 bps | 0 / 100 | taker, **and spread and slippage at twice the assumption on both legs** |

The headline is the middle cell rather than the cheapest. SEXTANT-005 judged at the venue's
own schedule because maker and taker were equal there and the fill mix could not move the
answer; here they are not equal, so the fill mix is a live assumption and the headline must
not sit at the flattering end of it.

**Spread and slippage are unchanged from SEXTANT-005**, on the same liquidity bands, cut at
5,000,000 and 1,000,000 quote units of trailing median daily turnover:

| band | spread (bps) | slippage (bps) |
|---|---:|---:|
| deep | 10 | 5 |
| mid | 25 | 10 |
| thin | 60 | 25 |
| unknown | 60 | 25 |

Both are **assumptions, not measurements**, and every report line says so. Carrying the
identical numbers across a third dataset is deliberate: it removes any question of the
assumption having been retuned. **They are charged on both legs**, which for a carry pair
means a round trip costs twice what a single-leg strategy pays — the central reason a small
carry can fail to survive.

Section 12 registers a bounded, rule-fixed sample of the venue's own `bookTicker` archive that
will say whether these figures are generous or mean. That measurement sits **beside** the
assumption and never replaces it.

**Funding is not a cost line here, it is the return.** It is accounted per settlement per leg
as section 5 describes, reported as its own line in every table, and never blended into fees.

**FX.** The foreign currency is USDT and the pair is `EURUSDT` on the venue itself, so the
conversion is EUR to USDT directly and no USDT-equals-USD assumption is needed. The rate is
the reciprocal of the pair's daily close, dated by bar close. Conversion is charged at 10 bps,
once in and once out. Unchanged from SEXTANT-005.

### 9. The eight variants

Everything a reimplementer would otherwise have to decide is fixed here. All are run at 1x as
section 5 defines it.

#### 9.1 Fixed for every variant

| choice | value |
|---|---|
| signal instant | the rebalance instant *R*, 00:00 UTC on the first of the month |
| signal inputs | funding settled **at or before** *R*; daily closes whose `close_time ≤ R` |
| entry price, both legs | each leg's last daily close that had finished forming at or before *R* |
| exit price | the same rule at the next rebalance |
| rebalance frequency | monthly |
| sizing | equal capital across the selected pairs, each leg `equity / (1.20 · N)` in notional |
| tie handling | ranks descending; ties broken by base asset ascending, byte-wise |
| insufficient data | any rule in section 6 failing means the asset is not in the universe; counted and reported as not-evaluable, never silently dropped |
| carrying positions | pairs carry across rebalances and folds; only the difference between held book and target book is traded, and only that difference pays cost |

`trailing_funding(A, R, L)` is the **sum** of every funding rate settled in the *L* days
ending at *R* for A's perpetual. A sum rather than a mean because it is the quantity actually
received over the period, and because a mean would need a divisor that the varying settlement
interval makes ambiguous.

`premium(A, R)` is A's premium-index daily close for the last bar closed at or before *R*.

#### 9.2 The variants

| id | selection rule | N |
|---|---|---:|
| `carry-basket-5` | the 5 assets in the carry universe with the largest trailing 30-day median quote turnover | 5 |
| `carry-basket-10` | the 10 assets with the largest trailing 30-day median quote turnover | 10 |
| `carry-rank30-5` | the 5 assets with the largest `trailing_funding(A, R, 30)` | 5 |
| `carry-rank30-10` | the 10 assets with the largest `trailing_funding(A, R, 30)` | 10 |
| `carry-rank90-5` | the 5 assets with the largest `trailing_funding(A, R, 90)` | 5 |
| `carry-rank90-10` | the 10 assets with the largest `trailing_funding(A, R, 90)` | 10 |
| `carry-premium-10` | the 10 assets with the largest `premium(A, R)` | 10 |
| `carry-positive-10` | the assets with `trailing_funding(A, R, 30) > 0`, the 10 largest of them; **if fewer than 10 qualify, hold only those and leave the rest in cash** | ≤ 10 |

The eight are three questions and no more. `basket` asks whether carry pays at all, with no
selection: it holds the most liquid assets and would hold them whatever funding did.
`rank30`, `rank90` and `premium` ask whether choosing *which* assets to carry adds anything,
on three different signals of the same underlying quantity. `positive` is the only variant
whose exposure varies, and it exists so that the decomposition in section 10 has something to
decompose: it is the one that can stand aside, and it is therefore the one that must beat its
own timing null.

There is no threshold sweep, no lookback ladder beyond two points, and no weighting scheme
other than equal. Each of those would be a plausible thing to try and each would spend budget
on a variant with no distinct hypothesis behind it.

### 10. Benchmarks, nulls and the decomposition

**All six benchmarks, for every variant, always in EUR.** No table mixes currencies.

| id | definition |
|---|---|
| `eur_cash` | zero return, zero cost |
| `btc_buy_and_hold` | BTCUSDT **spot** held across the window in EUR, paying the same cost regime |
| `equal_weight_passive` | the entire point-in-time carry universe held **long spot only**, equally weighted. Deliberately the long-only benchmark: it is what the same capital would have done without the short leg |
| `random_selection_fully_invested` | *N* carry pairs drawn uniformly without replacement at every rebalance, always fully invested — the outer sanity check |
| `exposure_matched_selection_null` | **per variant.** Draws uniformly without replacement exactly as many carry pairs as the variant held at that rebalance, leaving the same fraction idle. The variant's exposure path, none of its selection. **This is the null that matters for this family** |
| `timing_null` | **per variant.** Holds the whole carry universe equally weighted, scaled to the variant's realised invested fraction. The variant's timing, none of its selection |

Nulls are strategy-specific and **are themselves trials**, counted in
`research/trial-registry.jsonl` in full. Sampling is uniform without replacement within each
rebalance. Seeds are consecutive integers from 1. The exposure-matched null runs **500 seeds
per cell**, the fully-invested null **2,000**, in two cells: `vip0_even` and `stress`. If
measured wall clock projects beyond the compute budget the exposure-matched count steps
**down** 500 → 250 → 100 and the reduction is reported with the projection that caused it. It
is never stepped up.

**The decomposition, four numbers per variant per cell, never one.** Three are SEXTANT-005's;
the fourth is new and is the point of this family.

- **combined** — the variant's own net return series;
- **timing effect** — the timing null's series;
- **selection effect** — the variant's own selected pairs, scaled to full investment;
- **funding contribution** — the sum of all funding received and paid, as a share of the
  combined net return, reported beside the basis contribution and the cost lines so that a
  reader can see whether the return came from the funding stream, from basis convergence, or
  from something that should not have been there at all.

### 11. Success criteria, numerically

All five must hold for at least one variant for verdict **(A)** in this family.

| | criterion | test |
|---|---|---|
| 1 | **positive, and beats its own exposure-matched null** | OOS annualised net Sharpe in EUR, `vip0_even` cell, **strictly greater** than the 95th percentile of that variant's own exposure-matched null in the same cell, **and** OOS net return **strictly greater than zero** |
| 2 | survives deflation | **DSR ≥ 0.95** at the full registry trial count, same cell |
| 3 | the win is selection, not exposure | the selection effect's net return exceeds EUR cash **and** accounts for **at least 50 per cent** of the combined excess over EUR cash |
| 4 | regime stability | positive OOS net return in **at least 3 of the regimes carrying at least 6 months each**, and in no such regime a net return worse than `equal_weight_passive` over the same months |
| 5 | sign stability across cost regimes | net return keeps its sign across all four cells of section 8 |

**Criterion 1 carries a sign condition that SEXTANT-005's did not, and this is the one
substantive change to the criteria.** SEXTANT-005 recorded, in its own verdict, that its
criterion 1 misfired: in a cross-section where almost everything fell, a variant could clear
"beats the 95th percentile of its own null" by *losing less than chance lost*, and two
variants did, one of them while destroying 78.89 per cent of the account. The defect was
recorded rather than corrected there, because a pre-registration is never edited after a
number has been seen. It is corrected **here**, in a new pre-registration written before any
number, and the correction makes the criterion **stricter**. No criterion in this task is
looser than SEXTANT-005's.

**Verdict (B) for this family** — no edge — when **no variant satisfies criterion 1**.

**Verdict (C) for this family** — undetermined — when at least one variant satisfies
criterion 1 but not all five; or the resolved window carries fewer than 36 months; or `N_eff`
falls below 24 for every variant that cleared criterion 1. (C) states precisely what is
missing and what it would take to resolve it.

### 12. The spread measurement, fixed by rule now

The venue publishes `bookTicker` — best bid and best ask, tick by tick — for its perpetuals,
but only from **2023-05-16 to 2024-03-30**, at 50 to 90 MB per symbol per day. That is enough
to *measure* the spread on a slice and far too much to measure it on the window.

**The sample is fixed here, before any byte of it is read, and cannot be reselected:**

- **Days.** The first trading day of every other calendar month in the available range:
  2023-06-01, 2023-08-01, 2023-10-01, 2023-12-01, 2024-02-01, plus 2023-05-16, the first day
  the archive holds. Six days.
- **Symbols.** Six perpetuals, two per liquidity band, chosen by the same trailing 30-day
  median quote turnover the cost model's bands are cut on, evaluated at **2023-05-15**: the
  two highest-turnover members of the carry universe at that instant, the two nearest its
  median, and the two nearest its 10th percentile. Ties broken by symbol ascending.
- **Statistic.** For each symbol-day, the time-weighted mean and the median of
  `(ask − bid) / midpoint` in basis points, and the same two figures restricted to
  00:00–00:05 UTC, the five minutes around the rebalance instant.

**What it can and cannot say.** It measures the *quoted* spread on the perpetual leg, on six
days, in one eleven-month stretch of a longer window, and says nothing about the spot leg,
about slippage, about the other 2,000-odd days, or about what a 150 EUR order would actually
have paid. It is reported as a measurement over that stated slice, beside the configured
assumption, and **the assumption is what every variant is costed at**. Invariant 12 is not
weakened by measuring something; it would be weakened by reporting the measurement as though
it were the cost model, and it will not be.

This measurement is **not** a trial and consumes no variant budget. It changes no number in
any result table.

### 13. Leverage, and the honest limit on it for this family

Stage 3 of the brief sweeps leverage for any family clearing its criteria at 1x. For a
cash-and-carry pair that sweep is **partly unavailable, and the reason is a data fact rather
than a choice**.

Levering a cash-and-carry means borrowing to buy more spot. The rate on that borrow is a
margin-lending rate that this venue does not publish historically; today's endpoint describes
today. **A leverage sweep on the spot leg would therefore be a sweep over an invented number,
and it will not be run.** That is reported as a limitation of the family, not hidden.

What *is* computable, and will be reported if F1 clears 1x:

- **the margin buffer sweep.** The futures leg's initial margin fraction at 0.50, 0.20, 0.10
  and 0.05 — two, five, ten and twenty times leverage on that leg alone — which changes how
  much capital the pair ties up and therefore the return on capital, and changes the distance
  to a liquidation price. Return, drawdown, Sharpe, Sortino, capital utilisation and
  sensitivity to fees, funding and slippage at each;
- **liquidation risk and probability of ruin**, from the empirical distribution of the pair's
  own daily basis moves against the maintenance margin of section 4. **Not a normal
  approximation.** SEXTANT-005 measured skewness 2.46 and kurtosis 10.36 on this asset class,
  where a Gaussian assumption understates the tail by a wide margin precisely where it
  matters.

**Leverage is never a way to turn a losing family into a winning one.** If F1 does not clear
criterion 1 at 1x, none of the above is run and none of it is reported.

### 14. Statistics

Walk-forward, out-of-sample only. Sharpe annualised from monthly net returns in EUR.

**Deflated Sharpe** uses `bailey-lopez-de-prado-2014-ssrn-2460551` with the trial count read
from the registry **including null constructs and including every trial from every family in
this task, and every trial from SEXTANT-004 and SEXTANT-005 that the registry already holds**.
That is the honest count and it is a large one. The strategy-only count and the
this-family-only count are reported beside it so that the effect of the choice is visible, and
so that **the deflation this task's wider search costs can be read directly**: the bar at
SEXTANT-005's trial count and the bar at this task's are both stated.

**Bootstrap confidence intervals on every headline**: 10,000 resamples, 95 per cent
two-sided, generator seeded at 987654321.

**Effective observations, not the month count**, by the same two formulas SEXTANT-005 used,
both reported beside the raw count. For a carry book the cross-sectional one matters
differently: two carry pairs are two bets on two *bases*, and bases can be far less correlated
than the underlying prices are. Whether they are is measured, not assumed, and it is one of
the few places this family could genuinely differ from the last.

**Where the statistics cannot distinguish edge from noise, the report says so and quantifies
how far short they fall**: the effect size that would have been detectable at the available
`N_eff`, and the `N_eff` that would have been needed for the observed effect.

### 15. What would make this specification wrong

Stated in advance so the response is a procedure and not a judgement call. If a data
limitation or an anomaly forces a design change — a funding column that does not mean what
section 5 says, an FX pair that does not cover the window, a universe rule that admits
something it should not — the change gets a **new pre-registered version** with a new commit,
everything affected is rerun, and **both results are retained**. This file is never edited in
place after a number has been seen.

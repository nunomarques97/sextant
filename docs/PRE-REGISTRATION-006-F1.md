# SEXTANT-006 F1 pre-registration: funding, basis and carry

**Version `v2.2`. Part 1, the specification, committed before any funding number was
computed and before a single object of the futures archive had finished downloading.
Amendments 1 to 6 were all added before any variant had been run; no strategy result of any kind
existed when any of them was written. Sections 16, 17, 18, 26, 27 and 28 state what they changed
and why. Amendments 4, 5 and 6 were written after part 2 and change nothing part 2 measured.
Amendment 6 is the only one that changes the grid, and it is the last: the grid closed when the
runner started. `v1.7.1` adds section 28.6, which resolves what 28.2 left implicit about the months
between quarterly rebalances. It changes no registered choice, adds no variant and was committed
before the runner produced a number. `v1.8` adds section 29, the rule for how a void
execution is counted, written after the first execution was found void and before any figure of
the second had been read. `v1.9` adds section 30: it fixes the *role* that may declare a run void
as the Product Owner and never the Developer, and it makes section 12's spread sample conditional
on rule S1, a computed condition on the result file. Both were written before any figure of the
second execution had been read. `v2.0` adds section 31, which generalises rule S1 so it governs
every family and records two findings F1 measured. **Unlike every amendment before it, `v2.0` was
registered after this family's figures had been read**, on the Product Owner's instruction, and it
changes F1's acquisition answer from no to yes. Section 31.4 states why that cannot move an F1
result. `v2.1` adds section 32: it records the drafting defect in amendment 9 rather than repairing
it, gives rule S1 a floor **from F2**, and makes the measured spread the default cost **from F2**.
`v2.2` adds section 33: it anchors that floor to **zero** rather than to the null, registers rule P1
so the same defect cannot recur in a fourth place, strengthens criterion 1 **from F2**, and replaces
the banded spread assumption **from F2** with an estimator that has to pass a test written before it
was run. No amendment in this document changes an F1 variant, cell, criterion, threshold, budget or
verdict letter.**

Two parts, committed separately, for the same reason SEXTANT-005 split its own: the
specification needs no data and must be fixed before any is seen, while the dataset section
can only be written once acquisition has happened. **Part 1 is below and needs no data at
all. Part 2, sections 19 to 25, is the dataset section**, filled in with the acquisition's real
facts and committed before any strategy result existed.

`config/spike-006-f1.yaml` is the machine-readable half of this document. The runner asserts
that the two agree with the code and refuses to start if they have drifted, the same guard
`config/spike-005.yaml` carries.

> **Extended by 18.** The configuration now also carries the **enforced** trial budget, and the
> runner refuses to start unless the configuration is committed and clean. The report cites the
> configuration's commit SHA beside the results' commit SHA so a reader can verify the ordering
> without taking anyone's word for it.

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

> **Given a mechanism by 18.1.** The allowance above was prose, and prose does not refuse. It
> now lives in `config/spike-006-f1.yaml` as data and is charged by the runner **before** each
> engine run. An unregistered variant, an unregistered cost cell, and a charge past the
> allowance each raise. Nulls remain uncharged, for the reason stated above.

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

> **Amended by 16.3.** The original text above stands as written and is left unedited. What
> 16.3 adds is that the venue does not publish its maintenance-margin tiers *at all* without a
> credential, that 0.005 is therefore a published figure carried as an assumption rather than
> a datum, that applying it to 2020 is a look-ahead, and that the bias is **optimistic**. The
> liquidation study is consequently reported at three settings, not one.

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
| rebalance frequency | monthly for the eight variants of 9.2; **quarterly** for `carry-rank90-10-quarterly` (amendment 6, section 28) |
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

| id | selection rule | N | rebalance months |
|---|---|---:|---:|
| `carry-basket-5` | the 5 assets in the carry universe with the largest trailing 30-day median quote turnover | 5 | 1 |
| `carry-basket-10` | the 10 assets with the largest trailing 30-day median quote turnover | 10 | 1 |
| `carry-rank30-5` | the 5 assets with the largest `trailing_funding(A, R, 30)` | 5 | 1 |
| `carry-rank30-10` | the 10 assets with the largest `trailing_funding(A, R, 30)` | 10 | 1 |
| `carry-rank90-5` | the 5 assets with the largest `trailing_funding(A, R, 90)` | 5 | 1 |
| `carry-rank90-10` | the 10 assets with the largest `trailing_funding(A, R, 90)` | 10 | 1 |
| `carry-premium-10` | the 10 assets with the largest `premium(A, R)` | 10 | 1 |
| `carry-positive-10` | the assets with `trailing_funding(A, R, 30) > 0`, the 10 largest of them; **if fewer than 10 qualify, hold only those and leave the rest in cash** | ≤ 10 | 1 |
| `carry-rank90-10-quarterly` | the 10 assets with the largest `trailing_funding(A, R, 90)`, at quarterly rebalance instants only — **amendment 6, section 28** | 10 | 3 |

The first eight are three questions and no more. `basket` asks whether carry pays at all, with no
selection: it holds the most liquid assets and would hold them whatever funding did.
`rank30`, `rank90` and `premium` ask whether choosing *which* assets to carry adds anything,
on three different signals of the same underlying quantity. `positive` is the only variant
whose exposure varies, and it exists so that the decomposition in section 10 has something to
decompose: it is the one that can stand aside, and it is therefore the one that must beat its
own timing null.

**`carry-rank90-10-quarterly` is the fourth question, added by amendment 6 and asked at exactly
one point: does cadence matter?** It is `carry-rank90-10` with a quarterly rebalance and nothing
else changed, so the two form a one-factor comparison and are reported as a pair. Section 28 states
why the question could not be left out and what the addition costs.

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

All five must hold for at least one variant for verdict **(A)** in this family — **and, since
Amendment 1, so must criterion 6.** Six requirements rather than five is a strictly higher bar
than the one first registered, which is the only direction a criterion in this task may move.

| | criterion | test |
|---|---|---|
| 1 | **positive, and beats its own exposure-matched null** | OOS annualised net Sharpe in EUR, `vip0_even` cell, **strictly greater** than the 95th percentile of that variant's own exposure-matched null in the same cell, **and** OOS net return **strictly greater than zero** |
| 2 | survives deflation | **DSR ≥ 0.95** at the full registry trial count, same cell |
| 3 | the win is selection, not exposure | the selection effect's net return exceeds EUR cash **and** accounts for **at least 50 per cent** of the combined excess over EUR cash |
| 4 | regime stability | positive OOS net return in **at least 3 of the regimes carrying at least 6 months each**, and in no such regime a net return worse than `equal_weight_passive` over the same months |
| 5 | sign stability across cost regimes | net return keeps its sign across all four cells of section 8 |
| 6 | **added by 16.2** — the edge is not confined to the early window | the most recent 24 scored months, evaluated alone, satisfy criterion 1 in the headline cell |

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

> **See also 16.4**, which registers a second measurement of the same kind: the daily
> `bookDepth` tree, which the monthly trees do not carry, turns capacity from a turnover
> inference into a depth measurement over a stated slice.

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

> **Given a mechanism by 18.2.** "Never edited in place after a number has been seen" was an
> assertion nothing could check. The runner now refuses to start unless the configuration is
> committed and clean, and the report states whether the configuration's commit is an ancestor
> of the commit that introduced the results. A result whose ordering does not verify may not be
> reported as pre-registered.

---

## 16. Amendment 1 — decay, and the margin schedule that does not exist

**Added on the Sponsor's instruction while the acquisition was still downloading. No variant
had been run, no equity curve existed, and no funding number beyond the sixteen monthly means
already disclosed at the top of this file had been computed. The amendment makes the
specification stricter in both places it touches; nothing in it relaxes a criterion, widens a
grid, or adds a variant, and the trial budget of section 3 is unchanged at eight variants and
thirty-two strategy trials.**

### 16.1 The decay expectation, declared before the grid runs

**The Sponsor's hypothesis, stated here so the result can refute it.** Funding carry is a
widely known trade and has had capital pointed at it since roughly 2021. An average taken
across 2020 to 2026 may therefore be dominated by an early period that no longer exists, and
an edge that has already been arbitraged away is a different answer from an edge that
persists — different enough that it changes what should be built, which is why it must be
visible before anything is.

This is a real prediction with a direction, and it is registered as one.

> **Declared expectation D1.** The mean monthly net return of the two most recent complete
> scored calendar years is **lower** than that of the two earliest complete scored calendar
> years, for the best variant in the headline cell.

D1 is confirmed, refuted or left unresolved by the numbers below. It carries no weight in the
verdict by itself: it is stated so that a decay narrative told after the fact can be checked
against a prediction made before it.

**What is reported, for every variant, in the headline cell.**

A **per-calendar-year table**, alongside and not instead of the per-regime one:

| column | definition |
|---|---|
| scored months | how many of that year's months fall inside a scored fold |
| net return | compounded net return in EUR over that year's scored months |
| funding contribution | funding received less funding paid over those months |
| basis contribution | the market gain on the pairs held, which for a hedged book is the basis move |
| costs | fees, spread, slippage and conversion, itemised as everywhere else |
| annualised Sharpe | reported **only** where the year carries at least 6 scored months, and always beside its standard error |

A year with fewer than 6 scored months is reported with its return and an explicit statement
that nothing can be concluded from it, exactly as an under-populated regime is.

**Three statistics fixed here, before any of them is computed.**

1. **The trend.** An ordinary-least-squares slope of monthly net return on months elapsed
   since the first scored month, with a 95 per cent bootstrap confidence interval at 10,000
   resamples on the generator already seeded at 987654321. Reported as the slope, its
   interval, and whether that interval excludes zero. A negative slope whose interval excludes
   zero is measured decay. A negative slope whose interval spans zero is not, and will not be
   described as though it were.
2. **The two-year comparison of D1**, as stated above, with the difference and its bootstrap
   interval.
3. **The standalone recent window.** The most recent **24 scored months**, evaluated on their
   own against criterion 1 of section 11 — positive net return, and an annualised Sharpe above
   the 95th percentile of the same exposure-matched null draws restricted to the same months.

The recent-window test **adds no engine run and no registry trial**. It re-scores series that
already exist over a sub-window, and it percentiles the same null draws over the same months.
It is nonetheless a second test on one result, and the multiplicity is paid for by requiring
**both** windows to clear rather than either: see 16.2.

**Twenty-four months is a small sample and that is stated in advance.** SEXTANT-004 measured a
Sharpe standard error of 0.709 over 24 months. The recent window is therefore reported with
the effect size it *could* have detected at its own `N_eff`, and a failure to clear criterion 1
there is separated into two distinct findings that must never be merged:

- **the recent window rejects** — the point estimate is negative, or positive and below its
  own null's 95th percentile by more than the sample could plausibly be wrong about;
- **the recent window cannot resolve** — the point estimate is positive and above its null, or
  below it by less than the detectable effect size. This is (C) for the persistence question
  specifically, and it is reported in those words.

### 16.2 What this does to the verdict, which is to make it stricter

Criterion 1 of section 11 is unchanged for the full window. **A sixth requirement is added,
and it only ever removes passes:**

| | criterion | test |
|---|---|---|
| 6 | **the edge is not confined to the early window** | the most recent 24 scored months, evaluated alone, satisfy criterion 1 in the headline cell |

- A variant clearing criteria 1 to 5 **and** criterion 6 is an edge that persists.
- A variant clearing criteria 1 to 5 and **failing** criterion 6 with a *rejecting* recent
  window is a **decayed edge**. It is reported in those words with both windows' numbers, and
  **it is not verdict (A)**. An edge that existed until 2023 and does not exist now cannot
  justify building a system to trade it in 2026, and dressing it as (A) would be exactly the
  softening the brief forbids.
- A variant clearing criteria 1 to 5 and failing criterion 6 with an *unresolved* recent
  window is **(C)**, stated as: the full window shows an edge, the recent window is too short
  to say whether it survives, and here is the `N_eff` that would settle it.

### 16.3 The maintenance margin is an assumption, not a datum, and it flatters

**What the venue publishes, established by asking it.**

| source | what it answers | verdict |
|---|---|---|
| `/fapi/v1/leverageBracket` | today's maintenance-margin tiers per symbol | **not public.** Returns HTTP 401, `API-key format invalid`, without a credential. Even the current schedule is not readable anonymously |
| `/fapi/v1/exchangeInfo` | public, and describes tick, lot and notional filters | carries **no** margin brackets, and lists **only currently-trading contracts** |
| the web CMS brackets endpoint | today's tiers | rejected a bare request with `illegal parameter` |
| `data.binance.vision`, every tree at every grain | funding, klines, mark price, premium, depth, trades | **no brackets tree exists**, monthly or daily |

**There is no point-in-time maintenance-margin schedule, anywhere, at any price.** There is not
even a publicly readable schedule for today.

**So 0.005 is a documented published figure carried as an assumption, and section 4 is amended
to say so in those words.** It is today's lowest-tier maintenance margin for major contracts.
It is not a measurement, it is not dated, and it did not apply in 2020.

**Which way it biases the result, named.** Margin tiers were **tighter** when liquidity was
thinner — higher maintenance margin and lower permitted leverage, both earlier in the window
and on smaller assets. Applying today's looser tier to 2020, 2021 and 2022 therefore
**understates** the maintenance margin that actually applied, which **overstates** the distance
from a position to its liquidation price, which **understates** how often a liquidation would
have happened and **understates** the probability of ruin. **The bias is optimistic. It
flatters exactly the leveraged results the brief warns about.**

It is also a **look-ahead**: today's tier is information from after every decision instant in
the window, applied to all of them.

**What is registered in response.** Liquidation risk and probability of ruin are reported at
**three** maintenance-margin settings and never at one:

| setting | what it is |
|---|---|
| 0.005 | today's published lowest tier for majors. The optimistic end, and labelled as such |
| 0.010 | twice it |
| 0.025 | five times it, standing in for the tighter tiers that applied earlier in the window and to thinner assets |

**A conclusion about leverage counts only if it holds at 0.025.** That converts an
unmeasurable into a stated sensitivity in the direction that cannot flatter, which is what
invariant 12 already requires of spread and slippage.

> **Corrected by 17.2.** The sentence above is wrong and the original is left standing so the
> correction is visible. At this account's size every position sits deep inside the venue's
> first margin bracket, so 0.025 is a stress level rather than a candidate historical rate. All
> three settings are still reported; a variant failing **only** at 0.025 is not rejected on that
> basis. Criteria 1 to 6 are untouched by this.

**The initial margin fraction of 0.20 is a different kind of number and no look-ahead arises
from it.** It is not a venue limit read from an endpoint; it is this project's own choice of
how much buffer to post, far inside anything the venue has ever permitted on a major. A
self-imposed constraint applied historically constrains, and does not flatter.

**The margin-buffer sweep replaces the spot-leg leverage sweep as the parameterisation, not as
a fallback**, per the Sponsor's decision. Section 13 stands: the spot leg cannot be levered
over an unpublished margin-lending rate and will not be.

### 16.4 Capacity, which the archive turns out to support better than section 2 assumed

`docs/DATA-FEASIBILITY-006.md` recorded that order-book depth could not be reconstructed
outside the `bookTicker` slice, and that capacity would therefore rest on traded turnover
alone. That was established against the monthly trees. **The daily tree publishes
`bookDepth`, which the monthly tree does not**, and it is small: one file per symbol per day,
about 0.47 MB, sampling cumulative resting depth in notional at 1 to 5 per cent either side of
mid, every minute, from **2023-01-01 to 2024-05-17**.

That is a direct measurement of the quantity a capacity statement needs, and it is affordable.
**The sample is fixed here, before any byte of it is read:**

- **Days.** The first trading day of each calendar month in the available range: 2023-01-01
  through 2024-05-01, seventeen days.
- **Symbols.** The perpetual legs of the **twenty** carry-universe members with the largest
  trailing 30-day median quote turnover at **2022-12-31**, ties broken by symbol ascending.
- **Statistic.** Per symbol-day, the median across the day's minutes of cumulative resting
  notional within 1 per cent of mid, and the same within 5 per cent; plus both restricted to
  00:00-00:05 UTC, the five minutes around the rebalance instant.

**What it can and cannot say.** It measures resting depth on the **perpetual** leg only, on
seventeen days, in a seventeen-month stretch of a longer window. It says nothing about the
spot leg, nothing about the other years, and nothing about what would actually have filled.
Capacity is therefore reported as a **bound with its basis stated**: the capital at which one
rebalance's largest single order would consume a stated fraction of measured 1-per-cent depth,
at the sampled instants. Turnover-based capacity is reported for the whole window, as a
separate quantity.

> **Constrained by 17.1.** The depth window is seventeen months of a six-year evaluation
> window, and declared expectation D1 predicts the edge sits mostly outside it. 17.1 fixes
> three rules in response: a measured figure always carries its window, an extrapolation is
> never tabulated beside a measurement, and where the variant did not earn inside the depth
> window its capacity is reported as **unestablished**.

Like the spread sample of section 12, this is a **measurement, not a trial**. It consumes no
variant budget, and it changes no number in any result table: every variant remains costed at
the configured assumptions of section 8.

---

## 17. Amendment 2 — where capacity cannot be measured, and which bracket applies

**Added on the Sponsor's instruction, before the grid ran and while the acquisition was still
downloading. No variant had been run and no equity curve existed. It changes no criterion, no
variant, no cost cell and no trial budget: 17.1 constrains how a capacity number may be
reported, and 17.2 corrects a sentence in 16.3 that would have misreported a stress test as a
failure.**

### 17.1 Capacity and decay collide, and the report must say so

**The problem, stated before either number exists.** `bookDepth` covers **2023-01-01 to
2024-05-17**, about seventeen months. The evaluation window of section 7 spans roughly 2020 to
2026. Declared expectation D1 in 16.1 predicts that the carry's edge is concentrated early and
has decayed since.

**If D1 holds, then the period where the edge most likely lives is precisely the period where
capacity cannot be measured.** Those two facts are not independent, and a report that put a
depth-measured capacity figure next to a headline return earned mostly in 2021 would be
implying a measurement of something nobody measured.

**So capacity is reported under three rules, and none of them is a judgement call.**

**Rule C1 — measured means measured inside the depth window.** Every depth-derived capacity
figure carries the window it was measured over, in the same cell, in the same row. There is no
capacity figure in this report that does not state its window.

**Rule C2 — an extrapolation is never tabulated beside a measurement.** A capacity number for
any period outside 2023-01-01 to 2024-05-17 is an extrapolation. It appears in its own table,
under its own heading, labelled as an extrapolation, with the assumption that carried it
outside the window stated. It is never placed in the same table as a measured value, and never
in an adjacent column, because adjacency is itself a claim.

**Rule C3 — when the edge sits outside the window, capacity is unestablished, in those
words.** Two quantities decide it, both computed from series that already exist:

- `depth_months` — the count of the variant's scored months whose whole holding period lies
  inside the depth window;
- `mean_inside` and `mean_outside` — the variant's mean monthly net return over the scored
  months inside the depth window and over those outside it, with the difference and its
  bootstrap interval at the seed section 14 already fixed.

Then:

| condition | what the report says |
|---|---|
| `depth_months < 12` | **capacity unestablished.** The depth window does not contain enough of this variant's scored months to measure it |
| `mean_inside <= 0` | **capacity unestablished.** The variant did not earn over the months where depth can be measured, so there is no edge there whose capacity could be reported |
| `mean_inside > 0` and `mean_outside > mean_inside` | capacity is reported **as measured over a period in which this variant earned less than it did overall**, and that sentence appears beside the number |
| `mean_inside > 0` and `mean_outside <= mean_inside` | capacity is reported as measured, with its window, under rule C1 |

"Unestablished" is a real answer and is not softened. It does not mean the strategy has no
capacity; it means this dataset cannot say what it is, which is the same class of statement as
invariant 9's *not evaluable*.

**Turnover-based capacity is unaffected and is reported for the whole window**, because daily
quote volume is in every kline row for every month. It is a different quantity from resting
depth, it is labelled as an inference from traded notional rather than from a book, and rule
C2 keeps it out of the depth table.

### 17.2 The first bracket is the only bracket at this capital

**The arithmetic, which narrows the question sharply.** A carry pair's single-leg notional at
1x is `equity / (N * (1 + margin_fraction))`. The largest it can be under anything registered
in this document is one pair at the thinnest buffer the stage-3 sweep reaches:

```
1,500 EUR / (1 * (1 + 0.05))  =  1,428.57 EUR  ~  1,550 USDT
```

Binance's first USD-M margin bracket for a major contract covers notional far above that —
today's first bracket for BTCUSDT runs to 50,000 USDT, some thirty-two times the largest
position anything here can open. **Every position in every variant, in every cell, at every
margin-buffer setting, sits in the first bracket and is nowhere near leaving it.**

That matters because bracket *assignment* is the size-dependent, genuinely hard part of a
historical margin reconstruction, and at this capital it does not arise. **The only open
question is what the first-bracket maintenance rate was historically**, which is a single
number per era rather than a schedule.

**Whether an archived copy of it is findable: looked for, not found, and here is where the
search stopped.** The Internet Archive holds a snapshot of the venue's leverage-and-margin
page at
`https://web.archive.org/web/20211223052201/https://www.binance.com/en/futures/trading-rules/perpetual/leverage-margin`
(2021-12-23). The page is client-rendered: the snapshot contains the page shell and its
translation strings, and the bracket table itself was loaded by a request the archive did not
capture. No `maintMarginRatio`, `maintenanceMarginRate` or equivalent field appears anywhere in
the archived HTML. **The historical first-bracket rate is therefore not established.** The
snapshot URL is recorded so that anyone continuing this does not have to rediscover where the
trail goes cold.

**What 16.3's closing rule becomes.** 16.3 registered that "a conclusion about leverage counts
only if it holds at 0.025". **That sentence is wrong and is corrected here.** 0.025 is five
times today's published first-bracket rate; at a position size thirty-two times below the
first bracket's ceiling it is not a plausible historical rate, it is a stress level. Requiring
a conclusion to survive it would report a stress failure as a real one, which is a different
kind of dishonesty from the one 16.3 was guarding against and no less of one.

The three settings stand, unchanged, and all three are reported. What changes is what they
mean:

| setting | status |
|---|---|
| **0.005** | today's published first-bracket rate for majors. **The headline figure.** An assumption and a look-ahead, exactly as 16.3 says |
| **0.010** | twice it, carried as a companion for the possibility that the first-bracket rate was higher earlier in the window. Also an assumption; nothing measured supports or contradicts it |
| **0.025** | **a stress level, not a candidate rate.** Reported for robustness. **A variant that fails only at 0.025 is not rejected on that basis**, and the report says so where the number appears |

**This is a relaxation of a bar this document set for itself, and it is recorded as one.** It
was made before any result existed, on the arithmetic above rather than on anything observed,
and it touches only how a leverage and probability-of-ruin conclusion is phrased. **Criteria 1
to 6 of section 11 are untouched.** Nothing about whether a family shows an edge at 1x depends
on the maintenance margin, because at 1x a fully-collateralised carry pair has no liquidation
price at all.

**The direction-of-bias statement in 16.3 stands, and is narrower than it was.** The concern is
no longer that today's whole tier structure is being applied to 2020; it is only that the
first-bracket *rate* may have been higher then than it is now. That is a smaller unmeasured
quantity, it biases liquidation frequency and probability of ruin in the same optimistic
direction, and the 0.010 companion is what shows how much it could matter.

---

## 18. Amendment 3 — the budget is enforced, and the ordering is auditable

**Added on the Sponsor's instruction, before the grid ran and while the acquisition was still
downloading. No variant had been run and no equity curve existed. It changes no criterion, no
variant, no cost cell and no budget number. Both parts turn something this document already
asserted into something a machine refuses to violate and a reader can check without trusting
anybody.**

The two additions are answers to the same question asked twice: *what stops this document from
being decorative?* Section 3 declared a budget and section 15 declared that the file is never
edited after a number is seen. Neither statement had a mechanism. Now both do.

### 18.1 The budget lives in the configuration and the runner enforces it

**What was wrong with the budget as registered.** Section 3 states the allowance in prose. A
reader could verify that the prose says thirty-two, and nothing at all would have stopped a
thirty-third run. The number was a promise, and a budget the runner does not enforce is not a
budget.

**Where it lives now.** `config/spike-006-f1.yaml`, block `trial_budget`, as data:

| key | value |
|---|---|
| `family` | `F1` |
| `engine_version` | `sextant-006-f1` — how this family's trials identify themselves in the registry |
| `maximum_trials` | **32** |
| `variants` | the eight identifiers of section 9.2, in registered order |
| `cost_cells` | the four labels of section 8, in registered order |
| `nulls_are_charged` | **false** |

**What enforces it.** `sextant.engine.backtest.budget` holds a `TrialBudget` and a
`BudgetLedger`. The runner charges one trial per variant per cell **before the engine runs**,
never after, because a check that happens once an equity curve exists is a check somebody can
be tempted to argue with. Three refusals, and each catches a different way a search widens
after a result has been seen:

| refusal | what it catches |
|---|---|
| **unregistered variant** | the ninth variant, the adjusted parameter, the extra lookback. Budget remaining is not permission: the variant was never registered, so it cannot run whatever the allowance says |
| **unregistered cost cell** | the cheap violation. The headline cell disappoints, so a cheaper cell appears and is reported instead. It costs nothing to attempt and it raises |
| **exhausted allowance** | the family is closed. The message says so in those words: *no ninth variant, no adjusted parameter, no additional cell. If it did not clear its criteria, that is the answer.* |

**Three properties of the mechanism, each chosen deliberately.**

**A budget may not have slack in it.** `TrialBudget` refuses to be constructed unless
`maximum_trials` equals `len(variants) * len(cost_cells)` exactly. Registering fewer than the
grid and holding the rest in reserve is precisely the behaviour section 3 forbids in prose, so
the type forbids it in code. This is also why the exhaustion refusal is, for F1, structurally
unreachable through registered trials alone: thirty-two distinct pairs exist and thirty-two may
be charged. Overspending therefore *requires* running something unregistered, which is refused
by name with a clearer message than a count would give.

**Charging is idempotent on the pair.** Re-running the whole grid spends the allowance once,
not twice, exactly as the registry recognises a repeated evaluation as the same trial by
fingerprint. A budget that broke on a rerun would be a budget that discouraged reproducing a
published number, which is the opposite of the intent.

**Post-hoc trials already in the registry reduce what is left.** The ledger can be opened
against `research/trial-registry.jsonl`. Registered trials found there are **not** charged, so
a rerun stays free. Trials recorded against this family whose variant the budget does not name
**are** charged, because they were searches in this family and they inflate the multiplicity the
Deflated Sharpe Ratio has to deflate for. A family does not get to spend its whole declared
budget on top of whatever it already tried. This is the one path by which the allowance
genuinely runs out, and it is the path that would matter if a future session ever bypassed the
ledger.

**Nulls, benchmarks and decomposition constructs are still not charged**, unchanged from
section 3. Charging them would make running *more* nulls — the honest thing — consume the
allowance for the thing that needs restraining. They are counted in full in the registry, which
is what the DSR reads, so nothing is concealed by leaving them uncharged.

**What the report states.** Budget consumption is recomputed from the committed registry rather
than reported from the run's own counter, so the figure is one a reader can reproduce from the
file in the repository: the declared budget, the trials charged, what remains, the uncharged
null and benchmark count, and — if it is ever non-empty — a separately headed list of anything
post-hoc with the reason it was added.

### 18.2 The pre-registration ordering is auditable from the report itself

**What the drift guard can and cannot prove.** `assert_no_drift` compares 126 registered values
against the constants the code will use and refuses to start on any difference. That proves the
code and this specification agree **at run time**. It cannot prove they were not edited
*together*, after a result was seen, to make them agree — and nothing inside the process can,
because anything inside the process is written by the same run.

**Git ordering can prove it.** Four facts, and a reader can check all four:

1. `config/spike-006-f1.yaml` is **tracked**;
2. it had **no uncommitted edits** when the run started — staged or unstaged, either one means
   the committed SHA describes different bytes from the ones the run read;
3. the **commit that last touched it** exists, and its SHA and instant are recorded into the
   results;
4. that commit is an **ancestor** of the commit that introduced the results.

**What refuses, and when.** `registration_provenance` runs before the world is built and before
anything is computed. An untracked or dirty configuration raises and the run does not start.
There is no override, no `--force` and no environment variable, because a provenance check that
fails open records exactly the same thing as one that passes — which is invariant 10's collapse
in the place where it matters most.

**Why the results SHA is produced by a second command.** The SHA of the commit that introduces
a results file does not exist while the file is being written. So the audit is rerun **after**
the results are committed, and its output is what the report quotes:

```
uv run sextant spike-006-f1 ordering
```

Run before the results are committed it prints `results commit: NOT YET COMMITTED` and
`ordering verified: no`, rather than pretending. Run when the specification's commit is *not* an
ancestor of the results' commit — the exact shape of the failure this mechanism exists to catch,
where the numbers land first and the specification is written or widened once they are known —
it prints `ordering verified: NO` and exits non-zero. **A result whose ordering does not verify
may not be reported as pre-registered.**

**The report cites both commits.** The final results document and `docs/VERDICT-006.md` carry
the specification's SHA and instant, the results' SHA and instant, and the ancestry verdict
between them, so the claim "this was fixed before the numbers existed" is a pair of SHAs a
reader can check out rather than a sentence somebody typed.

**What this does not prove, stated so it is not oversold.** That the specification was *wise*,
or that these eight variants were the right eight. Only that they were fixed first. Ordering is
a necessary condition for a pre-registration to mean anything, and it is not a sufficient one.

### 18.3 What this amendment does not touch

Criteria 1 to 6 of section 11, the eight variants of section 9.2, the four cost cells of section
8, the 32-trial allowance of section 3, the seed counts of section 10 and every threshold in
sections 6, 7 and 14 are **unchanged**. Both parts of this amendment constrain how this family
may be run and how its result may be reported. Neither changes what would count as an edge.

---

# Part 2 — the dataset

**Committed after acquisition and before any strategy result exists. No variant had been run,
no equity curve existed and no return of any construction had been computed when this was
written.** Part 1 needed no data and was fixed before any was seen. This half can only be
written once the archive is on disk, which is why the two are separate commits.

Every figure below is **measured**, and every one of them is also written to
`research/spike-006-f1-dataset.json` by `uv run sextant spike-006-f1 dataset`. That file is the
single copy; this section reads it. Where a number here and a number there disagree, the file is
right and this section is stale.

## 19. What was acquired

| | |
|---|---|
| objects downloaded | **67,496** |
| bytes | 83.4 MB |
| wall clock | 83.8 minutes |
| **failures** | **0** |
| publisher SHA-256 verified | **67,496** |
| objects publishing no checksum | **0** |
| dataset fingerprint | `0e9582ac0e9e2991…` |

**Every object was checked twice**: the archive's own CRC inside the zip container, and then the
publisher's separately published SHA-256. A mismatch on either raises and does not write, so no
object reached the store unverified. That the second number equals the first, and the third is
zero, is the strongest statement this acquisition can make: there is no object here whose
integrity rests on the download not having gone wrong.

The **fingerprint** is one SHA-256 over the sorted set of all 67,496 digests. It goes on every
row this family writes to `research/trial-registry.jsonl`, so a changed dataset is a different
trial rather than the same trial with different data underneath it.

**The acquisition took the whole tree.** No quote filter, no window trim, no liquidity screen.
That was registered in stage 0 and it removes a degree of freedom: nothing about which symbols
exist in the store can have been influenced by what any of them did.

## 20. What the three trees hold, and where they disagree

| tree | first | last | months |
|---|---|---|---:|
| `fundingRate` | 2020-01 | 2026-08 | 80 |
| `klines` | 2020-01 | 2026-08 | 80 |
| `premiumIndexKlines` | 2020-01 | 2026-08 | 80 |

Stored after ingest: **951** perpetual bar series holding 691,993 daily bars, **952** funding
series holding 2,832,399 settlements, **947** premium series holding 650,417 rows.

**The three counts differ, and the differences are named rather than rounded away.**

| disagreement | symbols | what follows |
|---|---|---|
| funding published, no klines | **1** — `GAIBUSDT` | The listing calendar is built from **kline** presence, so this contract has no calendar entry and cannot enter the universe. Recorded because silently dropping a contract the venue published cash flows for is the quiet kind of exclusion invariant 9 exists to surface |
| klines published, no premium index | **4** — four meme listings with non-Latin tickers | Unrankable by `carry-premium-10`, counted as not-evaluable there, and **not** excluded from the universe. The other seven variants do not read the premium index, and dropping an asset because one variant cannot score it would make the universe depend on which variants exist |
| kline-months with no funding object | **1,135** | Why presence is taken from klines. Using funding presence would delist a contract on a missing cash-flow file, which is a fact about the archive rather than about the venue |

**73 contracts stopped publishing before the archive's last month, and 0 left and returned.**
Delistings are retained in the candidate set throughout, per the standing discipline.

## 21. Holes in the published funding, and what the registered rule does about them

This is the most consequential dataset fact in this section, and it was measured rather than
noticed.

| | |
|---|---|
| symbols scanned | 952 |
| symbols with at least one gap | **492** |
| missing settlements in total | **5,661** of 2,832,399, about 0.2 per cent |

A gap is two consecutive published settlements more than one of **the venue's own stated funding
intervals** apart. The interval comes from the venue's `funding_interval_hours` column and is
never inferred from the gaps themselves: a hole would redefine an inferred cadence and then
declare the series complete, which is precisely the defect `Settlement` carries that column to
prevent.

**The gaps are not spread evenly. One instant dominates.**

| settlement instant | symbols missing it |
|---|---:|
| **2026-06-24T04:00** | **423** |
| 2026-01-02T13:00 | 17 |
| 2026-01-02T14:00 | 17 |
| 2026-01-02T15:00 | 17 |
| 2025-07-18T17:00 | 7 |

The 2026-06-24 case was checked against the raw archive rather than assumed: the published CSV
for an affected symbol holds five settlements that day where its own 4-hour cadence requires
six, and 00:00, 08:00, 12:00, 16:00 and 20:00 are present while 04:00 is absent. **It is a gap
in what the venue published, not an artefact of the parser.** Only the 4-hour-cadence contracts
have an 04:00 settlement to miss, which is why the 8-hour majors are unaffected.

**What the registered rule does, and the visible consequence.** Section 6 rule 4 disqualifies an
asset whose trailing 30 days contains a gap, for that rebalance only. So the carry universe
falls from **339 pairs at 2026-06-01 to 108 at 2026-07-01 and back to 340 at 2026-08-01** — the
largest single-month contraction in the window, and it is a data artefact rather than a market
event. It is flagged automatically in the manifest so nobody has to spot it.

**The alternative was to sum whatever is on file.** That ranks a partial sum against whole ones
and understates it, every time, always in the same direction. Disqualifying is the conservative
choice and it was registered before this gap was known to exist.

**Bias direction, stated.** Rule 4 removes assets rather than admitting them, so it shrinks the
universe and can only reduce the opportunity set. Where it binds, the effect on a result is to
make it *worse*, not better.

## 22. The window the rule resolved to

| | |
|---|---|
| first usable month | **2020-11** |
| last usable month end | **2026-08** |
| **usable months** | **69** |
| registered floor | 36 |
| FX first day in the archive | 2020-01-03 |
| perpetual archive last month | 2026-08 |
| spot archive last month | 2026-08 |

Resolved from listing metadata and FX availability only. **No return was consulted.** The first
month is the earliest month start at least 300 days after the archive's first `EURUSDT` bar, so
the 180-day listing age, the 90-day longest lookback and the 30-day turnover window are all
satisfiable with a ragged-edge month to spare: 2020-01-03 plus 300 days is 2020-10-29, and the
first month start at or after that is 2020-11-01.

**69 months against a floor of 36.** Section 7's stop condition does not fire, and the reason it
does not is recorded here rather than left implicit.

The window is **13 months longer than SEXTANT-005's 66**, and starts three months earlier, for
one reason: SEXTANT-005 needed 360 days of FX warmup for a 360-day momentum lookback, and the
longest lookback any registered carry variant reads is 90 days.

## 23. The carry universe, per rebalance

| | |
|---|---|
| rebalances | **70** |
| pairs, minimum | **24** (2020-11-01) |
| pairs, median | **155** |
| pairs, maximum | **340** |
| spot candidates with a priced daily series | 754 |
| perpetual candidates with a priced daily series | 849 |

**Non-empty at every rebalance**, which section 7 requires and which is checked rather than
assumed: the world builder refuses to return a world with an empty instant and names the
instants that were empty.

The eight rebalances from 2020-11 to 2021-06 carry fewer than half the median. That is the
perpetual universe growing, not a defect, and it is flagged in the manifest so a reader can see
that the early folds are thinner than the late ones. It matters for declared expectation D1: the
early period the Sponsor expects the edge to live in is also the period with the fewest pairs to
choose among.

**One cross-check was run and it agrees exactly.** The universe intersects two policies on base
asset; the strategy re-derives its pairing independently from the candidate set by exact base
match. The two produce **the same pair count at all 70 rebalances, with zero disagreements**. If
they had ever differed, one of them was wrong and no result would have been trustworthy.

## 24. What each rule excluded

Counts are **independent**: an asset failing three rules appears in three counts. Attributing
each rejection to whichever rule happened to run first would answer "which rule fired?" when the
question worth asking is "which threshold binds?".

Summed across all 70 rebalances, **perpetual leg**:

| rule | rejected | not evaluable |
|---|---:|---:|
| `quote_currency` | 0 | 0 |
| `sourced_membership` | 39,534 | 0 |
| `listing_age` | 42,643 | 0 |
| `funding_evaluability` | 41,765 | 0 |
| `median_quote_volume` | 1,604 | **40,017** |
| `bar_coverage` | 41,489 | 0 |
| `asset_class` | 210 | 0 |

**Spot leg**:

| rule | rejected | not evaluable |
|---|---:|---:|
| `quote_currency` | 5,110 | 0 |
| `sourced_membership` | 25,736 | 0 |
| `bar_coverage` | 27,346 | 0 |
| `asset_class` | 1,050 | 0 |

**The 40,017 not-evaluable turnover observations are the largest single figure here and they are
not rejections.** A perpetual with fewer than 20 closed daily bars in its trailing 30 days
cannot have a median computed, so the rule declines to answer rather than guessing. Almost all of
them are contracts that had not yet listed at that rebalance, and they are also counted by
`listing_age` — which is exactly what independent tallies are for.

**Pairing losses**, summed across rebalances: **1,463** admissions on the perpetual leg had no
admitted spot counterpart, and **10,369** on the spot leg had no admitted perpetual. The
asymmetry is expected and it points the right way: the spot universe is much broader than the
perpetual one, so the binding constraint on a carry pair is whether the venue lists a perpetual
at all. **That biases towards tradability**, which is the conservative direction, and it was
stated in stage 0 before it was measured.

**15 scaled-unit perpetuals were excluded** by section 6 rule 1 — contracts such as
`1000PEPEUSDT` whose unit is a multiple of the underlying. Pairing one with its spot counterpart
needs a unit-conversion rule, and a rule invented after the data was seen is a degree of freedom.
The 15 symbols are listed in the manifest.

## 25. What part 2 does not change

**No criterion, no variant, no cost cell, no threshold and no trial budget.** Part 1 and its
three amendments stand exactly as committed. Nothing measured here was used to choose anything;
it is the description of the data the registered specification will now be run against, written
down before it was run so that a reader can tell the difference between a fact about the dataset
and a fact discovered while looking at a result.

Two things in this section could still stop F1, and both are registered stop conditions rather
than judgements: the window carrying fewer than 36 months, which it does not, and the carry
universe being empty at a rebalance, which it is not.

---

## 26. Amendment 4 — an unrepresentative month, and the venue that could actually trade this

**Added on the Sponsor's instruction, before the runner existed. No variant had been run, no
equity curve existed and no return of any construction had been computed. Both additions are
reporting requirements. Neither changes a criterion, a variant, a cost cell, a threshold or the
trial budget, and the funding-evaluability rule of section 6 stands exactly as written.**

### 26.1 The July 2026 contraction: composition, not size

**What was already established.** Section 21 measured the archive's missing settlement at
2026-06-24T04:00, absent for 423 symbols, and recorded that the registered rule consequently
disqualifies 231 pairs at the 2026-07-01 rebalance: 339 pairs become 108 and recover to 340 the
month after.

**Why size was the wrong thing to check.** Section 21 argued that the rule "can only reduce the
opportunity set" and that where it binds "the effect on a result is to make it worse, not
better". **That argument is about size and it does not carry to composition.** A rule that
removes two thirds of the universe non-randomly does not merely shrink the opportunity set; it
changes what the opportunity set *is*. If the 108 survivors differ systematically from the 231
excluded, that month's return is computed on an unrepresentative slice, and the direction of the
resulting bias is not knowable from the count.

**It matters more than an ordinary month.** 2026-07 falls inside the trailing 24 scored months
that criterion 6 evaluates. One artefact month carrying an unrepresentative slice would be
diluted to near-nothing across 69 months and is 1/24th of the test that decides whether the
edge persists.

**The comparison, fixed by rule before either group is examined.** At the rebalance with the
largest month-on-month contraction in pair count — identified by computation, not named here,
so the procedure survives a different dataset — the perpetual legs are split into two groups:
**admitted** and **excluded by `funding_evaluability`**. Both groups are described on three
attributes, all of which already exist in the world:

| attribute | statistic per group |
|---|---|
| **median funding rate** | the median of every rate the venue published for that perpetual in the trailing 30 days. A median rather than the trailing *sum* deliberately: the excluded group's sum is incomplete by construction, and comparing an incomplete sum against complete ones would measure the hole rather than the population |
| **contract age** | days from the perpetual's earliest sourced listing instant to the rebalance |
| **liquidity band** | the band the cost model would have charged, from trailing median quote turnover: `deep`, `mid`, `thin` or `unknown`, reported as a count and a share |

**One method for all three, so nothing is chosen per attribute.** For each, the difference
between groups is bootstrapped at **10,000 resamples on the generator already seeded at
987654321** — the same seed section 14 fixes for every interval in this task. For the two
numeric attributes the difference is between medians; for the band it is the difference in the
`deep` share. **A difference whose 95 per cent interval excludes zero is reported as
systematic. One whose interval spans zero is not, and will not be described as though it
were** — the same rule 16.1 applies to the trend slope.

**What is reported, and what is not decided.** The headline for the best variant is reported
twice: on the full scored series as registered, and on the same series with the affected
rebalance's month removed. The recent-24-month window of criterion 6 is reported the same way,
and the ex-contraction version takes **the 24 most recent scored months excluding the affected
one**, reaching one month further back so that both versions carry 24 observations. That is
deliberate: holding *n* equal means a difference between the two answers is attributable to the
month rather than to sample size. The null draws are restricted to the same months in both
cases.

**Criterion 6 is judged on the full series, exactly as registered.** The ex-contraction figure
sits beside it as a robustness note and decides nothing. **If the two disagree, the report says
so in the persistence section's opening sentence**, in the form: the answer to whether this edge
persists depends on a single month whose universe was determined by a hole in the venue's
published data. That is a statement a reader can act on. Quietly reporting whichever of the two
answers was preferred would not be.

*The Sponsor referred to the way a November 2024 outlier was handled on Kraken. No such
procedure is recorded anywhere in this repository — `docs/` carries no outlier-exclusion
treatment for that month or any other — so rather than claim to inherit a precedent, the
procedure above is written out in full here and is what will be followed.*

### 26.2 The research venue's fees are not the fees this account would pay

**The gap, and why it is the largest unstated caveat in this family.** Every cell in section 8
charges Binance's own published rates. Binance is the research venue and nothing more: section
2 says so, and no result computed on it is evidence that anything is executable elsewhere.
**The only venue this account can actually trade is Kraken.** A positive research finding that
arrives without the execution number is a finding whose practical value is unstated.

**Kraken's published schedule, looked up rather than assumed.** Both legs, from the venue's own
fee schedule page, read on 2026-09-10:

| leg | Kraken entry tier | Binance VIP 0 | ratio |
|---|---|---|---|
| **spot** | **40 bps maker / 80 bps taker** | 10 / 10 | **4x maker, 8x taker** |
| **perpetual futures** | **2 bps maker / 5 bps taker** | 2 / 5 | **identical** |

**The perpetual leg is the same price at both venues.** That was not the expected answer and it
is the single most useful thing the lookup produced: the entire cost differential between the
research venue and the execution venue sits on the **spot** leg, which is the long leg of every
carry pair in this family.

Kraken's futures schedule runs from 0.02/0.05 per cent at level 1 (below 5,000,000 USD of
30-day futures volume) down to negative maker rates at the Pro levels; the entry tier is what
applies here. Kraken's spot tier is "the most favourable of three indicators" — spot volume,
futures volume, or assets on platform. **At 1,500 EUR the account is in the entry tier on all
three**, so no bracket question arises, the same arithmetic that closed the margin-bracket
question in 17.2.

**The arithmetic, stated in advance of any result.** At the headline 50/50 fill mix, one full
round trip of a pair — open both legs, close both legs — pays in fees alone:

```
Binance:  2 * (10.0 + 3.5)  =  27 bps
Kraken:   2 * (60.0 + 3.5)  = 127 bps
```

about **100 bps per full round trip of additional fee**, before spread and slippage, which are
unchanged and charged on both legs at both venues. What that costs a variant depends on its
realised turnover, which the run measures rather than assumes: pairs carry across rebalances and
only the difference between held book and target book is traded, so a variant that keeps its
pairs pays this once and a variant that churns pays it monthly.

**What is registered, exactly.**

- **One sensitivity, one variant, one cell.** The variant with the highest out-of-sample
  annualised net Sharpe in the headline cell — ties broken by variant identifier ascending — is
  re-costed once at a cell labelled `kraken_execution`: **Kraken spot 40/80, Kraken futures
  2/5, 50/50 fill mix, spread and slippage unchanged**. It runs whether or not that variant
  cleared anything, because the number is informative either way.
- **It is not a cost cell and it is not part of the grid.** Criterion 5's sign-stability test
  is over the four cells of section 8 and is unchanged. No criterion reads this figure.
- **It does not consume variant budget, and the reason is one-directional.** Kraken's rates are
  higher than Binance's on one leg and identical on the other, so this re-cost **can only make
  a variant look worse**. It cannot produce a winner that was not already one, so it cannot
  widen the search, which is the only thing the budget exists to prevent. It is nonetheless
  **recorded in full in `research/trial-registry.jsonl`** and therefore counts in the Deflated
  Sharpe Ratio's trial count, where its only possible effect is to raise the bar.
- **It is labelled at every appearance.** Never in the same table as a research-venue number
  without the venue in the same row, by the same rule 17.1's rule C2 applies to capacity: a
  Kraken fee applied to a Binance universe is a hypothetical, and adjacency to a measurement is
  itself a claim.

**What this sensitivity is not, stated so it cannot be over-read.**

1. **It is not evidence that this strategy is executable on Kraken.** It substitutes a fee
   schedule and changes nothing else. The universe is still Binance's, the funding stream is
   still Binance's, and the prices are still Binance's.
2. **It is a lower bound on the difficulty, not an estimate of it.** Kraken lists far fewer
   perpetuals than Binance, so the real Kraken carry universe is narrower than the one this
   number is computed on — and a narrower universe means fewer pairs, worse diversification and
   a smaller opportunity set. Whether Kraken lists a perpetual for each base at each rebalance
   is a separate point-in-time question this task does not answer.
3. **It says nothing about whether the account may trade perpetual futures on Kraken at all.**
   That is a jurisdiction and product-availability question, it is outside this task's scope,
   and `docs/LIVE-GATES.md` is unaffected by anything here.

**The sentence this exists to make possible.** If the carry clears its criteria at research
rates and loses money at execution rates, the report says exactly that, in the verdict, in those
words. It would mean the edge is real and this account cannot harvest it, which is a different
answer from both (A) and (B) and must not be presented as either.

### 26.3 What amendment 4 does not touch

Criteria 1 to 6 of section 11, the eight variants of section 9.2, the four cost cells of
section 8, the 32-trial budget of section 3, the seed counts of section 10, the funding
evaluability rule of section 6, and every threshold in sections 6, 7 and 14 are **unchanged**.
26.1 adds a description of one month and a second reading of one headline. 26.2 adds one
labelled sensitivity outside the grid. Neither changes what would count as an edge.

---

## 27. Amendment 5 — break-even turnover, and the expectation that goes with it

**Added on the Sponsor's instruction, before the runner existed. No variant had been run and no
equity curve existed. It is a reframing of 26.2's output, not new work: the same single re-cost,
reported as a threshold rather than as a verdict. It changes no criterion, no variant, no cost
cell, no threshold and no trial budget, and it consumes no variant budget for the same
one-directional reason 26.2 gives.**

### 27.1 Why a pass or fail hides the thing worth knowing

26.2 registered a re-cost of the best variant at the execution venue's fees and said the report
would state whether the carry "survives at research rates and dies at execution rates". **That
framing throws away the quantity that actually binds.**

Median funding on this asset class annualises to single digits of per cent. One round trip of a
pair costs 27 basis points of leg notional at research fees and 127 at execution fees. So the
question is not whether fees are survivable in the abstract; it is **how often the book may turn
over before they consume the yield**. Twelve round trips a year erases the whole of a
single-digit carry at execution fees. One or two does not. A verdict of "survives" or "dies"
compresses that into a word and makes the answer useless the moment a fee schedule changes.

**A break-even turnover is reusable.** It is a threshold with the fee level in the denominator,
so a reader with a different fee schedule divides again rather than asking for another backtest.

### 27.2 The three numbers, defined exactly

**All three are round trips per year, and a round trip is the whole book: opening both legs of
every held pair and closing them again.** Defined that way because it is the unit the fee
arithmetic already uses and because a per-pair figure would depend on the position count, which
differs between variants.

**Fees are expressed in basis points of equity rather than of leg notional**, so the numerator
and the denominator are in the same units. One pair consumes `s * (1 + margin_fraction)` of
capital, so one leg's notional per unit of equity at full deployment is `1 / (1 + m)`:

```
fee per full-book round trip, in bps of equity  =  round_trip_fee_bps / (1 + margin_fraction)

research  :  27 / 1.20  =   22.50 bps of equity
execution : 127 / 1.20  =  105.83 bps of equity
```

| number | definition |
|---|---|
| **break-even at research fees** | `gross_return_bps_per_year / 22.50` |
| **break-even at execution fees** | `gross_return_bps_per_year / 105.83` |
| **realised turnover** | `fees_actually_charged_bps_of_equity_per_year / 22.50`, from the fee line the engine charged in the headline cell |

**`gross_return_bps_per_year`** is the best variant's out-of-sample **gross** return — before
fees, spread, slippage and conversion — compounded to an annual rate and expressed in basis
points of equity. **It is reported explicitly as its own number**, so the two break-evens can be
recomputed at any fee level without rerunning anything. That is the point of the reframing.

**Realised turnover is measured by inverting the same arithmetic**, from the fees the engine
actually charged rather than by reconstructing traded notional. That makes the three numbers
definitionally consistent: if the realised figure exceeds the research break-even, the variant's
gross carry did not cover its own fees, and the net return will be negative. No separate
turnover estimator can disagree with the fee line, because it is the fee line.

**The conversion leg is excluded** from the fee figure. It is charged twice for the whole run
rather than per rebalance, so folding it into a per-round-trip number would misattribute a fixed
cost to turnover.

**Spread and slippage are excluded, and this matters.** They are assumptions rather than
published rates, they are identical at both venues, and they also scale with turnover.
**The fee break-even is therefore an upper bound on allowable turnover, not an estimate of it**:
the break-even on total cost is strictly lower. The report says so where the numbers appear.

**One consistency check, registered here so it cannot be skipped.** The re-cost's own measured
fee line must equal `realised_turnover * 105.83` bps of equity per year, to rounding. The two
are computed by different routes — one from a run, one from arithmetic — and a disagreement means
one of them is wrong. It is reported as a matched pair, not as one number.

### 27.3 Declared expectation D2, before anything has run

D1 in 16.1 predicted decay. This predicts *where* the cost pressure acts, and it is registered in
the same form: with a direction, and refutable.

> **Declared expectation D2. The binding constraint on this family is holding period, not signal
> quality.** In two clauses, each of which can be refuted by the numbers the grid already
> produces:
>
> **D2a.** If any variant clears criteria 1 to 6 in the headline cell, it is a **low-turnover**
> one: its realised round trips per year is **at or below the median** of the eight variants'
> realised turnover. *Refuted* if a clearing variant sits strictly above that median.
>
> **D2b.** The best variant's **break-even turnover at execution fees is fewer than 12 round
> trips per year** — that is, a full monthly rebalance would not pay for itself at execution
> fees. *Refuted* if it is 12 or more, which would mean the execution-fee gap does not bind at
> the rebalance frequency this family actually trades.

**D2 carries no weight in the verdict**, exactly as D1 does not. It is stated so that a story
told after the fact about fees and holding periods can be checked against a prediction made
before it. Both clauses are recorded as confirmed, refuted or unresolved, and **unresolved is a
real answer**: D2a cannot be evaluated if no variant clears, and that is reported in those words
rather than quietly dropped.

**What would refute the whole of D2.** A variant whose gross carry is large enough that even 127
basis points a round trip leaves it profitable at monthly turnover. That would mean signal
quality, not holding period, was the binding constraint, and it would make this amendment's
framing wrong. It is a real possibility and it is why D2 is written down.

### 27.4 What amendment 5 does not touch

Criteria 1 to 6 of section 11, the eight variants, the four cost cells, the 32-trial budget, the
seed counts, the funding-evaluability rule and every threshold in sections 6, 7 and 14 are
**unchanged**. 26.2's re-cost is still one variant, one cell, one trial, recorded in the registry,
read by no criterion, and consuming no variant budget. **All that changes is what is printed:
three numbers and a sentence instead of a verdict.**

> **26.2 is amended here.** Its closing paragraph promised a sentence of the form "the carry
> clears at research rates and loses money at execution rates". That sentence is now the
> *conclusion* of a break-even statement rather than the whole of it, and where the two
> break-evens straddle realised turnover the report gives the numbers rather than the word. The
> rest of 26.2 stands unedited.

---

## 28. Amendment 6 — one variant at a slower cadence, and the one-factor comparison it exists for

**Added on the Sponsor's instruction, before the runner existed. No variant had been run, no equity
curve existed and no return, Sharpe or turnover of any kind had been computed for any variant. It
is the only amendment that changes the grid, and it is the last amendment: once the runner starts,
nothing further is added, and any idea arriving after a number exists is post-hoc, counted in full
in the registry and labelled as such in its own section.**

### 28.1 The gap it closes

Section 9.1 fixed the rebalance frequency at monthly for every variant. Cadence was therefore a
single point rather than a range, and the eight variants differed only in signal and position
count.

That was defensible while the fee schedule was the research one. Amendment 5 makes it indefensible.
At the execution venue one full-book round trip costs 105.83 basis points of equity, so the
turnover a given gross carry can pay for is small:

| gross carry, bps/yr | break-even round trips/yr | share of book replaceable, monthly | quarterly |
|---:|---:|---:|---:|
| 200 | 1.89 | 16% | 47% |
| 300 | 2.83 | 24% | 71% |
| 400 | 3.78 | 31% | 94% |
| 600 | 5.67 | 47% | 142% |
| 800 | 7.56 | 63% | 189% |

**Cadence is not turnover, and the difference is why the eight were not simply wrong.** Section 9.1
registers that pairs carry across rebalances and that only the difference between held book and
target book trades. Monthly cadence is therefore an *upper bound* of twelve round trips a year, not
a value, and the eight do span realised turnover — from near buy-and-hold, where a liquidity
ranking barely changes, to near-full rotation, where a 30-day funding ranking does.

**But they span it as a consequence of signal persistence, not as a designed axis, and the slow end
of that span is occupied only by the two variants that have no view of funding at all.** The cell
that is empty is *slow and funding-aware*: the one place this family could work at the venue this
account can actually trade. `carry-rank90-10` is the closest registered point, because a 90-day
trailing sum is smoother than a 30-day one, but it still re-ranks every month.

**No churn was measured before this amendment was written.** Measuring the realised turnover of the
registered selection rules and then choosing the grid on the answer would be selecting the design
on data, which is the thing the trial budget exists to prevent. The gap above is an argument from
the registered specification and from the published fee schedule, and from nothing else.

### 28.2 The variant

| id | signal | N | rebalance months | require positive |
|---|---|---:|---:|---|
| `carry-rank90-10-quarterly` | `funding_90` | 10 | 3 | no |

Every other choice in section 9.1 is unchanged: the same signal instant rule, the same entry and
exit prices, the same equal-capital sizing, the same tie handling, the same carrying rule. The
signal is evaluated, and the book is changed, only at rebalance instants three months apart —
00:00 UTC on the first of January, April, July and October — and the return series remains monthly.

**Why `funding_90` and not `funding_30`.** A 90-day trailing sum sampled quarterly reads
non-overlapping windows, so the lookback matches the holding period. A 30-day signal sampled
quarterly would act for three months on a one-month reading, which is a different and worse
hypothesis.

**Why quarterly and not slower.** The table in 28.1 answers it: at a 4 per cent gross carry,
quarterly cadence pays for replacing 94 per cent of the book at every rebalance, so quarterly
already sits inside the survivable region and nothing slower is needed to reach it. One cadence
point, not a ladder.

### 28.3 The one-factor comparison, and the reporting requirement

**`carry-rank90-10` and `carry-rank90-10-quarterly` are reported as a pair.** Same signal, same
lookback, same position count, same universe, same cost cells; cadence is the only difference
between them. Whatever separates their results is cadence, and that is the quantity this amendment
exists to measure. The report states the pair explicitly rather than leaving a reader to notice
that two rows differ in one field.

**Registered against a straightforward abuse.** A one-factor comparison is only one-factor if
neither member is quietly re-specified, so both members are named here and neither may be altered.

**Its rebalance count is printed beside its result every time it appears.** Twenty-three quarterly
rebalances against roughly seventy monthly ones for its siblings, on the same window, so its
evidence is thinner by construction. A reader must see that where the number is, not by looking it
up: a variant whose result rests on a third as many decisions is not comparable to its siblings on
the strength of the number alone. This applies to every table, every JSON record and every
sentence in which the variant's result appears.

### 28.4 What it costs

**The trial budget goes from 32 to 36**: nine variants across the same four cost cells. The
allowance remains the exact product, with no reserve, and the runner still refuses an unregistered
variant, an unregistered cell and any charge past the allowance.

**The deflation cost is small, and it is small for a reportable reason.** The registry the Deflated
Sharpe Ratio reads is dominated by null draws, not by grid trials:

| grid | projected registry trials | expected-maximum-Sharpe factor |
|---|---:|---:|
| 8 variants | 12,303 | 3.9110 |
| 9 variants | 13,309 | 3.9300 |

A 0.48 per cent higher deflation benchmark. Counted against the grid alone it would have been 4.27
per cent; the honest registry count is what makes the marginal variant cheap, which is the opposite
of the usual direction and worth stating plainly.

### 28.5 What it does not touch, and the grid closing

Criteria 1 to 6, the four cost cells, the seed counts, the funding-evaluability rule, the universe
rules, every threshold in sections 6, 7 and 14, declared expectations D1 and D2, and amendment 5's
break-even definitions are all **unchanged**. The eight existing variants are unchanged in every
field.

> **9.1 is amended here.** Its table row `rebalance frequency | monthly` now reads *monthly for the
> eight variants of 9.2; quarterly for `carry-rank90-10-quarterly`*. The per-variant field
> `rebalance_months` carries it, and the drift guard compares it for every variant. Nothing else in
> 9.1 changes.

> **9.2's closing paragraph is amended here.** "Three questions and no more" is now four: cadence
> is the fourth, asked at exactly one point, against exactly one sibling. The original reasoning
> stands unedited and the three questions it describes are unchanged.

### 28.6 What happens between rebalances, and before the first one

**Added as `v1.7.1`, before the runner produced a single number, and it changes no
registered choice.** 28.2 said the book is "held unchanged in between". Three things follow from
that plus the rules already registered elsewhere, and they are written down here because they
change the numbers and a competent stranger would otherwise have to guess.

**A sourced delisting still forces an exit.** The engine writes down and sells any held position
whose series end takes a haircut, whatever the allocation asks for. That is section 7's rule and it
applies to every variant on the same terms; cadence does not buy a pair the right to stay.

**A pair that leaves the carry universe is dropped, and not re-bought.** A remembered target whose
legs are no longer in the point-in-time candidate set is removed from the target. This is forced
rather than chosen: keeping it would ask the engine to re-buy an instrument the point-in-time
universe no longer admits, which is precisely how a delisted name gets re-entered and a
survivorship bias gets built.

**So the book between rebalances can only shrink, never grow, and the freed capital sits idle until
the next rebalance instant.** That follows from section 9.1's weighting rule rather than adding to
it: the denominator is the registered position count, so a pair leaving does not concentrate the
others. It is also the conservative direction — the variant is never handed an extra selection
decision between the rebalances it was granted.

**Before the first rebalance instant, nothing is held.** 28.2 registers the signal as evaluated
*only* at quarterly instants, so if the first out-of-sample month is not one of them the variant
starts in cash rather than taking a rebalance the registration does not grant. **The affected
months are reported beside the variant's result**, because they are an asymmetry against its
siblings and not a rounding detail.

**None of this is a new degree of freedom.** Each item resolves what the registered text left
implicit, in the only direction that does not either invent a rule or reintroduce a bias, and each
is asserted by a test rather than described.

### 28.7 The grid closing

**The grid is now closed.** Nine variants, four cells, 36 trials. On exhaustion F1 is closed: no
tenth variant, no adjusted parameter, no additional lookback, no further cell and no further
cadence. If the nine do not clear the criteria, that is the answer.

---

## 29. Amendment 7 — void runs, and how the trial registry counts them

**Added on the Sponsor's instruction, before the numbers of the second execution had been
looked at. The first execution's numbers had been seen and are void for the reasons below; the
second execution was still running when this was written and no figure from it had been read.
This section is a rule about counting, not a change to any variant, cell, criterion, threshold
or budget.**

### 29.1 Why the rule is written now

The trial registry is append-only and hash-chained, and the Deflated Sharpe Ratio reads its
count. That is what makes the deflation honest: a search cannot be un-run.

It also means an **engineering accident inflates the bar**. The first execution of this family's
grid ran to completion and reached a verdict with every funding line at exactly zero, because
the engine was built without the published settlement schedule and with the delisting haircut on
one side rather than either. Two registered values, verified by the drift guard, read by no code
path. Re-running left a second set of rows in the registry, and the DSR now deflates against
both.

For one repeat that is conservative and correct. Repeated, it stops being either: a family could
become unclearable because its runner was rewritten three times, which is a fact about this
project's engineering and not about the market. **A bar that moves for reasons unrelated to the
strategy space is not a statistical control, it is noise with a threshold attached.**

**So the rule is written before anybody knows whether it helps or hurts.** At the moment of
writing, the second execution's figures do not exist in any form anybody here has read. Deciding
this after a number exists is precisely the thing this apparatus is built to prevent.

### 29.2 What makes a run void

A run is **void** if and only if at least one of the following is true of it, and each is a
statement about the *machinery*, never about the result:

1. **A registered parameter was not read by the code path that ran.** The value the runner used
   differed from the value the committed configuration declares, for any reason: a keyword never
   passed, a default left in place, a constant compared by the drift guard and consumed by
   nothing. The two defects above are both of this kind.
2. **An accounting identity failed.** The ledger did not reconcile, the hedge did not cancel, a
   cost line was double counted, or funding entered the accounting more or less than once.
3. **The dataset changed underneath the run.** The archive fingerprint recorded in the results
   does not match the bytes on disk, or the run read a file that was rewritten while it ran.
4. **The run did not complete.** It raised, was killed, or wrote a partial result file.

**A run is not void because its answer was disliked, was surprising, was worse than expected, or
was worse than another run's.** No property of the *returns* can make a run void. If the reason
being offered mentions a Sharpe, a net return, a criterion or a verdict letter, it is not a
voiding reason and the run stands.

### 29.3 Who declares it, and when

**The Product Owner declares a run void, in writing, naming the clause of 29.2 that applies and
the specific defect. Never the Developer, and never the runner.** *(The role was written as "the
Sponsor" in `v1.8` and corrected in `v1.9`; section 30.1 states why, and the reason is not
seniority.)*

**The reason is conflict of interest.** The party that produced a run must not be the party that
decides it did not happen. A Developer who may void his own output can discard a result and call
the discarding a repair, and nothing in the artefacts would distinguish the two: a defect is
always available to be found in code one wrote oneself, and the search for one is more diligent
when the number is unwelcome. Separating the roles removes the question rather than answering it.

**The Developer's part is evidentiary and it is bounded**: name the defect, name the clause of
29.2 it falls under, and exhibit the regression test that now covers it. Producing that evidence
is not a declaration and does not become one by being convincing.

**The declaration is refused if the reason offered mentions a return, a Sharpe, a criterion or a
verdict letter**, per 29.2. The declaration is recorded in the results document of the family it
concerns and in the commit that supersedes the run.

**The declaration must name a defect that a test now covers.** A void run whose cause is not
regression-tested is a void run that will happen again, and the second occurrence would be
indistinguishable from an excuse. Both defects above are covered: a parametrised test asserts
each registered keyword reaches the engine wiring, the runner refuses to write a carry result
whose funding line is zero throughout, and fifteen invariants pin the hedge and the funding
identity.

### 29.4 How a void run is counted

**Its rows stay in the registry. Nothing is ever deleted, edited or excluded from
`research/trial-registry.jsonl`.** The chain would not survive it and the file's whole value is
that it cannot be revised.

**Its rows are counted in the DSR's trial count.** The count the deflation reads is the registry's
own count, including void executions, and no filtered count is substituted for it. The reported
figure is therefore conservative: it deflates against more searches than the strategy space was
actually searched with.

**Its rows carry a note saying so.** Every row written by a re-execution carries, in its `note`
field, the reason the previous execution was void. The registry is then self-describing: a reader
counting rows can see which of them are repeats and why, without being told.

**The deflation cost of the repeat is reported explicitly**, as a pair of figures: the DSR at the
registry's full count, which is the one every criterion is judged on, and the DSR at the count
excluding void executions, which is reported **for information only and never used**. If the two
straddle the 0.95 threshold, that fact is stated in the verdict in those words, and **the verdict
follows the full count**. A criterion judged on the smaller number would be a criterion loosened
after a result was seen.

### 29.5 The ceiling, and what happens at it

**A family may be re-executed at most twice for void runs.** That is three executions in total.
On a third void run the family is **suspended rather than re-executed**: its verdict is reported
as **(C)**, stating that the machinery could not be made to produce a trustworthy result inside
the task's budget, and naming every defect found. **(C) is the honest answer there** — the data
and the statistics could not distinguish an edge from noise because the apparatus measuring them
was not trustworthy, which is a real finding about this project's readiness and not a
disappointment to be worked around.

**A suspended family is never converted to (A) or (B) by a fourth attempt inside this task.** If
it matters enough to retry, it is a new pre-registered version in a new task, with the registry
carrying every row of every attempt.

### 29.6 What this does not touch

The nine variants, the four cost cells, the 36-trial budget, criteria 1 to 6, the seed counts,
the null constructs, declared expectations D1 and D2, and every threshold in sections 6, 7, 8, 11
and 14 are **unchanged**. This section governs how executions are counted and who may declare one
void. It grants no new trial, relaxes no threshold, and cannot make a failing variant pass.


## 30. Amendment 8 — who may void a run, and when the spread sample is worth its bytes

**Added on the Product Owner's instruction, before any figure of the second execution had been
read. Two items: a correction of authority in section 29, and one new computed condition, rule
S1, on an acquisition. Neither changes a variant, a cell, a criterion, a threshold or the
budget, and neither can make a failing variant pass.**

### 30.1 The declaring authority, fixed so a reader cannot get it wrong

Section 29.3 as written in `v1.8` said the Sponsor declares a run void and that the developer
does not. That is close to right and ambiguous in the way that matters, because this document
outlives the conversation it was written in and "you declare it" resolves to whoever is reading.

**The role is the Product Owner.** Not the Developer, in any circumstance, and not the runner.
Written as a role and not as a name, because a name in this position is a person who may be
absent and a role is an accountability that is not.

**The reason is conflict of interest, not seniority.** The party that produced a run must not be
the party that decides the run did not happen. The failure mode is not dishonesty, which is why
stating it as a rule is worth more than trusting a disposition:

- A defect can always be found in code one wrote oneself. The supply is inexhaustible.
- The diligence of the search for one is not independent of whether the result was welcome.
- A void declaration and a repair produce **identical artefacts**: a commit, a test, a rerun.
  Nothing downstream can tell them apart, so the control has to sit upstream, at who may declare.

**So the Developer's part is evidentiary and bounded.** Name the defect, name the clause of 29.2,
exhibit the test that now covers it. That evidence can be complete and compelling and it is still
not a declaration. The declaration is the Product Owner's, in writing, and it is refused if its
stated reason mentions a return, a Sharpe, a criterion or a verdict letter.

This is recorded in `config/spike-006-f1.yaml` as two fields rather than one sentence,
`declared_by_role: product-owner` and `never_declared_by_role: developer`, and the drift guard
compares both against named constants. Prose can be read two ways. A pair of guarded fields
cannot.

### 30.2 Rule S1 — the spread sample is acquired only if something earns

Section 12 registers a spread sample: 6 perpetuals, 6 days, from the `bookTicker` tree. At 50 to
90 MB a symbol-day that is **36 symbol-days and 1.8 to 3.2 GB**.

Section 12 fixed *what* would be measured, by rule, before any measurement. It did not fix
*whether* the measurement is worth making, and that question has an answer which depends on the
result file — which means it must be registered as a condition now, exactly as rule C3 was, or it
becomes a judgement made after a number exists.

**Rule S1. The spread sample is acquired if and only if at least one registered variant, in at
least one registered cost cell, earns a net return over the scored out-of-sample window that is
strictly greater than zero.**

Stated exactly, because a trigger with a soft edge is a decision deferred rather than made:

| | |
|---|---|
| quantity | net return over the scored out-of-sample window, after every cost line |
| threshold | `0`, strictly greater than, with no margin |
| variants considered | all nine registered variants |
| cells considered | the four registered cells, every one at the research venue's schedule |
| cells excluded | `kraken_execution`, a different fee schedule and so a different condition |
| decided from | `research/spike-006-f1.json`, by `spread_acquisition` in the analysis module |
| if true | acquire the 36 symbol-days. No subsampling: section 12's sample is already the minimum |
| if false | do not acquire, and report that S1 was false and which figures made it false |

No margin is added around zero on purpose. A margin would be a number chosen with the shape of
the answer already visible, which is the one thing every rule in this document exists to prevent.

**Why this condition and not another.** Spread is a configured assumption, and the measurement
can only ever move a variant in one direction: worse, or unchanged. Two cases, and they are not
symmetric.

1. **Nothing earns at research fees.** Then the family's gross carry is already cancelled by its
   basis risk and its fees, and a measured spread — however large — changes no conclusion. Three
   gigabytes would refine a cost line on a book that does not earn. The measurement would be
   activity, not evidence.
2. **Something earns at research fees.** Then spread is precisely the cost that could kill it,
   the assumption is load-bearing for the verdict, and the measurement becomes the most valuable
   3 GB in the task.

The same sequencing as rule C3, for the same reason: acquire the minimum the registered rules
need, decide what that minimum is by a rule fixed beforehand, and never let the size of a
download be argued from a result.

**What S1 does not do.** It does not make the spread assumption a measurement in the false case,
and it does not make it one in the true case either — invariant 12 stands, section 12's sample
sits *beside* the assumption, and every variant remains costed at the assumption in every cell.
A decision not to measure is not a claim that the assumption was right. It is a statement that
the number it would produce cannot change the answer, which is a different and much weaker claim,
and the report makes it in those words.

### 30.3 Cadence and turnover, corrected

Not a rule and not a change to anything registered. Amendment 6's argument for a quarterly
variant assumed that a slower cadence trades less. **That assumption is wrong as stated**, and it
is recorded here rather than quietly dropped, because the amendment's reasoning is part of the
pre-registration and a reader is entitled to see which part of it did not survive.

On a controlled fixture whose liquidity ranking rotates, the quarterly variant turned over *more*
than its monthly twin: 9132.49 against 9074.48 of notional traded. The mechanism is drift.
Skipping two decision instants defers the trades those instants would have made rather than
removing them; the weights drift from target for three months instead of one, and the single
correction at the quarter's end can exceed the two that were skipped. Whether it does depends on
how fast the ranking moves relative to the cadence, which is a property of the market.

**This raises the value of the quarterly cell rather than lowering it.** A monotonic relationship
could have been derived from the fee schedule alone, and the cell would have been an expensive
confirmation of arithmetic. A non-monotonic one cannot be derived: where the fee-optimal cadence
sits is a measurement. The regression test asserts that cadence changes turnover and deliberately
does not assert a direction, because a direction is what the fixture just refuted.

### 30.4 What this does not touch

The nine variants, the four cost cells, the 36-trial budget, criteria 1 to 6, the seed counts,
the null constructs, declared expectations D1 and D2, rules C1 to C3, section 12's sampling rules
themselves, and every threshold in sections 6, 7, 8, 11 and 14 are **unchanged**. 30.1 changes who
may declare a run void. 30.2 adds one condition on one acquisition. 30.3 records a correction to
an argument. None of them grants a trial, relaxes a threshold, or touches a computed value.


## 31. Amendment 9 — rule S1 generalised, and two findings that travel to every family

**Registered after F1's figures were read, on the Product Owner's instruction. That is stated
first because every other amendment in this document was written before the numbers it governs
existed, and this one was not.** What it may and may not do is bounded in 31.4, and the bound is
the reason it is admissible at all: it governs an *acquisition*, and no acquisition in this task
can change a computed result.

### 31.1 What was wrong with amendment 8's version

Amendment 8 registered rule S1 as: acquire the spread sample if some variant earns a positive net
return at research fees. On F1 that was **false**, so the sample was not acquired.

But the assumed spread sits *inside* the charges that produced the negative net. The assumption
therefore helped prevent the measurement that could have corrected it, and the report named that
circularity. A rule with that shape answers a question about the run rather than about the
assumption.

### 31.2 The generalised rule

**An assumed cost is measured only when its assumption could plausibly be carrying the verdict,
and the test is computed rather than argued.**

> **Rule S1 (amendment 9).** Set the assumed cost to zero and re-evaluate the run. The sample is
> acquired when some variant then reaches a net return **and** a Sharpe that would themselves
> clear **criterion 1**: strictly positive net return over the scored window, and an annualised
> Sharpe above the 95th percentile of that variant's own exposure-matched null.

**A sign change is not enough, and that is the whole point of the bar.** A variant that crosses
zero and lands at a return too small to clear its own null has not been rescued by the
assumption; it has been rescued by rounding. Measuring the assumption would change nothing about
the verdict, and acquiring data to refine a cost line on such a variant is acquiring data no rule
reads.

**How the counterfactual is computed.** The removed cost is added back in equal instalments
across the scored months, each converted to a return on that month's opening equity along the
realised path. That is exact in the total and in the sign of the net return, and approximate in
the volatility, because the true charge follows each month's turnover and is lumpier than a
constant. **From F2 the runner records per-month assumed costs**, the test is computed exactly,
and this model is retired. Where the model is used, the report says so beside the figure.

### 31.3 What it decides for F1, which is not what the instruction expected

**The rule fires.** With spread and slippage set to zero, two of the nine variants clear
criterion 1:

| variant | net at zero assumed cost | Sharpe at zero | its null's 95th percentile | clears |
|---|---:|---:|---:|---|
| `carry-basket-5` | +1.48% | 0.091 | -1.262 | **yes** |
| `carry-rank90-10-quarterly` | +1.72% | 0.097 | -1.560 | **yes** |
| the other seven | -8.33% to -80.52% | -0.256 to -0.863 | -1.262 to -1.575 | no |

**The mechanism does not match the intent it was written with, and the reason is that the null
bar is negative.** The instruction registering this rule expected it to stay unfired, on the
reasoning that a variant landing at 18.70 or 39.48 EUR over 56 months is too small to clear its
own null. On this data it is not: the exposure-matched null is *itself* losing, at a 95th
percentile Sharpe between -1.26 and -1.58, so a counterfactual Sharpe near zero clears it
comfortably. The bar is a low one here in absolute terms, and criterion 1 was never designed to
be an absolute bar.

**Both readings are recorded rather than one being chosen quietly.** The rule as written fires.
The intent as stated would not have. Which of the two governs the acquisition is the Product
Owner's to settle, and until it is settled the sample is not acquired, because a 1.8 to 3.2 GB
download made on a reading of a rule its own author did not expect is exactly the kind of
decision this apparatus exists to make visible rather than convenient.

### 31.4 What amendment 9 cannot do, and why a post-hoc acquisition rule is admissible

**It cannot change an F1 number.** Every variant remains costed at the registered assumption in
every cell, under invariant 12. Section 12's sample sits *beside* the assumption and is never
substituted into the cost model. So the strongest thing an acquisition can produce is a labelled
measurement printed next to a labelled assumption.

**It grants no trial, relaxes no threshold, adds no variant and touches no criterion.** Criterion
1 appears in the rule as a *bar for a counterfactual*, not as a criterion being re-evaluated: no
variant's criterion 1 answer changes, and the verdict letter is unaffected.

**It is reported as post-hoc wherever it appears.** The results document labels the rule with the
version that registered it and states that it was registered after the figures were read. A
reader must never have to reconstruct that ordering from git to discover it.

### 31.5 Two findings carried into F2 and F3 rather than rediscovered

**The currency leg is reported as its own line in every family, and is never folded into fees.**
F1 measured it at 20.3 per cent of the best variant's toll against 13.7 per cent in exchange
fees. A euro-funded account trading USDT-quoted instruments pays a currency toll larger than the
venue's own; it scales with turnover rather than sitting fixed per run; and it will land
identically on any family quoted away from the account's currency, which is all of F2, F3 and
F6. Folding it into fees would hide the larger of the two and would misattribute a structural
cost of this account to the venue's schedule.

**The family-level result is the headline, not the best variant's.** Across F1's nine variants
the price legs returned 2,094.76 of the 2,113.62 received in funding, leaving **18.86** on 1,500
of equity. The best variant cleared 227.53. The family-level figure is the finding, and it is
stated in these terms: *the premium is compensation for the basis risk that earns it, priced
close to exactly*. A best-variant headline would have reported the opposite impression from the
same run, and every family from here reports the grid before it reports its best member.

### 31.6 What this does not touch

The nine variants, the four cost cells, the 36-trial budget, criteria 1 to 6, the seed counts,
the null constructs, declared expectations D1 and D2, rules C1 to C3, section 12's sampling rules
and every threshold in sections 6, 7, 8, 11 and 14 are **unchanged**.


## 32. Amendment 10 — the defect in amendment 9, a floor from F2, and a measured spread

**Three things. The first is a record, the other two are prospective and take effect at F2.
Nothing here changes an F1 figure, an F1 criterion or F1's verdict.**

### 32.1 The drafting defect in amendment 9, recorded and attributed

**The Product Owner's own account, recorded at the Product Owner's instruction:**

> My drafting was defective. I wrote that a sign change is not enough, then wrote a test that
> accepts one, because criterion 1's null is losing and a variant earning 18.70 EUR satisfies both
> of its clauses at once.

**The rule was honoured because it fired, not because acquiring was convenient.** Amending a rule
to avoid the action it requires, after seeing that it requires that action, is the precise move
this apparatus exists to prevent. That the rule and the defect both belong to the Product Owner
changes nothing about that: the whole value of a pre-registered rule is that its author is bound
by it too, and a rule its author may reinterpret on sight of its output is not a rule.

So the sample was acquired under rule S1 as amendment 9 wrote it, and the correction below applies
to families that have not run.

### 32.2 Rule S1 gains a floor, from F2

> **The floor.** From F2, the counterfactual must clear the null's 95th percentile **by at least
> one standard error of its own Sharpe estimate**, computed with the skewness and kurtosis
> correction rather than the normal approximation.

**Stated as a principle and not as a number**, deliberately. A euro threshold chosen now would be
a number picked after seeing which euro figures were in play, which is the same defect in a
different costume. A threshold in units of the estimate's own uncertainty is fixed by the data
rather than by the author.

**What it means.** An assumption is worth measuring when removing it could produce a result
*distinguishable from noise*, not merely one with a different sign.

**The corrected standard error is used because the plain one is a floor rather than a measurement.**
`PerformanceStatistics.sharpe_standard_error` assumes normal returns and says so in its own
docstring. The corrected form uses the third and fourth moments the series actually has, is larger
on a fat-tailed negatively skewed series, and therefore sets a higher bar. It is computed from the
same variance term the Probabilistic Sharpe Ratio already uses, held in one place so the two
cannot disagree.

**It does not apply to F1.** F1 is judged by the bar registered when it ran. Applying a bar
retrospectively would be amendment 9's defect in the opposite direction.

### 32.3 What the floor does to F1's figures, recorded because they exist

**It would not have changed F1's answer.** Both clearing variants clear the null by far more than
one standard error:

| variant | Sharpe at zero | null's 95th percentile | one standard error | the floor's bar | clears |
|---|---:|---:|---:|---:|---|
| `carry-basket-5` | 0.091 | -1.262 | 0.484 | -0.778 | **yes** |
| `carry-rank90-10-quarterly` | 0.097 | -1.560 | 0.472 | -1.088 | **yes** |

**A floor stated relative to a deeply negative null is not a floor in absolute terms.** The null's
95th percentile sits at -1.26 to -1.58 because the exposure-matched null is itself losing over
this window, and one standard error is about 0.48. Adding it moves a bar that is already far below
zero to a bar that is still far below zero.

**So the intent is still not expressed by the mechanism, and this is recorded rather than
quietly fixed.** A bar stated relative to **zero** — the counterfactual's Sharpe must exceed zero
by one standard error of itself — *would* have left rule S1 unfired on F1, because 0.091 and 0.097
are both smaller than their own standard errors of 0.484 and 0.472. Which of the two the intent
means is **open, and it is the Product Owner's to settle before F2 runs**. The Developer's part is
to have computed both and to have changed neither.

### 32.4 The measured spread becomes the default from F2

**From F2 the spread measured under rule S1 is the default cost, per liquidity band, and the
registered assumption is retained beside it as a labelled alternative.** Every F2 and later result
states which of the two it was computed at.

**Why, in one line: the assumed spread is 41.2 per cent of F1's toll and four families have still
to run on it.** It is the largest single line in the cost model and it was a guess. Measuring it
once replaces the guess for everything that follows. If the measurement is close to the
assumption, every later result is stronger for it; if it is far off, that is learned before four
more families run on a wrong number rather than after.

**Invariant 12 is not weakened by this and is the reason it is safe.** A measured value is
reported as a measurement and an assumed value as an assumption, with the label attached wherever
the figure appears. A band the sample did not reach keeps its assumption, labelled as one — and
section 12's sample reaches only the **deep** band, because every member of the carry universe at
2023-05-15 sits above the deep band's floor. The mid and thin assumptions remain assumptions and
are reported as such.

**F1 is unchanged.** It remains costed at the assumption in every cell, and the measurement is
printed beside its cost table and never substituted into a computed figure.

### 32.5 What this does not touch

The nine variants, the four cost cells, the 36-trial budget, criteria 1 to 6, the seed counts, the
null constructs, declared expectations D1 and D2, rules C1 to C3, and every threshold in sections
6, 7, 8, 11 and 14 are **unchanged**. 32.1 records; 32.2 and 32.4 take effect at F2.


## 33. Amendment 11 — the floor anchored to zero, rule P1, and an estimated spread

**Three settlements. Every one applies from F2. None of them touches an F1 figure, an F1 criterion
or F1's verdict letter, and section 33.5 says what each would have done to F1 had it applied,
because those numbers exist and hiding them would be the same class of move this apparatus exists
to prevent.**

### 33.1 Rule S1's floor is anchored to zero

> **The floor, as settled.** From F2, rule S1 fires when some variant, with its assumed cost set to
> **zero**, has an annualised Sharpe exceeding **zero** by at least one standard error of its own
> estimate, skewness- and kurtosis-corrected, **and** exceeding the 95th percentile of its own
> exposure-matched null. **Both clauses, never either.**

**Why zero and not the null.** The exposure-matched null over this window is itself losing: its
95th percentile sits at -1.262 and -1.560 for the two variants in question. A bar anchored to it is
a bar below zero, and a variant that merely fails to lose clears it. Adding one standard error of
about 0.48 to a bar at -1.26 moves it to -0.78, which is still a bar that no profitable strategy
needs to reach. That is not what the intent said. The intent was that removing an assumption must
be able to produce a result *distinguishable from noise*.

**Amendment 10's floor is superseded, not deleted.** Both forms stay in this document. Amendment 10
states the bar as it was first written; this states the bar that applies. On F1's figures the
null-anchored form would have fired and the zero-anchored form would not have, and neither was
substituted for the other after the fact.

### 33.2 Rule P1: every null comparison is paired with an absolute test

> **Rule P1.** Every criterion that compares a variant to a null is paired with an **absolute** test
> against zero.

**Why a standing rule rather than a third patch.** "Better than a losing null" and "makes money"
are different claims, and this project has confused them in three different places: the
exposure-matched null in SEXTANT-004, criterion 1 as written in section 11, and both of the Product
Owner's attempts at a floor for rule S1. A patch repairs one site. The defect is in the *shape* of
the comparison, so the rule is registered at the level of the shape.

**The mechanism a null comparison is good for and the one it is not.** A null answers "could a
process with no edge have produced this?" It cannot answer "is this worth doing?", and over a window
where the null loses money the two answers come apart by the whole size of the null's loss. Both
questions have to be asked explicitly, because either one alone passes something it should not.

### 33.3 Criterion 1, strengthened from F2

| | criterion 1 |
|---|---|
| **as written, and as F1 was judged** | Sharpe above the null's 95th percentile, **and** a strictly positive net return |
| **as strengthened, from F2** | Sharpe above the null's 95th percentile, **and** a mean scored-period return exceeding one standard error of that mean |

The absolute clause becomes a *t*-statistic: the mean return over its own standard deviation, times
the square root of the observation count, strictly greater than one.

**The weakness being repaired.** "Strictly positive" would pass a variant earning 18.70 EUR on
1,500 across 56 months. That is a positive number. It is not a return distinguishable from nothing,
and section 7.4's rescue test is where it nearly mattered.

**This can only ever remove a pass.** A mean exceeding its own standard error is positive, so
everything the new clause admits the old clause already admitted. The change is strictly narrowing,
in every family, on any data. That property is the whole reason it is safe to register *after* F1's
figures are known — an amendment that could only ever make the bar harder cannot have been chosen to
let something through.

**It is not applied to F1.** F1 was judged on the weaker form and its verdict letter is not
reopened. `docs/SPIKE-006-F1-RESULTS.md` section 13.1 re-reports F1's existing figures against the
strengthened clause as a clearly labelled **supplementary reading**, which states in its first line
that F1 was judged on the weaker form. A supplementary reading is not a re-scoring.

### 33.4 Rule E1: the banded spread assumption is replaced by an estimate, from F2

**The band structure is the defect, and a better constant cannot repair it.** The bands are cut on
quote turnover. Spread does not respond to turnover. Section 7.5 measured two orders of magnitude of
spread *inside a single band*: 0.0357 bps on `BTCUSDT` against 4.2452 bps on `API3USDT`, both deep,
both above the deep band's floor by a wide margin. A partition cut on the wrong variable stays wrong
however carefully each cell is filled in.

**So the replacement is per instrument and per period, estimated from prices the archive already
holds.** This also supersedes amendment 10's "the measured spread becomes the default": six measured
symbol-days cannot be a default for a five-year window. What they are good for is calibrating
something that can be.

**The registered estimator.** Abdi, F. and Ranaldo, A. (2017), *A Simple Estimation of Bid-Ask
Spreads from Daily Close, High, and Low Prices*, Review of Financial Studies 30(9), 4437-4480. With
`c` the log close, `h` the log high, `l` the log low and `eta = (h + l) / 2` the log mid-range, the
two-day term is

```
s2_t = 4 * (c_t - eta_t) * (c_t - eta_{t+1})
```

and the period estimate is the square root of the **mean** of `s2_t` over the period. A non-positive
mean is reported as zero and counted, never rooted. The result is a proportional effective spread;
basis points are 10,000 times it.

**Why this one.** It reports the higher correlation with measured effective spreads of the two
candidates, it needs only close, high and low — exactly what the archive holds — and it contains no
overnight-gap adjustment, so there is no step in it at which an implementer chooses anything.

**Corwin and Schultz (2012) is computed beside it, for comparison only.** Two-day estimates below
zero are set to zero before averaging, which is the paper's own correction, and the overnight
adjustment is applied as the paper states it. **It is never adopted if Abdi-Ranaldo fails rule E1.**
Adopting whichever of two estimators passes, after seeing which one passed, is selection with one
extra step in front of it.

#### Rule E1's acceptance test, written before the calibration was run

> **All three clauses must hold.** Computed on `research/spike-006-f1-estimator.json`, in exactly
> the way rules C3 and S1 are computed conditions on the result file.
>
> **Ordering.** The Spearman rank correlation between estimated and measured spread across the six
> symbols is at least **0.771**, the one-tailed 5 per cent critical value at n = 6. A cost model
> that misranks its instruments is worse than a constant, because a cross-sectional strategy trades
> the ranking.
>
> **Magnitude.** The median across the six symbols of `|log2(estimated / measured)|` is at most
> **1**: for at least half the symbols the estimate is within a factor of two of the measurement.
> Against a sample spanning two orders of magnitude, a factor of two is a real test.
>
> **Positivity.** Over the F1 window, taking every perpetual with enough stored history in its
> trailing window at every month end, the estimator returns a strictly positive figure for at least
> **90 per cent** of the instrument-periods asked of it. A cost model cannot charge zero for a
> spread, and an estimator that is frequently non-positive is not a cost.

**If it passes**, the estimate becomes the default spread cost from F2, per instrument and per
period, with the banded assumption retained beside it as a labelled alternative, and every F2 and
later result states which of the two it was computed at.

**If it fails**, the banded **assumption** is kept, labelled an assumption exactly as it is now, and
the failure is reported with its numbers. A worse number that looks like a measurement is worse than
a guess that is labelled a guess. No third estimator is tried, no clause is relaxed, and the
comparison estimator is not promoted.

#### What the calibration compares is not identical, and that is said before the numbers

The estimator estimates an **effective** spread and section 7.5 measured a **quoted** spread. Where
trades print at the touch the two coincide; where they do not, effective is the smaller. An estimate
that sits below the measurement by a small factor is therefore consistent with the estimator being
right, and saying so afterwards would be indistinguishable from an excuse.

The calibration month *contains* the day it is compared against. That would be look-ahead in a
trading decision and is not one here, because nothing is traded on a calibration. The window that
prices a trade from F2 is a different window and is registered as such.

#### How the estimate is applied from F2

**Point-in-time.** The figure charged at a decision instant is estimated from the 30 stored daily
bars ending **strictly before** that instant, the same trailing window the liquidity bands are
already cut on. Estimating from the period a trade falls in would use the trade's own month to price
the trade.

**Too little history.** An instrument with fewer than 20 usable two-day pairs in its trailing window
is charged at the banded **assumption** for its band, labelled as one, and the number of such
charges is reported. It is never charged zero and never dropped from the universe for it.

**Halving.** The estimate is a full spread and the cost model charges a half-spread per leg, so half
the estimate is what is charged. Section 7.5's measured comparison is stated the same way and for
the same reason.

**Invariant 12 gains a third provenance rather than losing a distinction.** An estimated spread is
reported as an **estimate** — neither a measurement nor an assumption — with its estimator named and
its calibration error beside it wherever a cost line depends on it. What would weaken the invariant
is letting an estimate print as a measurement.

**Slippage is untouched.** Nothing here estimates slippage. It is an intraday quantity, daily bars
cannot calibrate it, and it remains an assumption at its registered figures with its label attached.

### 33.5 What each of the three would have done to F1, and why that is recorded rather than applied

| | would it have changed F1? | how it is handled |
|---|---|---|
| the zero-anchored floor (33.1) | rule S1 would **not** have fired: 0.091 against a standard error of 0.484, and 0.097 against 0.472 | F1's acquisition stands, under the rule in force when it was acquired |
| criterion 1 strengthened (33.3) | no variant cleared criterion 1 in F1's headline cell under the weaker form, so a narrower form cannot change the letter | re-reported as a labelled supplementary reading in section 13.1 |
| the estimated spread (33.4) | nothing: every F1 cell is costed at the assumption, and section 7.4 shows the verdict survives deleting the assumed cost entirely | printed beside the assumption, never substituted |

**Two of the three are recorded against F1 as *not firing*, which is the direction that needs the
most care.** A rule that fires costs data and effort; a rule that does not fire costs nothing and is
therefore the one an author is tempted to write. The protection is that the zero-anchored floor was
settled by the Product Owner after being shown both readings and the figures for each, the
sample it would have prevented was already acquired and reported before it was settled, and the
choice therefore could not save anybody any work.

### 33.6 What this does not touch

The nine variants, the four cost cells, the 36-trial budget, criteria 1 to 6 **as F1 was judged on
them**, the seed counts, the null constructs, declared expectations D1 and D2, rules C1 to C3, and
every threshold in sections 6, 7, 8, 11 and 14 are **unchanged**. Every clause in section 33 takes
effect at F2.

# SEXTANT-006 family F1: cash-and-carry, spot against the perpetual

Every number here was produced by one run of the pre-registered grid and is
reproducible from `research/spike-006-f1.json`, which carries the monthly return
series each figure was computed from. The specification is
`docs/PRE-REGISTRATION-006-F1.md` and `config/spike-006-f1.yaml`, both committed
before the numbers existed; the commit that carries them is cited below and its
ancestry is checkable with `git merge-base --is-ancestor`.

- pre-registration: **v1.7.1**
- engine version: `sextant-006-f1`
- run made from commit: `ce77cf5bf218`
- everything denominated in **EUR**
- **verdict: (B)**

> **This run read `v1.7.1`; the specification is now `v2.0`.** Amendments 7 and 8 were registered while the grid
> was running and neither changes a computed value: amendment 7 fixes how a void
> execution is counted in the trial registry, and amendment 8 fixes who may
> declare one void and makes the spread sample conditional on rule S1. No
> variant, cell, criterion, threshold, seed count or budget differs between the
> two versions, and every figure below would be identical under either. The
> ordering still holds: both amendments were committed before any figure of this
> run had been read.

---

## 1. Pre-registration ordering

The drift guard proves the code matches the configuration. It cannot prove the
configuration was not edited alongside the code after a result was seen. Git
ordering can, and these are the two facts that establish it.

| fact | value |
|---|---|
| specification | `config/spike-006-f1.yaml` |
| its commit | `5fdc4d0f20ad4da38b6cf04bc217a0db22a3ede7` |
| committed at | 2026-09-10T12:33:09+00:00 |
| commit subject | feat(carry): the cadence wrapper, and what it does between rebalances |
| clean at run time | yes |
| repository head at run time | `ce77cf5bf21807d46a4d642c780997e69763a382` |

The run refuses to start if the configuration is untracked or has uncommitted
changes, because in either case no committed SHA describes the bytes the run
read, and citing one anyway would be a false statement about the ordering.

Run `sextant spike-006-f1 ordering` after this document is committed to print
the ancestry check between the configuration's commit and the results' commit.

That command reports the **newest** commit touching the configuration, which is
amendment 8's and not the one whose bytes this run read. Both are ancestors of the
results commit, which is the property being checked. The SHA in the table above is
the one recorded at run time, and it is the one that describes the bytes.

## 2. The trial budget, and what it refused

The budget is the exact grid rather than a ceiling with a reserve behind it: a
reserve is what gets spent after a result is seen. The runner charges before the
engine runs, so a trial outside the declared allowance is refused before it can
produce a number anybody has seen.

| | |
|---|---|
| declared | 36 trials |
| variants | 9 |
| cost cells | 4 |
| charged this run | 36 |
| recorded in the committed registry | 36 |
| remaining | 0 |
| null and benchmark constructs, not charged | 84 |

No post-hoc trial was recorded against this family. The nine variants and the
four cells are what ran, and nothing else.

## 3. The window and the universe

| | |
|---|---|
| first usable month | 2020-11 |
| last usable month end | 2026-08 |
| usable months | 69 |
| out-of-sample | 2021-11-01 to 2026-07-01 |
| scored months | 56 |
| walk-forward folds | 4 |
| carry pairs per rebalance | 24 to 340, median 155 |

The window was resolved by the rule in section 7 from listing metadata and FX
availability alone. No return was consulted to choose it.

## 4. What each cell charges

Fees are the venue's published schedules and differ by leg. Spread, slippage and
the maker/taker fill mix are **assumptions**, not measurements, and are labelled as
such wherever a figure computed under them appears. They are charged on **both**
legs, so a carry round trip costs twice what a single-leg strategy pays.

| cell | spot maker/taker | futures maker/taker | fill mix | spread & slippage | role |
|---|---|---|---|---|---|
| `vip0_maker` | 10 / 10 bps | 2 / 5 bps | maker-only | assumption x1 | reported only |
| `vip0_even` | 10 / 10 bps | 2 / 5 bps | half-and-half | assumption x1 | headline, every criterion judged here |
| `vip0_taker` | 10 / 10 bps | 2 / 5 bps | taker-only | assumption x1 | reported only |
| `stress` | 10 / 10 bps | 2 / 5 bps | taker-only | assumption x2 | runs the nulls |

One full-book round trip, in basis points of one leg's notional:

- research schedule (`vip0_even`): **27.00 bps**, which is **22.50 bps of equity** at a margin fraction of 0.20
- execution schedule (`kraken_execution`): **127.00 bps**, which is **105.83 bps of equity**

## 5. The nine variants in the headline cell (`vip0_even`)

Net of every cost line. The Sharpe's standard error is beside it because over a
window this length it is usually the most important number on the page.

| variant | net return | Sharpe | std err | 95th pct of its null | months | rebalances |
|---|---:|---:|---:|---:|---:|---:|
| `carry-basket-10` | -16.11% | -0.794 | 0.469 | -1.575 | 56 | 56 |
| `carry-basket-5` | -5.74% | -0.248 | 0.464 | -1.262 | 56 | 56 |
| `carry-positive-10` | -57.13% | -1.805 | 0.493 | -1.575 | 56 | 56 |
| `carry-premium-10` | -91.81% | -1.221 | 0.477 | -1.575 | 56 | 56 |
| `carry-rank30-10` | -57.13% | -1.805 | 0.493 | -1.575 | 56 | 56 |
| `carry-rank30-5` | -72.31% | -1.451 | 0.483 | -1.262 | 56 | 56 |
| `carry-rank90-10` | -31.09% | -1.183 | 0.476 | -1.575 | 56 | 56 |
| `carry-rank90-10-quarterly` (18 rebalances) | -15.49% | -0.680 | 0.467 | -1.560 | 56 | 18 |
| `carry-rank90-5` | -35.60% | -1.305 | 0.479 | -1.262 | 56 | 56 |

**The null is the exposure-matched one**: as many carry pairs as the variant held
at that rebalance, drawn at random, leaving the same fraction idle. It inherits the
variant's exposure path exactly and differs from it only in which pairs it holds,
which is what makes it the null that answers whether choosing these assets did
anything.

## 6. Cadence: the one-factor comparison

Amendment 6 registered `carry-rank90-10-quarterly` as `carry-rank90-10` with the
rebalance frequency changed and nothing else: same signal, same lookback, same
position count, same universe, same cost cells. Whatever separates these two rows
is cadence.

| | net return | Sharpe | rebalances | idle months before first rebalance |
|---|---:|---:|---:|---:|
| `carry-rank90-10` | -31.09% | -1.183 | 56 | 0 |
| `carry-rank90-10-quarterly` | -15.49% | -0.680 | 18 | 2 |

The quarterly variant consulted its signal **18** times
against **56** for its monthly twin over the same scored
window. Its evidence rests on that many decisions and not on the month count, which
is why the figure appears beside its result everywhere it is printed.

Section 28.3 anticipated roughly 23 against roughly 70. Those were counts over
the 69-month usable window; the scored window is shorter, so the realised
counts are 18 against 56. The ratio is
the one that was registered; the absolute figures are smaller because twelve
months are spent fitting and are never scored.

Between rebalances the quarterly variant's book can only shrink, never grow: a
sourced delisting still forces an exit, a pair leaving the carry universe is
dropped and never re-bought, and the freed capital sits idle rather than
concentrating the rest. Section 28.6.

### Cadence and turnover are not monotonically related

Amendment 6 was argued for on the assumption that a slower cadence trades less.
**That assumption is wrong as stated, and the correction is recorded here rather
than dropped.** On a controlled fixture whose liquidity ranking rotates, the
quarterly variant turned over *more* than its monthly twin, not less: 9132.49
against 9074.48 of notional traded.

The mechanism is drift. Skipping two decision instants does not remove the trades
those instants would have made, it defers them. The held weights drift from the
target for three months instead of one, and the single correction at the end of
the quarter can exceed the two corrections that were skipped. Whether it does
depends on how fast the ranking moves relative to the cadence, which is a
property of the market rather than of the schedule.

This makes the quarterly cell worth **more** than the amendment claimed, not less.
A monotonic relationship could have been reasoned about from the fee schedule
alone, and the variant would have been an expensive way to confirm arithmetic.
A non-monotonic one cannot be: where the fee-optimal cadence sits is a
measurement, and this is the cell that measures it. The regression test asserts
that cadence changes turnover and deliberately does not assert a direction.

On the archive, in this window, it went the other way from the fixture:

| | rebalances | notional traded | fees | spread | slippage | total cost |
|---|---:|---:|---:|---:|---:|---:|
| `carry-rank90-10` | 56 | 141,036.89 | 95.14 | 285.87 | 124.01 | 306.56 |
| `carry-rank90-10-quarterly` | 18 | 91,566.95 | 61.76 | 189.63 | 82.23 | 128.76 |

The quarterly variant traded **64.9%** of its monthly twin's notional here, so
on this universe the slower cadence did trade less. On the rotating fixture it
traded more. Both are real and they do not contradict each other: the direction
depends on how fast the ranking moves relative to the cadence, which is precisely
why it had to be measured rather than assumed.

## 7. Where the return came from

Four ways of cutting the same book. **Combined** is the variant. **Timing** holds the
whole carry universe at the variant's own invested fraction: its timing, none of its
selection. **Selection** holds the variant's own pairs scaled to full investment: its
selection, none of its timing. **Funding** is the realised settlement stream, which is
the return rather than a cost line and is never blended into fees.

| variant | combined | timing | selection | price legs | + funding | - charges | = net |
|---|---:|---:|---:|---:|---:|---:|---:|
| `carry-basket-10` | -16.11% | -20.77% | -16.11% | 21.55 | -25.81 | 237.46 | -241.72 |
| `carry-basket-5` | -5.74% | -20.77% | -5.74% | 27.06 | 108.70 | 221.88 | -86.12 |
| `carry-positive-10` | -57.13% | -20.77% | -57.13% | -272.51 | 304.79 | 889.28 | -857.00 |
| `carry-premium-10` | -91.81% | -20.77% | -91.81% | -753.42 | 13.75 | 637.44 | -1,377.11 |
| `carry-rank30-10` | -57.13% | -20.77% | -57.13% | -272.51 | 304.79 | 889.28 | -857.00 |
| `carry-rank30-5` | -72.31% | -20.77% | -72.31% | -470.58 | 322.00 | 936.12 | -1,084.70 |
| `carry-rank90-10` | -31.09% | -20.77% | -31.09% | -159.80 | 387.33 | 693.88 | -466.36 |
| `carry-rank90-10-quarterly` (18 rebalances) | -15.49% | -20.78% | -16.26% | -103.62 | 370.68 | 499.44 | -232.38 |
| `carry-rank90-5` | -35.60% | -20.77% | -35.60% | -110.92 | 327.39 | 750.43 | -533.96 |

The first three columns are returns on the account. The last four are amounts in
account currency on the registered starting equity and they add up as written:
price legs plus funding less charges is net. **Charges exclude funding**, because
the ledger books a receipt as a negative cost and its own total is therefore
already net of the carry; subtracting that total from a carry that also contains
the funding would count the funding twice.

**This is the family's whole result in one table.** The funding stream is real and
in most variants it is large. The price legs of a hedged pair should hold only the
basis, and they give most of it back. What the two leave is then smaller than the
cost of trading it.

### 7.1 What the toll is made of

The charges column is where this family dies, so here it is itemised. **Funding is
not in these totals**: a receipt is booked as a negative cost line and the funding
stream sits on the return side of the identity above.

The second pair of columns is `carry-rank90-10-quarterly`, whose carry cleared the most
before any charge: 267.06 EUR of price move plus
funding. It is the most favourable case in the grid.

| part | all nine, EUR | share | the best case, EUR | share |
|---|---:|---:|---:|---:|
| exchange fees | 810.43 | 14.1% | 61.76 | 12.4% |
| spread (assumed) | 2,260.68 | 39.3% | 189.63 | 38.0% |
| slippage (assumed) | 991.73 | 17.2% | 82.23 | 16.5% |
| FX conversion | 1,201.69 | 20.9% | 91.57 | 18.3% |
| delisting haircut | 490.67 | 8.5% | 74.25 | 14.9% |
| **total charged** | **5,755.21** | 100% | **499.44** | 100% |

**Two of the five are assumptions, and together they are 56.5% of the toll.** Spread and slippage
are configured values under invariant 12 rather than measurements. Published
exchange fees are 14.1% of the toll, and the
conversion leg is 20.9%, larger than
the fees themselves.

**So this verdict does not say the venue's fee schedule ate the premium.** It says
the total cost of trading it did, and the majority of that total is two numbers
this project assumed rather than measured. That belongs in the verdict in those
words, and it is the honest reading of what F1 establishes.

Per variant, in the headline cell:

| variant | fees | spread | slippage | FX | delisting | total | assumed share |
|---|---:|---:|---:|---:|---:|---:|---:|
| `carry-basket-10` | 50.46 | 74.86 | 37.41 | 74.73 | 0.00 | 237.46 | 47.3% |
| `carry-basket-5` | 47.18 | 69.88 | 34.94 | 69.88 | 0.00 | 221.88 | 47.2% |
| `carry-positive-10` | 121.27 | 369.30 | 160.06 | 179.78 | 58.86 | 889.28 | 59.5% |
| `carry-premium-10` | 96.88 | 237.30 | 105.43 | 143.92 | 53.91 | 637.44 | 53.8% |
| `carry-rank30-10` | 121.27 | 369.30 | 160.06 | 179.78 | 58.86 | 889.28 | 59.5% |
| `carry-rank30-5` | 118.73 | 371.24 | 160.51 | 176.11 | 109.53 | 936.12 | 56.8% |
| `carry-rank90-10` | 95.14 | 285.87 | 124.01 | 141.04 | 47.82 | 693.88 | 59.1% |
| `carry-rank90-10-quarterly` (18 rebalances) | 61.76 | 189.63 | 82.23 | 91.57 | 74.25 | 499.44 | 54.4% |
| `carry-rank90-5` | 97.75 | 293.28 | 127.08 | 144.89 | 87.43 | 750.43 | 56.0% |

### 7.2 What a cheaper assumption would have produced

**A sensitivity, not a result.** Every variant below is still costed at the
registered assumption everywhere else in this document, and no criterion is
recomputed here. The columns scale the spread and slippage lines by a multiplier and
leave everything else exactly as it ran.

The arithmetic is exact in the charges and approximate in the path. No registered
variant reads a cost when it decides, so a cheaper world would have held the same
pairs in the same weights; it would also have compounded a larger equity into every
later position, so the true figure at a lower assumption is a little better than
this. The multiplier at which each variant breaks even is in the last column.

| variant | as run | half | a quarter | none at all | breaks even at |
|---|---:|---:|---:|---:|---:|
| `carry-basket-10` | -241.72 | -185.58 | -157.51 | -129.44 | -1.153 |
| `carry-basket-5` | -86.12 | -33.71 | -7.50 | 18.70 | 0.178 |
| `carry-positive-10` | -857.00 | -592.32 | -459.98 | -327.64 | -0.619 |
| `carry-premium-10` | -1,377.11 | -1,205.74 | -1,120.06 | -1,034.38 | -3.018 |
| `carry-rank30-10` | -857.00 | -592.32 | -459.98 | -327.64 | -0.619 |
| `carry-rank30-5` | -1,084.70 | -818.82 | -685.89 | -552.95 | -1.040 |
| `carry-rank90-10` | -466.36 | -261.42 | -158.95 | -56.48 | -0.138 |
| `carry-rank90-10-quarterly` (18 rebalances) | -232.38 | -96.45 | -28.49 | 39.48 | 0.145 |
| `carry-rank90-5` | -533.96 | -323.78 | -218.69 | -113.60 | -0.270 |

**A multiplier at or below zero means the run loses with spread and slippage
deleted entirely.** Seven of the nine are in
that position, and for them no spread measurement of any kind could change the
sign: their carry does not cover the exchange fees, the conversion leg and the
delisting haircut on their own.

**Two of the nine do flip: `carry-basket-5`, `carry-rank90-10-quarterly`.** They turn positive only when
spread and slippage fall to about 18% of the assumption, a reduction of
roughly 82%. Even with both lines deleted they earn 18.70 and 39.48 EUR on
1,500 of equity over fifty-six months, which is low single-digit per cent in
total rather than a year. That is a sign change and not an edge: neither comes
near criterion 1, whose bar is the 95th percentile of the variant's own null.

### 7.3 Rule S1, and the circularity it had to break

**Amendment 8's version of rule S1** acquired the spread sample if some variant
earned a positive net return at research fees. No variant did, so it was false.
**But the assumed spread is inside the charges that produced that negative net**,
so the assumption helped prevent the measurement that could have corrected it.

**Amendment 9 replaces the test with the counterfactual** and the bar is criterion
1 itself: set the assumed cost to zero, re-evaluate, and acquire when some variant
would then clear a positive net return *and* a Sharpe above its own null. A sign
change alone is not a rescue.

### 7.4 Rule S1: could the assumed cost be carrying the verdict?

Amendment 9, registered **after** these figures had been read and labelled as such
everywhere it appears. It governs an acquisition and can move no number in this
document: every variant stays costed at the registered assumption in every cell,
under invariant 12.

Spread and slippage are set to zero and the run re-evaluated. The bar is criterion 1
applied to that counterfactual: a positive net return **and** a Sharpe above the 95th
percentile of the variant's own exposure-matched null.

| variant | net as run | net at zero | Sharpe at zero | its null's p95 | clears |
|---|---:|---:|---:|---:|---|
| `carry-basket-10` | -16.11% | -8.77% | -0.404 | -1.575 | no |
| `carry-basket-5` | -5.74% | 1.48% | 0.091 | -1.262 | yes |
| `carry-positive-10` | -57.13% | -32.89% | -0.854 | -1.575 | no |
| `carry-premium-10` | -91.81% | -80.52% | -0.701 | -1.575 | no |
| `carry-rank30-10` | -57.13% | -32.89% | -0.854 | -1.575 | no |
| `carry-rank30-5` | -72.31% | -53.95% | -0.863 | -1.262 | no |
| `carry-rank90-10` | -31.09% | -8.33% | -0.256 | -1.575 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | -15.49% | 1.72% | 0.097 | -1.560 | yes |
| `carry-rank90-5` | -35.60% | -11.93% | -0.358 | -1.262 | no |

**Rule S1 as written: yes.** Two of the nine variants clear criterion 1 once the assumed cost is removed, so by the registered rule the spread sample is to be acquired.

#### The rule fires, and its author expected it not to

**This is reported rather than resolved, because the two readings disagree.**
The rule was registered on the reasoning that a variant landing at a small
positive figure is *rescued by rounding rather than by the assumption*, and
would fail to clear its own null. On this data it does clear it.

**The reason is that the null bar is negative.** The exposure-matched null is
itself losing over this window, at a 95th-percentile Sharpe between
-1.575 and -1.262, so a counterfactual Sharpe near
zero clears it comfortably. Criterion 1 is a
comparison against chance in this market, not an absolute bar, and at zero
assumed cost these two variants beat chance while earning almost nothing.

In absolute terms the two are still tiny: they earn 18.70 and 39.48 EUR on
1,500 of equity across 56 months. Whether that is *the assumption carrying the
verdict* or *rounding* is precisely what the two readings disagree about.

**The verdict letter does not move either way, and that is computed rather
than argued.** Criterion 1 is one of six. Asking criterion 2 of the same
counterfactual:

| variant | Sharpe at zero, per month | expected max under the trial count | DSR | clears 0.95 |
|---|---:|---:|---:|---|
| `carry-basket-5` | 0.0263 | 1.8818 | 0.0000 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | 0.0280 | 1.8619 | 0.0000 | no |

Both columns are per month, which is the unit the deflation works in: an
annualised 0.09 is a monthly 0.026. Both deflated Sharpes are **0.0000**
against a threshold of 0.95, because the expected maximum under the registry's
honest trial count is about seventy times the counterfactual's own Sharpe.
**So even with spread and slippage
deleted entirely, no variant clears all six criteria and F1's verdict stays
(B).** What the acquisition could buy is a measured cost line beside a variant
that beats a losing null while earning 1.5 per cent over four and a half years.

**Until the Product Owner settles which reading governs, the sample is not
acquired.** A 1.8 to 3.2 GB download made on a reading of a rule that its own
author did not expect is the kind of decision this apparatus exists to make
visible rather than convenient.

**The counterfactual is modelled, not measured.** The removed cost is added back in
equal instalments across the scored months, on each month's opening equity along
the realised path. That is exact in the total and in the sign of the net return,
and approximate in the volatility, because the real charge follows each month's
turnover. From F2 the runner records per-month assumed costs and the same test is
computed exactly. The conclusion here does not rest on the approximation: the two
clearing Sharpes sit more than a full point above their null bars.

## 8. Regime stability

Months are counted over the **scored** window. Counting them over the whole usable
window would let the twelve fitting months decide whether a regime is conclusive for
a result they were never part of. A month's regime is the label that was knowable
when its position was opened, not when it closed.

| regime | scored months | conclusive |
|---|---:|---|
| bear | 31 | yes |
| bull | 16 | yes |
| crash | 1 | no |
| not_evaluable | 0 | no |
| recovery | 9 | yes |

| variant | bear | bull | recovery |
|---|---:|---:|---:|
| `carry-basket-10` | -17.93% (30m) | 2.02% (15m) | 0.02% (9m) |
| `carry-basket-5` | -13.97% (30m) | 8.58% (15m) | 1.13% (9m) |
| `carry-positive-10` | -50.65% (30m) | -5.66% (15m) | -8.83% (9m) |
| `carry-premium-10` | -89.61% (30m) | -13.01% (15m) | -9.57% (9m) |
| `carry-rank30-10` | -50.65% (30m) | -5.66% (15m) | -8.83% (9m) |
| `carry-rank30-5` | -68.84% (30m) | -4.16% (15m) | -8.17% (9m) |
| `carry-rank90-10` | -32.98% (30m) | 3.41% (15m) | -2.12% (9m) |
| `carry-rank90-10-quarterly` (18 rebalances) | -20.82% (30m) | 8.12% (15m) | -0.88% (9m) |
| `carry-rank90-5` | -35.39% (30m) | 1.40% (15m) | -3.31% (9m) |

## 9. The most recent 24 scored months

Criterion 6, added by amendment 1. It asks whether the edge is confined to the early
part of the window. The null it is measured against is **the same draws** as the
full-window null, restricted to the same trailing months: not a fresh set of draws,
which would be a different experiment.

| variant | months | net return | Sharpe | 95th pct, same draws | holds |
|---|---:|---:|---:|---:|---|
| `carry-basket-10` | 24 | -1.32% | -0.394 | -1.999 | no |
| `carry-basket-5` | 24 | 2.06% | 0.656 | -1.581 | yes |
| `carry-positive-10` | 24 | -49.09% | -2.598 | -1.999 | no |
| `carry-premium-10` | 24 | -88.66% | -1.624 | -1.999 | no |
| `carry-rank30-10` | 24 | -49.09% | -2.598 | -1.999 | no |
| `carry-rank30-5` | 24 | -64.07% | -1.914 | -1.581 | no |
| `carry-rank90-10` | 24 | -33.95% | -2.644 | -1.999 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | 24 | -19.37% | -1.942 | -1.956 | no |
| `carry-rank90-5` | 24 | -34.60% | -2.624 | -1.581 | no |

### 9.1 The largest contraction, and the headline without it

The rebalance with the biggest month-on-month fall in pair count is **2026-07-01**: 339 pairs before, 108 after, 231 excluded. Amendment 26.1 requires that a
contraction be described by *composition* rather than by size, because a smaller
slice that is representative and a smaller slice that is not are different facts.

| attribute | admitted | excluded | difference | 95% interval | systematic |
|---|---:|---:|---:|---|---|
| contract_age_days (median) | 1,917.00 | 577.00 | 1,340.00 | 1,111.00 to 1,445.50 | yes |
| liquidity_band (mean) | 0.444 | 0.264 | 0.18 | 0.0711 to 0.29 | yes |
| median_funding_rate (median) | 4.44e-05 | 5e-05 | -5.61e-06 | -2.24e-05 to 7.92e-06 | no |

Admitted minus excluded, so a positive difference means the surviving group scores higher on that attribute.

**The 108 pairs surviving 2026-07-01 differ systematically from the 231 excluded on: contract_age_days, liquidity_band. That month's return is therefore computed on an unrepresentative slice, and the direction of the bias is not knowable from the count alone. The headline is reported with and without this rebalance.**

The split is on the venue's funding cadence, not on anything the rule names: 104 of 108 admitted legs settle every 8 hours, against 227 of 231 excluded legs every 4 hours. Only a contract on the faster cadence has a settlement at the instant the venue failed to publish, so the rule removed that population almost entirely. Contract age and liquidity differ as a consequence, because the venue assigns faster funding to newer and more volatile contracts.

*DESCRIBED, NOT TESTED. The funding cadence is not one of the three attributes amendment 26.1 registers, carries no interval, and supports no verdict. Adding a fourth test after seeing the result would be the post-hoc widening this task is arranged to prevent.*

Every variant's headline in the headline cell, with that month and without it.
Both figures are compounded from the same monthly series, so the comparison is
like for like:

| variant | net return | without | difference | Sharpe | without |
|---|---:|---:|---:|---:|---:|
| `carry-basket-10` | -16.11% | -15.36% | -0.75% | -0.794 | -0.761 |
| `carry-basket-5` | -5.74% | -4.82% | -0.93% | -0.248 | -0.206 |
| `carry-positive-10` | -57.13% | -56.28% | -0.86% | -1.805 | -1.777 |
| `carry-premium-10` | -91.81% | -91.42% | -0.39% | -1.221 | -1.204 |
| `carry-rank30-10` | -57.13% | -56.28% | -0.86% | -1.805 | -1.777 |
| `carry-rank30-5` | -72.31% | -71.94% | -0.37% | -1.451 | -1.447 |
| `carry-rank90-10` | -31.09% | -30.39% | -0.70% | -1.183 | -1.161 |
| `carry-rank90-10-quarterly` (quarterly) | -15.49% | -14.90% | -0.59% | -0.680 | -0.657 |
| `carry-rank90-5` | -35.60% | -34.95% | -0.65% | -1.305 | -1.286 |

**What this decides: nothing. Criterion 6 is judged on the full series exactly as registered; these figures sit beside it, and section 6 rule 4 is unchanged.**

The verdict is the one computed on the full series. Dropping a month because
its composition is inconvenient is the move this apparatus exists to prevent.
The column is here so a reader can see how much of the headline that month
carried, which on these figures is under one percentage point everywhere.

## 10. Sign stability across the cost regimes

Criterion 5. A variant whose net return changes sign between two cost assumptions is
a variant whose result is the assumption rather than the market.

| variant | `vip0_maker` | `vip0_even` | `vip0_taker` | `stress` |
|---|---:|---:|---:|---:|
| `carry-basket-10` | -15.77% | -16.11% | -16.46% | -23.13% |
| `carry-basket-5` | -5.39% | -5.74% | -6.09% | -12.78% |
| `carry-positive-10` | -56.66% | -57.13% | -57.61% | -73.46% |
| `carry-premium-10` | -91.69% | -91.81% | -91.92% | -95.15% |
| `carry-rank30-10` | -56.66% | -57.13% | -57.61% | -73.46% |
| `carry-rank30-5` | -71.98% | -72.31% | -72.64% | -83.88% |
| `carry-rank90-10` | -30.58% | -31.09% | -31.60% | -49.13% |
| `carry-rank90-10-quarterly` | -15.09% | -15.49% | -15.89% | -30.42% |
| `carry-rank90-5` | -35.08% | -35.60% | -36.11% | -53.82% |

## 11. Deflation, and what the search cost

The Deflated Sharpe Ratio asks what the best of this many searches would have
produced by chance alone. The trial count is the honest one from the append-only
hash-chained registry, nulls included, because a null draw is a search whether or
not anybody hoped it would win.

- trials including null constructs: **455**
- trials excluding null constructs: **152**

| variant | Sharpe | expected max under the null | PSR | DSR | clears 0.95 |
|---|---:|---:|---:|---:|---|
| `carry-basket-10` | -0.794 | 1.8163 | 0.0351 | 0.0000 | no |
| `carry-basket-5` | -0.248 | 1.8818 | 0.2797 | 0.0000 | no |
| `carry-positive-10` | -1.805 | 1.8163 | 0.0000 | 0.0000 | no |
| `carry-premium-10` | -1.221 | 1.8163 | 0.0000 | 0.0000 | no |
| `carry-rank30-10` | -1.805 | 1.8163 | 0.0000 | 0.0000 | no |
| `carry-rank30-5` | -1.451 | 1.8818 | 0.0000 | 0.0000 | no |
| `carry-rank90-10` | -1.183 | 1.8163 | 0.0012 | 0.0000 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | -0.680 | 1.8619 | 0.0662 | 0.0000 | no |
| `carry-rank90-5` | -1.305 | 1.8818 | 0.0011 | 0.0000 | no |

The expected maximum is in per-observation units and is the benchmark the observed
Sharpe is deflated against. Its inputs - the trial count, the measured variance of
the null's Sharpe distribution, the observation count, the skewness and the
kurtosis - are all in the result file beside the output, because a report showing
only the output invites exactly the argument this project exists to avoid.

## 12. Break-even turnover at the execution venue

Amendments 4 and 5. The research grid runs at one venue's published schedule; the
only venue this account can trade is another, whose spot leg is four to eight times
more expensive. This is a **single clearly-labelled sensitivity**, read by no
criterion, consuming no variant budget. **No result at the research venue is evidence
about execution at the other one.**

The re-cost was run on **`carry-basket-5`**, chosen by the rule fixed before the grid
ran: highest out-of-sample net Sharpe in the headline cell.

| | round trips per year |
|---|---:|
| break-even at research fees (22.50 bps of equity) | 8.33 |
| break-even at execution fees (105.83 bps of equity) | 1.77 |
| realised turnover | 3.00 |
| gross carry, annualised | 187.3938832281445961398841700 bps |

The best variant's gross carry pays for 8.33 full-book round trips a year at research fees and 1.77 at execution fees, against 3.00 realised, so it clears the research schedule and not the execution one. Both figures are upper bounds: spread and slippage also scale with turnover and are excluded.


### Realised turnover, every variant

Round trips a year, from the fee line the engine charged, by the registered
definition: fees as basis points of equity a year, divided by the
22.50 basis points one full-book round trip costs at
research fees.

Amendment 5 registered its break-evens against an illustrative 400
basis points of gross carry a year: **17.8** round trips at research fees and
**3.8** at execution fees. The realised carry is not that figure, so both
comparisons are shown.

| variant | round trips/yr | vs 17.8 | vs 3.8 |
|---|---:|---|---|
| `carry-basket-10` | 3.20 | inside | inside |
| `carry-basket-5` | 3.00 | inside | inside |
| `carry-positive-10` | 7.70 | inside | **outside** |
| `carry-premium-10` | 6.15 | inside | **outside** |
| `carry-rank30-10` | 7.70 | inside | **outside** |
| `carry-rank30-5` | 7.54 | inside | **outside** |
| `carry-rank90-10` | 6.04 | inside | **outside** |
| `carry-rank90-10-quarterly` (18 rebalances) | 3.92 | inside | **outside** |
| `carry-rank90-5` | 6.21 | inside | **outside** |

Every variant sits inside the illustrative research break-even and most sit
outside the execution one. That comparison is against an assumed 4% carry rather
than against what these variants earned, and the realised break-evens above,
computed from the realised gross carry, are the binding pair.

**Both break-evens are upper bounds.** Spread and slippage also scale with
turnover, are identical at both venues, and are excluded from the fee arithmetic,
so the break-even on total cost is strictly lower than either figure. The
conversion leg is excluded too, but **not for the reason amendment 5 gives**:
see the correction below.

### 12.1 A correction to amendment 5's justification

Section 27.2 excludes the conversion leg from the fee arithmetic and says it is
*charged twice for a whole run rather than per rebalance, so folding a fixed cost
into a per-round-trip figure would misattribute it to turnover*. **That premise is
wrong.** The engine charges the conversion twice per *position*, on the way in and
on the way out, so it scales with traded notional exactly as a fee does. In this
run the conversion line is 10.0000 basis points of turnover for every one of the
nine variants, to four decimal places, which is the registered rate and not a
coincidence.

**What this changes, and what it does not.** The registered *definition* of the
break-even is unaffected: it was defined on the fee line alone and that is what was
computed, so no figure in this document moves. What changes is the reading. The
excluded conversion is not a fixed overhead sitting outside the turnover question;
it is a turnover-scaling charge about half again the size of the exchange fees
themselves, and its exclusion makes the break-evens a **looser** upper bound than
section 27.2 claims. A reader recomputing at another fee schedule should add it to
the fee line rather than treat it as a constant.

This is a defect in a justification, not in a computation, and it is reported
rather than repaired in place: amendment 5 was registered before the run and its
text is not edited afterwards. It is not a voiding reason under section 29.2 -
every registered parameter was read by the code path that ran, and the conversion
rate the ledger charged is the registered one.

### Declared expectation D2

Registered before the runner produced anything: *the binding constraint on this
family is holding period, not signal quality.*

- **D2a** (a clearing variant is at or below the median realised turnover of the
  nine): **unresolved** - no variant cleared all six, so it cannot be evaluated
- **D2b** (the best variant's execution break-even is fewer than 12 round trips a
  year): **confirmed**

D2 carries no weight in the verdict. It was stated so that a story told after the
fact about fees and holding periods can be checked against a prediction made
before it.

## 13. The six criteria

All six must hold, in the headline cell, for a variant to count as working. A
criterion that could not be evaluated reads `n/a`, which is not a pass.

| variant | 1 null+sign | 2 deflation | 3 selection | 4 regimes | 5 sign | 6 recent | all six | N_eff |
|---|---|---|---|---|---|---|---|---:|
| `carry-basket-10` | no | no | no | no | yes | no | **no** | 36.2 |
| `carry-basket-5` | no | no | no | no | yes | yes | **no** | 43.1 |
| `carry-positive-10` | no | no | no | no | yes | no | **no** | 20.8 |
| `carry-premium-10` | no | no | no | no | yes | no | **no** | 45.0 |
| `carry-rank30-10` | no | no | no | no | yes | no | **no** | 20.8 |
| `carry-rank30-5` | no | no | no | no | yes | no | **no** | 19.8 |
| `carry-rank90-10` | no | no | no | no | yes | no | **no** | 25.0 |
| `carry-rank90-10-quarterly` (18 rebalances) | no | no | no | no | yes | no | **no** | 40.5 |
| `carry-rank90-5` | no | no | no | no | yes | no | **no** | 32.4 |

**Criterion 1 carries a sign condition** that SEXTANT-005's did not. That task
recorded its criterion 1 misfiring: in a cross-section where almost everything
fell, a variant could clear *beats the 95th percentile of its own null* by losing
less than chance lost, and two did, one while destroying most of the account. The
correction requires a positive net return as well, and it makes the criterion
stricter rather than looser.

`N_eff` is the effective observation count after the series' own autocorrelation.

## 14. What the two sample rules decided

Neither order-book sample is a trial and neither can change a variant's result.
Both are acquisitions, and both were made conditional on this file **before it
existed**: rule C3 for depth, in section 17, and rule S1 for spread, in section 30.

### Rule C3, capacity (depth)

Shown in the headline cell, `vip0_even`. The requirement below is computed across
all four registered cells, so a variant that earned inside the window in any of them
would still call the sample for.

| variant | scored months inside the depth window | mean net inside | mean net outside | outcome |
|---|---:|---:|---:|---|
| `carry-basket-5` | 16 | -0.11% | -0.09% | unestablished: the variant did not earn inside the window |
| `carry-basket-10` | 16 | -0.60% | -0.20% | unestablished: the variant did not earn inside the window |
| `carry-rank30-5` | 16 | -0.21% | -2.99% | unestablished: the variant did not earn inside the window |
| `carry-rank30-10` | 16 | -0.19% | -2.06% | unestablished: the variant did not earn inside the window |
| `carry-rank90-5` | 16 | 0.35% | -1.29% | measured, with its window |
| `carry-rank90-10` | 16 | 0.41% | -1.14% | measured, with its window |
| `carry-premium-10` | 16 | -0.87% | -4.73% | unestablished: the variant did not earn inside the window |
| `carry-positive-10` | 16 | -0.19% | -2.06% | unestablished: the variant did not earn inside the window |
| `carry-rank90-10-quarterly` | 16 | 0.43% | -0.59% | measured, with its window |

**Depth sample required: yes.** At least one variant lands on a measured outcome, so section 12's twenty-symbol, seventeen-day bookDepth sample is acquired.

UNESTABLISHED does not mean this family has no capacity. It means this dataset
cannot say what it is, which is the same class of statement as invariant 9's
*not evaluable*, and it is not softened.

### Rule S1, the spread sample

| | |
|---|---:|
| runs considered, at research fees | 36 |
| of those, with a positive net return | 0 |
| best run | `carry-basket-5` in `vip0_maker` |
| its net return | -5.39% |

**Spread sample acquired: no.** No registered variant earns a positive net return at research fees. Spread is an assumed cost and measuring it can only make a variant look worse, so the measurement would refine a cost line on a book that does not earn. The 1.8 to 3.2 GB is not downloaded.

Either way the configured spread remains labelled an assumption under invariant
12. An unmeasured spread is never reported as a measured one, and a decision not
to measure is not a claim that the assumption was right.

## 15. Capacity, measured

Rule C3 asked for this and section 12 fixed its shape before anything ran.
**337 symbol-days** were acquired of 340 requested, 139.2 MB, and every one of them was verified
against the publisher's own SHA-256. The `bookTicker` spread sample of the same
section was **not** acquired, because rule S1 was false.

- window: **2023-01-01/2024-05-17** (rule C1: no capacity figure omits it)
- symbols: the **20** deepest carry-universe perpetuals by trailing 30-day median quote turnover at 2022-12-31
- days: the first of each month, 17 of them
- figures: resting notional on **both sides**, cumulative to the stated distance
  from mid, median across the day's minutes and then across the days

| symbol | days measured | days with an opening window | median within 1% | within 5% |
|---|---:|---:|---:|---:|
| `ADAUSDT` | 17 | 10 | 4,463,972.06 | 13,405,430.57 |
| `APEUSDT` | 17 | 10 | 1,902,861.32 | 5,710,914.99 |
| `AXSUSDT` | 17 | 10 | 1,288,078.22 | 5,002,568.64 |
| `BNBUSDT` | 17 | 10 | 10,397,988.25 | 28,237,407.11 |
| `BTCUSDT` | 17 | 10 | 202,645,925.51 | 790,056,978.96 |
| `CHZUSDT` | 17 | 10 | 1,085,632.01 | 3,336,980.07 |
| `DOGEUSDT` | 17 | 10 | 6,934,641.55 | 22,351,413.88 |
| `DYDXUSDT` | 17 | 10 | 2,356,982.39 | 7,106,881.39 |
| `EOSUSDT` | 17 | 10 | 2,465,849.41 | 8,916,748.71 |
| `ETCUSDT` | 17 | 10 | 3,064,270.68 | 10,898,760.02 |
| `ETHUSDT` | 17 | 10 | 99,372,684.66 | 360,271,026.09 |
| `FTMUSDT` | 16 | 9 | 2,371,881.70 | 5,995,245.82 |
| `LINKUSDT` | 17 | 10 | 4,715,214.73 | 14,133,804.41 |
| `LTCUSDT` | 17 | 10 | 6,037,944.05 | 19,132,775.04 |
| `MASKUSDT` | 17 | 10 | 1,685,619.08 | 3,849,459.73 |
| `MATICUSDT` | 16 | 9 | 5,769,136.73 | 13,356,015.81 |
| `OCEANUSDT` | 17 | 10 | 826,386.28 | 2,035,450.66 |
| `SOLUSDT` | 17 | 10 | 6,905,897.69 | 20,444,856.49 |
| `WAVESUSDT` | 17 | 10 | 771,613.25 | 2,102,039.71 |
| `XRPUSDT` | 16 | 9 | 10,235,598.13 | 27,924,631.63 |

Across every measured day: **3,727,759.52** within 1 per cent and **11,082,780.04** within 5, in USDT.

### What it means at this account, and what it does not

One leg of one pair is **125.00 EUR** at the registered equity, position count
and margin fraction. Against the thinnest of the twenty that is a fraction of a
basis point of what rests within one per cent of mid:

| thinnest of the twenty | `WAVESUSDT` |
|---|---:|
| its median resting notional within 1% | 771,613.25 |
| one leg, as a share of it | 0.0162% |

No FX conversion is applied to that comparison. The depth is in USDT and the
leg in EUR, and no plausible rate moves a figure of this size by an order of
magnitude. The units are stated rather than blended.

**Depth is not what stops this family.** One leg is under two hundredths of a
per cent of what rests within one per cent of mid on the thinnest symbol sampled,
so no capacity constraint could have produced the returns in section 5. What does
stop it is in sections 7 and 12: the funding stream is real and is roughly
cancelled by the basis, and the costs then exceed what is left.

**Three things this sample cannot say.** These are the twenty *deepest* members,
so the median across them is an upper bound on what a median universe member
offers, and the universe ran to 340 pairs. It measures the **perpetual leg only**,
and a cash-and-carry needs both legs to fill. And it is seventeen days inside a
seventeen-month stretch of a window running from 2021 to 2026: rule C2 makes any
figure outside that window an extrapolation, and none is offered here.

**Three days were never published.** `FTMUSDT` on 2023-11-01; `MATICUSDT` on 2023-11-01; `XRPUSDT` on 2023-11-01. A day the venue did not publish is absent from the median rather than counted as a zero, and the day count beside each figure says how many it was taken over.

**The opening window is mostly absent.** The venue's publication frequently starts
hours into the day, so the 00:00-00:05 UTC figures the statistic asks for exist on
about ten of the seventeen days. Where they are absent the figure is null, never
zero: no snapshot is not an empty book.

## 16. Verdict for family F1

### (B)

No variant's out-of-sample net Sharpe exceeded the 95th percentile of its own exposure-matched null while also earning a positive net return, in the headline cell. Criterion 1 is the floor and nothing reached it.

- variants clearing criterion 1: none
- variants clearing all six: none

### What this verdict rests on

**The family-level result is the headline, not the best variant's.** Across the
nine variants the book received 2,113.62 in
funding and the price legs gave back 99.1%
of it, leaving **18.85** on 1,500 of equity before a single charge. That is
the finding:
**the premium is compensation for the basis risk that earns it, priced close to
exactly.** The best single variant cleared
267.06, and reporting that
figure in front would give the opposite impression from the same run.

**The premium is real.** Six of the nine variants cleared a positive carry before any charge: for those,
the funding received exceeded what the price legs gave back. This is the first
positive gross result anywhere in this project, and it is what theory predicts for a
hedged carry. F1 does not say the effect is absent.

**It dies in the toll, and 56.5% of that toll
is assumed rather than measured.** Spread and slippage are configured values under
invariant 12; published exchange fees are only 14.1% of what was charged. So this verdict is
**not** the statement that a retail fee schedule consumed the premium, and it is
not purely a statement about the market either. It rests substantially on two
numbers this project chose, and section 7.2 states what it would have produced had
they been chosen lower.

**What survives that caveat.** Seven of the nine variants lose with spread and slippage deleted entirely, so
for those the assumption changes nothing at all. Two do turn positive, and by rule
S1 as amendment 9 writes it they clear criterion 1 in that counterfactual. **They
still fail criterion 2 with a deflated Sharpe of 0.0000 against a bar of 0.95**, so
no variant clears all six even with the assumed cost deleted. **(B) is therefore
robust to the assumption it rests on**, which is the claim that had to be checked
before the letter could be trusted. Section 7.4 carries the arithmetic.

**The currency leg is reported separately wherever it appears, and always will be.**
At 20.9% of the toll against
14.1% in exchange fees, a euro-funded account
trading instruments quoted elsewhere pays a currency toll larger than the venue's
own. It scales with turnover rather than sitting fixed per run, and it will land the
same way on every family quoted away from the account's currency. Section 31.5.

This is one family's verdict, not the task's. The task's verdict lives in
`docs/VERDICT-006.md` and is written once every family has been run or reported
as not reached.

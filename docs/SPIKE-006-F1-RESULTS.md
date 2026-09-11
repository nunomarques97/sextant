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

> **This run read `v1.7.1`; the specification is now `v1.9`.** Amendments 7 and 8 were registered while the grid
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

| variant | combined | timing | selection | funding | gross | costs |
|---|---:|---:|---:|---:|---:|---:|
| `carry-basket-10` | -16.11% | -20.77% | -16.11% | -25.81 | -4.25 | 263.27 |
| `carry-basket-5` | -5.74% | -20.77% | -5.74% | 108.70 | 135.76 | 113.18 |
| `carry-positive-10` | -57.13% | -20.77% | -57.13% | 304.79 | 32.27 | 584.49 |
| `carry-premium-10` | -91.81% | -20.77% | -91.81% | 13.75 | -739.67 | 623.69 |
| `carry-rank30-10` | -57.13% | -20.77% | -57.13% | 304.79 | 32.27 | 584.49 |
| `carry-rank30-5` | -72.31% | -20.77% | -72.31% | 322.00 | -148.58 | 614.12 |
| `carry-rank90-10` | -31.09% | -20.77% | -31.09% | 387.33 | 227.52 | 306.56 |
| `carry-rank90-10-quarterly` (18 rebalances) | -15.49% | -20.78% | -16.26% | 370.68 | 267.06 | 128.76 |
| `carry-rank90-5` | -35.60% | -20.77% | -35.60% | 327.39 | 216.47 | 423.04 |

Funding, gross and costs are in account currency on the registered starting
equity. A funding figure larger than the combined return means the settlement
stream earned more than the book kept, and the difference is basis and costs.

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

**Both break-evens are upper bounds.** Spread and slippage also scale with
turnover, are identical at both venues, and are excluded from the fee arithmetic,
so the break-even on total cost is strictly lower than either figure. The
conversion leg is excluded too: it is charged twice for a whole run rather than
per rebalance, and folding a fixed cost into a per-round-trip figure would
misattribute it to turnover.

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

This is one family's verdict, not the task's. The task's verdict lives in
`docs/VERDICT-006.md` and is written once every family has been run or reported
as not reached.

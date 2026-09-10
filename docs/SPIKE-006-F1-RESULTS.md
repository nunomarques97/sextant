# SEXTANT-006 family F1: cash-and-carry, spot against the perpetual

Every number here was produced by one run of the pre-registered grid and is
reproducible from `research/spike-006-f1.json`, which carries the monthly return
series each figure was computed from. The specification is
`docs/PRE-REGISTRATION-006-F1.md` and `config/spike-006-f1.yaml`, both committed
before the numbers existed; the commit that carries them is cited below and its
ancestry is checkable with `git merge-base --is-ancestor`.

- pre-registration: **v1.7.1**
- engine version: `sextant-006-f1`
- run made from commit: `a819c019d8b3`
- everything denominated in **EUR**
- **verdict: (B)**

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
| repository head at run time | `a819c019d8b3efee156e10780dfbc464c311cf2e` |

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
| null and benchmark constructs, not charged | 65 |

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
| `carry-basket-10` | -14.82% | -1.655 | 0.489 | -2.836 | 56 | 56 |
| `carry-basket-5` | -12.92% | -1.768 | 0.492 | -2.116 | 56 | 56 |
| `carry-positive-10` | -64.42% | -2.950 | 0.540 | -2.836 | 56 | 56 |
| `carry-premium-10` | -87.67% | -1.209 | 0.477 | -2.836 | 56 | 56 |
| `carry-rank30-10` | -64.42% | -2.950 | 0.540 | -2.836 | 56 | 56 |
| `carry-rank30-5` | -75.91% | -2.169 | 0.506 | -2.116 | 56 | 56 |
| `carry-rank90-10` | -46.60% | -2.766 | 0.532 | -2.836 | 56 | 56 |
| `carry-rank90-10-quarterly` (18 rebalances) | -34.37% | -2.146 | 0.505 | -2.737 | 56 | 18 |
| `carry-rank90-5` | -48.54% | -3.134 | 0.550 | -2.116 | 56 | 56 |

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
| `carry-rank90-10` | -46.60% | -2.766 | 56 | 0 |
| `carry-rank90-10-quarterly` | -34.37% | -2.146 | 18 | 2 |

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

## 7. Where the return came from

Four ways of cutting the same book. **Combined** is the variant. **Timing** holds the
whole carry universe at the variant's own invested fraction: its timing, none of its
selection. **Selection** holds the variant's own pairs scaled to full investment: its
selection, none of its timing. **Funding** is the realised settlement stream, which is
the return rather than a cost line and is never blended into fees.

| variant | combined | timing | selection | funding | gross | costs |
|---|---:|---:|---:|---:|---:|---:|
| `carry-basket-10` | -14.82% | -15.88% | -14.82% | 0.00 | 21.57 | 243.92 |
| `carry-basket-5` | -12.92% | -15.88% | -12.92% | 0.00 | 26.75 | 220.51 |
| `carry-positive-10` | -64.42% | -15.88% | -64.42% | 0.00 | -219.86 | 746.39 |
| `carry-premium-10` | -87.67% | -15.88% | -87.67% | 0.00 | -684.76 | 630.27 |
| `carry-rank30-10` | -64.42% | -15.88% | -64.42% | 0.00 | -219.86 | 746.39 |
| `carry-rank30-5` | -75.91% | -15.88% | -75.91% | 0.00 | -382.00 | 756.67 |
| `carry-rank90-10` | -46.60% | -15.88% | -46.60% | 0.00 | -113.25 | 585.68 |
| `carry-rank90-10-quarterly` (18 rebalances) | -34.37% | -15.11% | -34.92% | 0.00 | -77.09 | 438.52 |
| `carry-rank90-5` | -48.54% | -15.88% | -48.54% | 0.00 | -84.02 | 644.16 |

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
| `carry-basket-10` | -7.96% (30m) | -4.47% (15m) | -2.26% (9m) |
| `carry-basket-5` | -6.31% (30m) | -3.98% (15m) | -2.30% (9m) |
| `carry-positive-10` | -49.72% (30m) | -18.76% (15m) | -11.66% (9m) |
| `carry-premium-10` | -82.88% (30m) | -19.28% (15m) | -9.25% (9m) |
| `carry-rank30-10` | -49.72% (30m) | -18.76% (15m) | -11.66% (9m) |
| `carry-rank30-5` | -64.41% (30m) | -21.49% (15m) | -12.60% (9m) |
| `carry-rank90-10` | -34.37% (30m) | -12.34% (15m) | -6.10% (9m) |
| `carry-rank90-10-quarterly` (18 rebalances) | -25.12% (30m) | -6.65% (15m) | -5.31% (9m) |
| `carry-rank90-5` | -34.47% (30m) | -13.54% (15m) | -8.13% (9m) |

## 9. The most recent 24 scored months

Criterion 6, added by amendment 1. It asks whether the edge is confined to the early
part of the window. The null it is measured against is **the same draws** as the
full-window null, restricted to the same trailing months: not a fresh set of draws,
which would be a different experiment.

| variant | months | net return | Sharpe | 95th pct, same draws | holds |
|---|---:|---:|---:|---:|---|
| `carry-basket-10` | 24 | -5.97% | -6.583 | -2.785 | no |
| `carry-basket-5` | 24 | -4.70% | -3.786 | -2.145 | no |
| `carry-positive-10` | 24 | -47.83% | -3.165 | -2.785 | no |
| `carry-premium-10` | 24 | -81.07% | -1.406 | -2.785 | no |
| `carry-rank30-10` | 24 | -47.83% | -3.165 | -2.785 | no |
| `carry-rank30-5` | 24 | -61.60% | -2.403 | -2.145 | no |
| `carry-rank90-10` | 24 | -35.10% | -3.330 | -2.785 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | 24 | -26.10% | -2.857 | -2.729 | no |
| `carry-rank90-5` | 24 | -34.04% | -3.562 | -2.145 | no |

## 10. Sign stability across the cost regimes

Criterion 5. A variant whose net return changes sign between two cost assumptions is
a variant whose result is the assumption rather than the market.

| variant | `vip0_maker` | `vip0_even` | `vip0_taker` | `stress` |
|---|---:|---:|---:|---:|
| `carry-basket-10` | -14.47% | -14.82% | -15.18% | -21.95% |
| `carry-basket-5` | -12.59% | -12.92% | -13.24% | -19.45% |
| `carry-positive-10` | -64.02% | -64.42% | -64.81% | -77.98% |
| `carry-premium-10` | -87.50% | -87.67% | -87.83% | -92.61% |
| `carry-rank30-10` | -64.02% | -64.42% | -64.81% | -77.98% |
| `carry-rank30-5` | -75.62% | -75.91% | -76.20% | -85.94% |
| `carry-rank90-10` | -46.20% | -46.60% | -46.99% | -60.64% |
| `carry-rank90-10-quarterly` | -34.06% | -34.37% | -34.68% | -46.05% |
| `carry-rank90-5` | -48.13% | -48.54% | -48.96% | -63.16% |

## 11. Deflation, and what the search cost

The Deflated Sharpe Ratio asks what the best of this many searches would have
produced by chance alone. The trial count is the honest one from the append-only
hash-chained registry, nulls included, because a null draw is a search whether or
not anybody hoped it would win.

- trials including null constructs: **354**
- trials excluding null constructs: **116**

| variant | Sharpe | expected max under the null | PSR | DSR | clears 0.95 |
|---|---:|---:|---:|---:|---|
| `carry-basket-10` | -1.655 | 5.3438 | 0.0350 | 0.0000 | no |
| `carry-basket-5` | -1.768 | 5.9599 | 0.0432 | 0.0000 | no |
| `carry-positive-10` | -2.950 | 5.3438 | 0.0000 | 0.0000 | no |
| `carry-premium-10` | -1.209 | 5.3438 | 0.0000 | 0.0000 | no |
| `carry-rank30-10` | -2.950 | 5.3438 | 0.0000 | 0.0000 | no |
| `carry-rank30-5` | -2.169 | 5.9599 | 0.0000 | 0.0000 | no |
| `carry-rank90-10` | -2.766 | 5.3438 | 0.0000 | 0.0000 | no |
| `carry-rank90-10-quarterly` (18 rebalances) | -2.146 | 4.6295 | 0.0000 | 0.0000 | no |
| `carry-rank90-5` | -3.134 | 5.9599 | 0.0000 | 0.0000 | no |

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

The re-cost was run on **`carry-premium-10`**, chosen by the rule fixed before the grid
ran: highest out-of-sample net Sharpe in the headline cell.

| | round trips per year |
|---|---:|
| break-even at research fees | could not be formed |
| break-even at execution fees | could not be formed |
| realised turnover | could not be formed |

The gross return or the fee line the arithmetic needs was not available, so no
break-even is reported. It is not reported as zero: a tight threshold and no
threshold are different facts about a strategy.

## 13. The six criteria

All six must hold, in the headline cell, for a variant to count as working. A
criterion that could not be evaluated reads `n/a`, which is not a pass.

| variant | 1 null+sign | 2 deflation | 3 selection | 4 regimes | 5 sign | 6 recent | all six | N_eff |
|---|---|---|---|---|---|---|---|---:|
| `carry-basket-10` | no | no | no | no | yes | no | **no** | 56.0 |
| `carry-basket-5` | no | no | no | no | yes | no | **no** | 56.0 |
| `carry-positive-10` | no | no | no | no | yes | no | **no** | 20.7 |
| `carry-premium-10` | no | no | no | no | yes | no | **no** | 47.8 |
| `carry-rank30-10` | no | no | no | no | yes | no | **no** | 20.7 |
| `carry-rank30-5` | no | no | no | no | yes | no | **no** | 20.4 |
| `carry-rank90-10` | no | no | no | no | yes | no | **no** | 41.9 |
| `carry-rank90-10-quarterly` (18 rebalances) | no | no | no | no | yes | no | **no** | 53.0 |
| `carry-rank90-5` | no | no | no | no | yes | no | **no** | 48.6 |

**Criterion 1 carries a sign condition** that SEXTANT-005's did not. That task
recorded its criterion 1 misfiring: in a cross-section where almost everything
fell, a variant could clear *beats the 95th percentile of its own null* by losing
less than chance lost, and two did, one while destroying most of the account. The
correction requires a positive net return as well, and it makes the criterion
stricter rather than looser.

`N_eff` is the effective observation count after the series' own autocorrelation.

## 14. Verdict for family F1

### (B)

No variant's out-of-sample net Sharpe exceeded the 95th percentile of its own exposure-matched null while also earning a positive net return, in the headline cell. Criterion 1 is the floor and nothing reached it.

- variants clearing criterion 1: none
- variants clearing all six: none

This is one family's verdict, not the task's. The task's verdict lives in
`docs/VERDICT-006.md` and is written once every family has been run or reported
as not reached.

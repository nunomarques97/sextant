# Live gate criteria

These are the contract. They are not to be edited by the Developer.

LIVE mode may not be enabled until all of the following are demonstrated and
reviewed:

1. Walk-forward out-of-sample evaluation across at least 3 non-overlapping
   windows.
2. Net-of-all-costs return positive in at least 2 of 3 out-of-sample windows.
3. Out-of-sample max drawdown within the configured limit.
4. Deflated Sharpe Ratio, computed with an honest count of every strategy and
   parameter set tried, still positive.
5. At least 60 days of paper trading with tracking error against the backtest
   below a configured threshold.
6. Explicit written Sponsor sign-off.

Backtest return alone is never sufficient. A strategy showing an extraordinary
backtest return is treated as a suspected defect until proven otherwise.

---

## How this document relates to the code

The criteria above are the decision. The mechanical gates in the code are
separate and weaker: they exist so that live trading cannot start by accident,
not so that it can start correctly.

Starting in LIVE requires all three of:

- `mode: live` in the resolved configuration;
- `SEXTANT_ALLOW_LIVE=1` in the process environment (it cannot be set from any
  YAML file);
- a passing preflight, which includes complete credentials and an API key proven
  not to carry withdrawal permission.

Satisfying those three is necessary and not remotely sufficient. Nothing in the
code can check criteria 1 to 6, and no future automation should be trusted to
sign off criterion 6.

Current state: LIVE cannot start at all. The withdrawal-permission probe is not
wired, preflight treats "cannot be determined" as unsafe, and there is no engine
to run.

---

## 7. The random-baseline rejection filter

*Added under SEXTANT-004, on the Product Owner's instruction, in the terms the
brief specified. Criteria 1 to 6 above are untouched.*

Before a strategy is analysed at all, it is measured against what pure chance
produced over the same window, under the same costs, through the same engine.
The distribution is in [`docs/NULL-BASELINE.md`](NULL-BASELINE.md) and was
computed from **10,000 seeds** of random selection, **8 instruments** drawn
uniformly and **without replacement** from the point-in-time executable universe
at every monthly rebalance. Sampling is without replacement because eight
positions means eight distinct names; a null that could concentrate several
slots in one instrument would not mirror the constraint the strategy operates
under.

**The filter.**

- A strategy scoring **below** the 95th percentile of the random distribution
  for its quote policy and its fill-mix assumption is **rejected for
  insufficient evidence**. That rejection is final and needs no further
  analysis.
- A strategy scoring **above** it has passed this filter **and nothing more**.
  It has **not** demonstrated edge.

**Why passing is necessary and nowhere near sufficient.** The threshold is
multiple-comparison naive. Test twenty strategies against it and roughly one
clears it by chance alone. That is precisely what the Deflated Sharpe Ratio
exists to correct for, which is why passing here buys entry to the real analysis
rather than a verdict. Everything in criteria 1 to 6 still applies in full:
walk-forward out-of-sample performance across three non-overlapping windows, the
Deflated Sharpe Ratio computed with an honest count of every strategy and
parameter set ever tried, the drawdown limit, sixty days of paper trading, and
written Sponsor sign-off.

**The threshold, and how uncertain it is.**

Annualised net Sharpe at the 95th percentile of 10,000 random selections, under
the walk-forward plan, which is the plan any strategy is evaluated under. All
figures are EUR-denominated.

| Quote policy | Fill mix | 95th percentile | 95% CI on the percentile |
| --- | --- | ---: | --- |
| EUR only | 100% maker (assumed) | -0.146 | [-0.152, -0.142] |
| EUR only | 50/50 maker/taker (assumed) | -0.180 | [-0.185, -0.175] |
| EUR only | 100% taker | -0.213 | [-0.219, -0.207] |
| EUR+USD, currency leg ignored | 100% maker (assumed) | -0.375 | [-0.382, -0.363] |
| EUR+USD, currency leg ignored | 50/50 maker/taker (assumed) | -0.427 | [-0.437, -0.417] |
| EUR+USD, currency leg ignored | 100% taker | -0.481 | [-0.490, -0.470] |
| EUR+USD, currency leg applied | 100% maker (assumed) | -0.422 | [-0.431, -0.413] |
| EUR+USD, currency leg applied | 50/50 maker/taker (assumed) | -0.474 | [-0.484, -0.466] |
| EUR+USD, currency leg applied | 100% taker | -0.524 | [-0.535, -0.518] |

The interval is a two-sided 95% bootstrap confidence interval on the percentile
itself, from 10,000 resamples drawn with an explicitly constructed PCG64
generator seeded at 987654321. It describes sampling error, meaning how far the
threshold would move if the experiment were re-run with a different seed set. It
says nothing about whether the cost model is right.

**Every one of these thresholds is negative, and that is a weakness of this
window, not a property of the filter.** Over 2024-04 to 2026-04 the executable
cross-section fell; random selection lost money, so chance produces a negative
Sharpe and a strategy merely breaking even clears the filter. In this window the
filter rejects only strategies that lose *more* than chance did. It is a floor
worth having and it is a low one. The second, harder floor is stated in
criterion 2 and is unchanged: net-of-all-costs return positive in at least two
of three out-of-sample windows.

**The window is too short for this filter to separate skill from noise, and the
size of that problem is measurable.** Twenty-four monthly out-of-sample
observations give an annualised Sharpe standard error of approximately **0.71**.
The entire spread of thresholds in the table above is about 0.38 wide. A single
strategy's Sharpe cannot be distinguished from a point half a standard error
away, so clearing the threshold by anything less than roughly 1.4 in annualised
Sharpe is inside the noise. Nothing in this window can establish edge. The filter
is a rejection tool and that is the whole of its use.

**Which threshold applies.** The one for the quote policy the strategy actually
traded and the fill mix its result is quoted at. A strategy quoted under the
all-maker assumption is compared against the all-maker threshold. A strategy
whose verdict changes across the three fill mixes has not cleared the filter; it
has demonstrated that its result depends on an execution property nobody has
measured.

**When the threshold is restated.** The distribution is a property of the window,
the universe and the cost assumptions. If any of the three changes, whether a new
archive quarter, a corrected cost assumption or a different quote policy, the
null is recomputed from scratch under a new benchmark-configuration version and
both results are retained. It is never adjusted after a strategy has been
measured against it.

---

## 8. What the paper phase is obliged to measure

Criterion 5 requires sixty days of paper trading. Two of those days' worth of
data have a job beyond tracking error, and this section names them so that the
paper phase cannot end without producing them.

**The maker/taker fill ratio. This is the obligation.** Every backtest result
this project has produced assumes post-only limit orders and therefore maker
fees. That is a **hypothesis about execution, not a property of it**: a post-only
order that never fills earns no fee and no position either, and the ratio of
maker to taker fills is the single unmeasured input with the largest effect on
net return. The paper phase must record, for every fill, whether it was a maker
or a taker fill, and how many post-only orders were placed and never filled. The
measured ratio then replaces the assumption, and every headline result is
recomputed at it.

Until that measurement exists, no result may be quoted at the all-maker
assumption alone. Results are quoted at all three mixes, or not quoted.

**The realised spread and slippage.** Both are configured, deliberately
pessimistic assumptions, because no historical quote data exists for either venue
and none is recoverable for a past date. The paper phase can measure both
forward. Where the measurement is *worse* than the assumption, every backtest
result is restated. Where it is better, the assumption stays as it is until the
Sponsor decides otherwise: a cost assumption is only ever loosened deliberately
and on the record.

**Neither measurement licenses a re-run of a strategy that was already
rejected.** A strategy rejected by the filter in section 7 stays rejected. New
cost measurements change the *null* as well as the strategy, and the comparison
is re-made on both sides or not at all.

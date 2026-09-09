# SEXTANT-005 verdict: does crypto momentum have an edge?

**Pre-registration `v1`, both parts committed before any result existed. 16 variants,
80 strategy trials, 253 trials in total including every null. 52 out-of-sample months
over a 66-month window. Every figure in EUR.**

---

## The answer

# (B) Insufficient evidence. Close the trading project.

Not one of the sixteen pre-registered variants made money, in any cost cell, under either
quote policy. Eighty variant-cells were run and **all eighty are negative**: the best lost
**28.40%** of the account over 52 out-of-sample months and the worst lost **99.77%**. In the
headline cell, where the criteria are judged, the best lost **78.89%** and the worst
**99.53%**. EUR cash returned 0% and beat every one of the eighty. Bitcoin, bought and held
through the same window at the same costs, returned **+86.31%** and beat every one of them by
at least 114 points.

Every variant lost money **before a single fee was charged**. Costs account for 10.8% of the
gross loss. There is no cost assumption that could be relaxed to rescue this.

---

## The registered verdict rule says (C). Here is why this document does not.

This is the most important paragraph in the report and it is placed before the evidence
rather than after it.

`config/spike-005.yaml` routes the verdict to **(C) undetermined** when "at least one variant
satisfies criterion 1 but not all five". Two variants satisfy criterion 1: `xs-momentum-360-5`
under the USDT policy, and `xs-momentum-360-10` under EUR. By the letter of the rule I
registered, the answer is (C).

**The rule misfires, and it misfires for a reason worth stating.** Criterion 1 asks whether a
variant's Sharpe beats the 95th percentile of its own exposure-matched random null. In a
cross-section where almost everything fell, that percentile is itself deeply negative — the
USDT fully-invested null has a median Sharpe of **−0.460** and a 95th percentile of **−0.147**.
A variant clears criterion 1 there by *losing less than chance lost*. `xs-momentum-360-5`
cleared it while losing 78.89% of the account. `xs-momentum-360-10` cleared it under EUR while
losing 52.14%. Neither is evidence of edge and neither could be.

The defect is mine: criterion 1 needed a sign condition, and criterion 3 carries one
("the selection effect's net return exceeds EUR cash") which the verdict rule failed to chain
in front of it. That is recorded here as a specification defect rather than corrected, because
a pre-registration is never edited after a number has been seen.

**(C) would also be factually false.** (C) means the data or the statistics cannot distinguish
edge from noise, and it obliges the report to state precisely what is missing and what it would
take to resolve it. Nothing is missing. The window is 66 months with 52 scored out of sample;
the effective observation count is 43 to 52 against a pre-registered floor of 24; the delisting
record carries 269 delistings inside the window; the trial count is honest at 253. Answering
"undetermined — nothing is missing" would be the mirror image of the failure the brief warns
about: presenting a decisive negative as an open question, exactly as presenting an
inconclusive result as a soft (A) would be.

So: the registered letter is **(C)**, the evidence supports **(B)**, both are on this page, and
the recommended decision is identical under either reading.

---

## What was tested

| | |
|---|---:|
| variants, pre-registered before any data | 16 |
| shapes | cross-sectional (8), time-series long/flat (8) |
| lookbacks | 30, 90, 180, 360 days |
| cost cells per variant | 4 |
| quote policies | 2 (USDT headline, EUR secondary) |
| **strategy trials** | **80** |
| deterministic runs kept whole | 186 (80 variants, 10 benchmarks, 48 selection-only, 48 timing-null) |
| seeded null distributions | 3 fully-invested (2,000 seeds each), 48 exposure-matched (500 seeds each) |
| **total trials in the registry, nulls included** | **253** |
| engine runs executed | ~30,000 |
| wall clock | 81.8 minutes |

The registered seed counts were used in full. The reduction ladder was never entered: a null
run measured 0.125 s against a 2-hour budget.

Every strategy was reported against all six benchmarks, all in EUR: cash, Bitcoin buy-and-hold,
the equal-weight passive universe, the fully-invested random null, its own exposure-matched
selection null, and its own timing null. No table mixes currencies.

---

## What was found

### The headline, and the two numbers that end the argument

| construct | net return | max drawdown | Sharpe |
|---|---:|---:|---:|
| **EUR cash** | **0.00%** | **0.00%** | — |
| BTC buy and hold | +86.31% | 63.65% | +0.525 |
| equal-weight passive, USDT universe | −86.60% | 90.14% | −0.298 |
| **best of 16 variants, headline cell** (`xs-momentum-360-5`) | **−78.89%** | — | +0.036 |
| worst of 16 variants, headline cell (`xs-momentum-30-5`) | −99.53% | — | −0.941 |
| best of all 80 variant-cells (`ts-trend-360-sma`, EUR) | −28.40% | — | −0.033 |

Doing nothing beat everything, for the second phase running. Holding one asset and never
trading beat everything by a wider margin still.

### Costs did not cause this

| | |
|---|---:|
| total gross PnL across the 16 variants, headline cell | −20,201.34 |
| total costs charged | 2,175.01 |
| costs as a share of the gross loss | **10.8%** |
| variants that lost money before any cost | **16 of 16** |

The spread and slippage assumptions are deliberately pessimistic and unmeasured, and they are
carried unchanged from SEXTANT-004 precisely so nobody can claim they were retuned. It does not
matter. Delete every cost line and every variant still loses.

### Deflation

Every USDT variant has a Deflated Sharpe Ratio of **0.0000** at the honest trial count of 253.
The highest DSR anywhere in the grid is **0.1513**, for `xs-momentum-360-10` under EUR, against
a pre-registered threshold of 0.95.

The undeflated statistic is already fatal. The best variant's probabilistic Sharpe against a
zero benchmark is **0.5300** — a coin flip that its true Sharpe is above zero, before any
correction for having tried eighty things.

### The decomposition: selection, timing, and the two together

| variant | combined | selection | timing | reading |
|---|---:|---:|---:|---|
| `xs-momentum-360-5` (USDT) | −78.89% | −78.89% | −86.55% | lost money; fully invested, so selection *is* the result |
| `xs-momentum-360-10` (EUR) | −52.14% | −42.13% | −61.53% | lost money; its selection lost less than its exposure, and still lost |

**Not one variant has a positive selection effect.** The best selection effect anywhere in the
grid is −42.13%. Criterion 3 — that the win is selection rather than reduced exposure — is
failed by all thirty-two headline variant-cells, because there is no win to attribute.

The trend family is worth one further sentence, because it is the family whose exposure varies
and the decomposition exists to judge it. **Only 3 of the 16 trend variant-cells beat their own
timing null.** The other thirteen did worse than simply holding the whole executable universe at
the same invested fraction. Standing partly aside in a falling market helped; choosing which
names to hold while standing aside hurt, and hurt more often than it helped.

### Per regime

Scored months: **bear 29, bull 15, recovery 8, crash 1.** Only three regimes carry the six
months the pre-registration requires before anything may be concluded from them; the single
crash month is reported and nothing rests on it.

The best variant, `xs-momentum-360-5`, was positive in **1 of 3** conclusive regimes:

| regime | months | variant | equal-weight |
|---|---:|---:|---:|
| bull | 15 | +12.8% | −16.4% |
| bear | 27 | −70.3% | −73.3% |
| recovery | 8 | −48.1% | −51.2% |

There is something here and it is worth naming honestly: in bull months this variant beat the
passive universe by 29 points, and it beat it in all three regimes. What it never did was make
money. A strategy that loses less than a falling market in every regime is a strategy that
tracks a falling market, and 15 bull months out of 52 is not a demonstration.

No variant in the grid was positive in more than one conclusive regime.

### How much evidence there actually is

| | |
|---|---:|
| scored months | 52 |
| effective observations, autocorrelation-corrected | 43 to 52 |
| pre-registered floor | 24 |
| standard error on an annualised Sharpe | 0.139 to 0.148 |
| smallest annualised Sharpe this sample could detect | **≈ 0.94** |
| positions held | 5 to 10 |
| mean pairwise correlation of the names held | **0.334 to 0.385** |
| **effective independent bets** | **2.07 to 2.38** |

Two things follow, and the second is the more useful.

**This sample could not have detected a small edge.** A true annualised Sharpe below about 0.94
is inside the noise here. The measured Sharpes are −0.94 to +0.04, so the honest statement is
that no *large* positive edge exists and that a small one could not have been resolved. That
limit is not what decides this verdict; the net returns are. A strategy whose best case is
losing 78.89% of the account over four and a third years does not need a significance test.

**Breadth was illusory, again.** Holding ten names bought 2.3 independent bets, because the
names moved together at a correlation of 0.36. The Kraken window failed for the same reason at
0.804 against Bitcoin. Widening the universe from 13 instruments to 307 did not buy the
independence it was supposed to, and this is now measured on two venues rather than one.

### Sign stability

All sixteen variants keep their sign across all four cost cells. Every one of the sixty-four
USDT variant-cells is negative, from −78.89% at the research venue's own schedule to −99.77% at
the other venue's taker rate. Criterion 5 is the only criterion the grid passes, and it passes
it by being uniformly, robustly negative.

---

## The five criteria, counted

| criterion | headline variant-cells passing, of 32 |
|---|---:|
| 1 — beats its own exposure-matched null | 2 |
| 2 — DSR ≥ 0.95 | **0** |
| 3 — the win is selection, not exposure | **0** |
| 4 — positive in ≥ 3 conclusive regimes | **0** |
| 5 — sign stable across cost regimes | 32 (all negative in all four cells) |
| **all five** | **0** |

---

## Why this is not enough to continue

Four independent reasons, any one of which would be sufficient.

**Nothing made money.** Eighty variant-cells, every one negative, over 52 out-of-sample
months. The margin is not marginal: the best case anywhere destroyed 28% of the account and the
best case in the headline cell destroyed four-fifths of it.

**The losses are not a cost artefact.** All sixteen variants are gross-negative. Costs are a
tenth of the damage.

**Deflation is fatal at an honest trial count.** DSR 0.0000 across the headline panel. Even the
undeflated statistic is a coin flip.

**Breadth does not exist in this asset class.** Ten names are 2.3 bets. That is a property of
crypto, measured now on two venues over two disjoint windows, and it caps how much
diversification any cross-sectional strategy in this universe can ever buy.

---

## What I would want a sceptic to attack first

Listed in the order I would attack them.

**The window is dominated by a falling market.** 29 of 52 scored months are bear. A momentum
strategy on a universe that fell 86.6% will lose, and one might argue this measures the window
rather than the strategy. The counter is in the per-regime table: in the 15 bull months the best
variant returned +12.8%, which annualises to nothing like enough to recover, and no variant was
positive in more than one conclusive regime. The counter is not airtight. A window with a
sustained altcoin bull run in its out-of-sample section would test this better, and no such
window exists in the archive with a venue-sourced EUR rate.

**Long-only.** The engine cannot short, by construction. Cross-sectional momentum is usually
tested long-short, and the short leg is where a falling cross-section would have paid. This
spike tested what this system can execute, and it says nothing about a long-short variant. That
is a real gap and it is the single most plausible way the hypothesis could survive.

**Monthly rebalancing only.** Nothing weekly or daily was tested. Both were outside the
pre-registered grid and testing them now would be post-hoc.

**The liquidity floor is low.** 250,000 quote units of daily turnover admits a median of 307
instruments, many of them thin. A stricter floor would produce a different universe. Untested,
and untestable without a new pre-registration.

**The regime cascade is my design.** A different rule would allocate months differently and
could move the per-regime table. It was fixed before any data and is computable point-in-time,
which is the defence, not a proof that it is the right cut.

**Two universe rules are inert.** Minimum-notional and lot-size feasibility admit everything,
because no historical instrument-constraint record exists for delisted symbols on this venue.
Recorded in part 2 section 2.7. The practical effect is nil at a 150 EUR target position, but
they are not doing work.

**One data gap.** `PORTOEUR-1d-2022-11` is served by the venue as a zero-byte object. That
symbol is not-evaluable across any window spanning November 2022. One symbol, one month.

---

## What was confirmed by running, and what was assumed

**Confirmed by running.** Every net return, Sharpe, drawdown and cost line in this document
came out of the walk-forward engine over the ingested archive. The 269 delistings, the 66-month
window, the 856,601 bars and the 28,577 checksummed objects are counts of things on disk. The
null distributions are 2,000 and 500 actual engine runs each, not analytic approximations. The
DSR trial count is read from the append-only registry, whose hash chain verifies. Both timestamp
eras of the archive were checked by hand against known calendar days.

**Assumed, and labelled everywhere.** The spread and slippage figures are configured, not
measured, and no historical quote data exists on either venue to calibrate them. The fill mix is
an assumption; at the research venue's schedule it cannot move the fee line, and it is reported
at three settings on the other venue's schedule. The 20% delisting haircut is a stated
assumption. The FX conversion is charged at the venue's spot rate, which is the applicable
schedule as far as the published tariff goes but was not verified against a live quote. **The
conclusion does not depend on any of them**: every variant loses gross.

---

## The research boundary

Binance was the research venue of this spike and nothing else. Nothing in this document is
evidence that any strategy is executable on Kraken, or that this account may trade on Binance.
That question is separate and remains open — and is now moot for this project, because there is
nothing to execute.

---

## Recommendation

**Close the trading project.**

Two phases have now measured the same thing on two independent venues, two disjoint windows and
two orders of magnitude of cross-section. Kraken's 24 months could demonstrate nothing at any
realistic effect size. Binance's 52 months could, and what they demonstrate is that sixteen
pre-registered momentum and trend variants lose money net and gross, in every cost regime, in
every regime of the market that carries enough months to speak, against a cash balance that
returned zero.

The remaining untested direction that could plausibly change this answer is a long-short
cross-sectional variant, which this engine cannot execute and which would need a new
pre-registration, a shorting cost model, a borrow-availability record that no archive supplies,
and a fresh window. That is a new project, not a continuation of this one, and the Sponsor
should decide whether to start it on its own merits rather than on the strength of anything
measured here.

What this project built is worth keeping regardless of that decision: a point-in-time universe
with a sourced delisting record on two venues, a walk-forward engine with no in-sample code
path, an itemised cost model, a calibrated null, an append-only trial registry, and the habit of
writing down what you are going to test before you test it. That machinery answered a question
in eighty-two minutes that would otherwise have been argued about indefinitely.

---

*Results: [`docs/SPIKE-005-RESULTS.md`](SPIKE-005-RESULTS.md). Specification:
[`docs/PRE-REGISTRATION-005.md`](PRE-REGISTRATION-005.md). Raw:
`research/spike-005.json`. Every trial: `research/trial-registry.jsonl`.*

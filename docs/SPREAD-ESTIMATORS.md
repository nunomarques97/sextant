# Estimating a bid-ask spread from daily prices, and why it does not work here

**The finding, in one line: the two standard low-frequency spread estimators cannot resolve
a crypto perpetual's spread, and the reason is a ratio rather than an implementation
detail.**

This document exists because amendment 12, rule A12.8, asks that the result be kept as a
methodological finding rather than filed as a failed amendment. It is the reason a future
version of this project must not reach for these estimators again, and it is worth more in
the repository than the amendment that produced it.

**What was tested, and against what.** Abdi and Ranaldo (2017) as the registered estimator
and Corwin and Schultz (2012) as a comparison, against 36 symbol-days of real top-of-book
quotes from the venue's own archive: six perpetuals on six days, 155 million quotes, every
object verified against the publisher's SHA-256. The acceptance test was written and
committed in `4572705` before the calibration in `cd8e1c9` was run, and the ordering is
checkable in git.

## The estimators

**Abdi and Ranaldo (2017)**, *A Simple Estimation of Bid-Ask Spreads from Daily Close,
High, and Low Prices*, Review of Financial Studies 30(9), 4437-4480. With `c` the log
close, `h` the log high, `l` the log low and `eta = (h + l) / 2` the log mid-range, the
two-day term is

```
s2_t = 4 * (c_t - eta_t) * (c_t - eta_{t+1})
```

and the period estimate is the square root of the mean of those terms. A non-positive mean
is reported as zero and counted, never rooted. The result is a proportional effective
spread.

**Corwin and Schultz (2012)**, *A Simple Way to Estimate Bid-Ask Spreads from Daily High
and Low Prices*, Journal of Finance 67(2), 719-760. The two-day high-low range against the
sum of the two daily ranges, with the paper's overnight-gap adjustment and its own
correction of setting negative two-day estimates to zero before averaging.

Both are implemented in [`spread_estimator.py`](../src/sextant/engine/execution/spread_estimator.py).

## What they produced

| symbol | measured quoted spread | Abdi-Ranaldo | ratio | Corwin-Schultz | ratio |
|---|---:|---:|---:|---:|---:|
| `BTCUSDT` | 0.0357 bps | 0.0000 bps | — | 93.66 bps | 2,622x |
| `ETHUSDT` | 0.0541 bps | 29.02 bps | 536x | 101.33 bps | 1,872x |
| `THETAUSDT` | 1.2482 bps | 17.20 bps | 13.8x | 112.19 bps | 89.9x |
| `BLZUSDT` | 1.4694 bps | 108.89 bps | 74.1x | 191.14 bps | 130.1x |
| `UNFIUSDT` | 2.0190 bps | 197.91 bps | 98.0x | 204.98 bps | 101.5x |
| `API3USDT` | 4.2452 bps | 223.39 bps | 52.6x | 204.14 bps | 48.1x |

Against rule E1's three registered clauses:

| clause | statistic | bar | holds |
|---|---:|---:|---|
| ordering, Spearman's rho across the six symbols | 0.943 | at least 0.771 | **yes** |
| magnitude, median `\|log2(estimated / measured)\|` | 6.413 | at most 1 | **no** |
| positivity, share of instrument-periods estimated positive | 42.6% | at least 90% | **no** |

**Not adopted.** Corwin-Schultz would clear positivity at 92.2 per cent and was not
promoted: it clears it by being systematically large, and its magnitude error is worse than
the estimator that was actually registered. Nothing was left on the table by refusing it.

## The one result worth keeping

**The ordering clause holds while the magnitude clause fails by a factor of 85.** The
estimator ranks the six symbols almost correctly — one swap of adjacent neighbours — while
being one to three orders of magnitude out on the level. It carries information about
*which* instrument is wider and none about *how* wide.

That is not a small distinction. A cross-sectional strategy trades the ranking, so an
estimator that ranks correctly is not useless; it is useless *as a cost*. Anything built on
these estimators in future must be built on their ordering and never on their level.

## Why, in the form that forecloses the obvious next suggestion

Each two-day term estimates the squared spread **plus noise whose scale is the daily
variance**. So whether the estimator can resolve anything is a ratio, not a judgement. The
standard error of a mean of `n` terms falls as the root of `n`, so resolving a squared
spread `s2` at one standard error needs

```
n  >=  (scatter of the two-day terms / s2) ^ 2
```

Measured on the real data, per symbol:

| symbol | scatter of the two-day terms | squared measured spread | two-day pairs needed |
|---|---:|---:|---:|
| `API3USDT` | 2.666e-03 | 1.802e-07 | 2.188e+08 |
| `UNFIUSDT` | 2.208e-03 | 4.076e-08 | 2.935e+09 |
| `THETAUSDT` | 1.651e-03 | 1.558e-08 | 1.124e+10 |
| `BLZUSDT` | 3.666e-03 | 2.159e-08 | 2.884e+10 |
| `ETHUSDT` | 6.521e-04 | 2.929e-11 | 4.955e+14 |
| `BTCUSDT` | 5.619e-04 | 1.276e-11 | 1.939e+15 |

**`BTCUSDT` would need about two thousand million million two-day pairs, which is some five
trillion years of daily bars.** A thirty-day window against a thirty-year one is not the
difference. No choice of averaging period rescues this, and that is why the failure is
structural rather than a tuning problem.

## The implementation is not the explanation, and that is tested

[`test_spread_estimator.py`](../tests/unit/test_spread_estimator.py) simulates a
quote-driven market: an efficient log price on a random walk, trades printing alternately
at the bid and the ask, daily high, low and close taken from the prints.

| true spread | daily volatility | Abdi-Ranaldo recovers | Corwin-Schultz recovers |
|---:|---:|---:|---:|
| 100 bps | 1% | 124 bps | 108 bps |
| 50 bps | 1% | 69 bps | 69 bps |
| 10 bps | 1% | floored to 0 | 43 bps |
| 1 bps | 4% | floored to 0 | 151 bps |

Both estimators recover a known spread at 100 and at 50 basis points and both break down as
the spread falls relative to the volatility. **The arithmetic is right and the question is
impossible.**

## The regime these estimators were built for

They were validated on equities, where a spread of tens of basis points sits against daily
moves of one or two per cent. A perpetual quoting **0.036 bps against four per cent daily
volatility** is four orders of magnitude outside that regime. The papers are not wrong and
the implementations are not wrong; the instrument is different by so much that the estimator
is measuring its own noise.

**The general form of the lesson.** A low-frequency estimator of a microstructure quantity
has a signal-to-noise ratio set by the quantity over the volatility. Before reaching for
one, compute that ratio and the implied sample size. It takes a few lines and it would have
answered this question before any of the work was done.

## What was done instead

Rule E1's failure clause was honoured: the banded **assumption** was kept, labelled an
assumption. Amendment 12 then settled what that means in practice — the assumption is a
registered **upper bound**, roughly nineteen times the only measurement there is, it decides
every verdict, and it is reported beside the measured bound on every headline so that no
reader sees one without the other.

**And the correct response to a failed inference was not a better inference.** Rule H1
registers the alternative: check whether the venue published the quantity. It did, for part
of the window — which is how the 36 symbol-days these estimators were tested against exist
at all.

## Where the numbers are

- `research/spike-006-f1-estimator.json` — the calibration, every clause and every input
- `research/spike-006-f1-spread.json` — the 36 measured symbol-days it was calibrated against
- `docs/SPIKE-006-F1-RESULTS.md` section 7.6 — the same result in the report it belongs to
- `docs/PRE-REGISTRATION-006-F1.md` sections 33.4 and 33.7 — the rule, and its recorded outcome

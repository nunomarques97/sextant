# ADR 0006 - numpy, polars and scipy, and the boundary each is confined to

Status: accepted
Date: 2026-09-09
Phase: SEXTANT-004 (walk-forward backtesting engine and the calibrated null)

## Context

SEXTANT-004 builds the backtesting engine and calibrates what no edge looks
like. Three things it has to do cannot be done well with the standard library
alone: reshape several thousand parquet series into a panel, compute
distributional statistics over ten thousand simulated equity curves, and
evaluate the normal CDF and its inverse to the precision the Deflated Sharpe
Ratio needs.

Phase 0 §7 anticipated all three and asked that each arrive with the phase that
needs it, justified here rather than in a commit message.

The constraint that shapes every decision below is invariant 4: prices,
quantities, fees and PnL are `Decimal`. None of these three libraries has a
usable decimal type, and none of them is going to acquire one. So the question
is never "may we use floats", it is "where exactly do floats begin, and can that
place be named and tested".

## Decision

Add all three, each confined to a different set of layers, enforced by
`.importlinter` and by `tests/unit/test_numeric_boundary.py`.

### numpy - the statistics, and nothing else

**Why.** Sharpe, Sortino, drawdown, percentiles and bootstrap resampling over a
distribution of ten thousand simulated runs. Written in pure Python over
`Decimal` these are correct and unusably slow; the bootstrap alone is ten
thousand resamples of ten thousand values.

**Where.** `sextant.engine.statistics`, and `sextant.engine` generally. The
engine is allowed to compute with arrays. `domain` and `ports` are not.

**The rule that keeps it honest.** A `Decimal` becomes a float in exactly one
function, `engine.statistics.boundary.equity_curve_as_returns`, and a float
becomes a `Decimal` in exactly one other, `boundary.statistic_as_decimal`. Every
other module that imports numpy is forbidden from importing `Decimal` or any of
the monetary value objects, and a test asserts it over the whole source tree. A
boundary that exists in twelve places is not a boundary.

**Rejected:** `statistics` from the standard library. It covers mean, median and
standard deviation and stops there. No percentile-of-a-distribution with a
stated interpolation rule, no bootstrap, and no array semantics, so the
resampling loop would be Python-level anyway.

**Rejected:** computing the statistics in `Decimal` throughout. A Sharpe ratio
is a dimensionless estimate with a standard error larger than its own third
decimal place. Carrying twenty-eight significant digits through it implies a
precision the estimate does not have, and it would make the null experiment cost
hours rather than minutes. The right answer is not more precision in the
statistics, it is keeping the statistics away from the money.

### scipy - the normal CDF and its inverse

**Why.** The Deflated Sharpe Ratio is `Φ(·)` of a studentised expression, and
the expected-maximum term is `Φ⁻¹(1 - 1/K)`. `Φ` is expressible as
`0.5 * erfc(-x / sqrt(2))` with `math.erfc`, but `Φ⁻¹` is not in the standard
library at all, and a hand-rolled rational approximation to it is precisely the
kind of code that is wrong in the tail and never tested there.

**Where.** `sextant.engine.statistics.dsr`, through `scipy.stats.norm` only.

**Rejected:** implementing `Φ⁻¹` ourselves from Acklam's or Wichura's
approximation. It is thirty lines and it would need its own reference cases in
the tail, which is work spent reproducing a library everyone else already
trusts. The DSR's own reference cases (ADR body below, and
`tests/unit/test_deflated_sharpe.py`) are where our verification effort belongs.

**Rejected:** `statsmodels`. Far larger, pulls pandas, and we need two functions.

### polars - loading the panel, in the adapter layer only

**Why, and one claim withdrawn.** The null experiment needs daily closes and
turnover for roughly 1,600 symbols over 39 months, which is 1,600 parquet files.
The obvious justification is speed, and it does not survive measurement: reading
all 1,640 series one at a time through `ParquetBarStore` takes **4.8 seconds**,
and the polars scan of the same directory takes **4.8 seconds**. There is no
speed argument here and this ADR does not make one.

What polars does earn is the shape of the code. The loader is one scan with a
declared schema and no per-file bookkeeping, the reshaping a cross-sectional
research phase needs is expressible in it directly, and the values stay text all
the way through so nothing can parse an exact decimal into a float behind our
backs. The dependency is also mandated by the SEXTANT-004 brief and anticipated
by Phase 0 §7. It is added on those grounds, confined to one module, and the
performance claim that would have been the easy justification is stated here as
false rather than left implied.

**Where.** `sextant.adapters.storage` only. Not the engine, not `app`, not
`domain`, not `ports`. The engine asks the `BarRepository` port; it never learns
that a dataframe was involved.

**Why polars rather than pandas.** This is the recommendation SEXTANT-002 made
and the reason has not changed: pandas has an implicit index that participates
silently in every alignment, so a join or an arithmetic operation between two
frames can reorder or reindex rows without an error and without a diff. In a
point-in-time system that failure mode is indistinguishable from look-ahead. In
polars there is no index, so the family of bugs is not expressible. polars is
also strict about dtypes, which matters here because prices are stored as text
(ADR 0005) and a library willing to guess would parse them into floats behind
our backs.

**Rejected:** pandas, for the reason above. **Rejected:** doing it with duckdb,
which is already a dependency. duckdb could do the scan, and it does the row
counting in `ParquetBarStore.row_counts` today. It was rejected on the same
maintainability grounds as above, and given that the speed argument turned out
to be empty, this is worth saying plainly: **duckdb could have done this, and so
could the existing store.** If the Product Owner would rather carry one fewer
dependency, dropping polars costs three lines of code and no measurable time.

## Consequences

- Three more runtime dependencies, all widely used and all with published wheels
  for the target platform.
- One new failure mode to guard: a monetary value silently becoming a float.
  Guarded in three ways - the import contracts above, the single-function
  boundary, and `test_numeric_boundary.py`, which fails if any module outside
  the boundary imports both an array library and a monetary type.
- `uv.lock` is **not** regenerated by this change, because `uv` is not installed
  on the machine this phase was built on. The dependencies were installed into
  the existing virtual environment with `pip --python`. Whoever next has `uv`
  available must run `uv lock` and commit the result; until then the lock file
  and `pyproject.toml` disagree about three packages. This is recorded rather
  than hidden, and it is the one loose end this ADR leaves.
- The versions the SEXTANT-004 results were computed under are recorded in every
  run manifest, so a later reader can tell whether a number was produced under
  the same arithmetic.

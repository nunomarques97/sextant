# Phase 0 findings and architecture proposal

**Task:** SEXTANT-001 - repository bootstrap and architecture proposal
**Date:** 2026-09-09
**Status:** for Product Owner review. No trading logic exists. Phase 1 has not started.

---

## 1. What was built

The engineering foundation and nothing else. No strategy, no indicator, no
feature engine, no regime classifier, no backtester, no risk rule, and no
network call to any exchange or model provider.

### Layout

```
sextant/
  pyproject.toml           uv, ruff, mypy strict, pytest, import-linter
  uv.lock                  committed
  .importlinter            the layering contract - the main deliverable
  .env.example             variable names only, no values
  config/
    base.yaml              shared ground
    backtest.yaml          the default profile
    paper.yaml
    live.yaml
  src/sextant/
    domain/                money, time, venue, instrument, universe, market_data,
                           capability, availability, mode, risk, decision, errors
    ports/                 clock, exchange, repository, cost, llm
    adapters/
      clocks.py            SystemClock, SimulatedClock
      exchanges/
        base.py            shared scaffolding, no venue branching
        binance/           capabilities.py + client.py
        kraken/            capabilities.py + client.py
        registry.py        venue name -> adapter class, a lookup table
      storage/             placeholder
      llm/                 placeholder
    engine/
      features/ regime/ strategies/ risk/ execution/   documented skeletons
    app/
      settings.py config.py credentials.py preflight.py
      logging_setup.py wiring.py startup.py cli.py
  tests/unit/  tests/integration/
  docs/adr/  docs/LIVE-GATES.md  docs/PHASE-0-FINDINGS.md
  .github/workflows/ci.yml
```

### What is actually enforced, not merely intended

| Rule | Mechanism |
|---|---|
| `domain` imports nothing else in the system | import-linter contract `domain-is-pure` |
| `engine` never sees an adapter or the wiring layer | import-linter contract `engine-is-abstract` |
| `ports` never see an adapter | import-linter contract `ports-are-abstract` |
| Exchange SDKs only inside `adapters.exchanges` | import-linter contract `exchange-sdks-are-quarantined` |
| Binance and Kraken cannot import each other | import-linter contract `venues-are-independent-peers` |
| No venue name in `domain`, `ports`, `engine` | `tests/unit/test_source_hygiene.py` |
| No `if venue == "..."` anywhere | `tests/unit/test_source_hygiene.py` |
| No `Any`, `cast()` or type-ignore in `src` | `tests/unit/test_source_hygiene.py`, tokenised |
| No wall-clock read outside `adapters` | `tests/unit/test_source_hygiene.py` |
| No LLM schema field for size, leverage, exposure, stop or risk budget | `assert_no_risk_fields` at import + tests |
| Floats rejected for monetary values | `MoneyTypeError` at construction + tests |
| Naive datetimes rejected | `NaiveDatetimeError` at construction + tests |
| LIVE cannot start without three independent conditions | `require_live_authorisation` + preflight + tests |

### The deliberately failing experiment

Required by the acceptance criteria and automated so it cannot rot. The test
`test_a_forbidden_import_actually_breaks_the_build` writes a real violating
module, runs import-linter in a subprocess, asserts a non-zero exit, deletes the
module, and a following test asserts the contracts hold again. Three violations
are exercised:

| Probe written | Forbidden import | Contract broken |
|---|---|---|
| `src/sextant/domain/_contract_probe.py` | `from sextant.adapters.clocks import SystemClock` | `domain-is-pure` |
| `src/sextant/engine/_contract_probe.py` | `from sextant.adapters.exchanges.kraken.client import KrakenClient` | `engine-is-abstract` |
| `src/sextant/adapters/exchanges/kraken/_contract_probe.py` | `from sextant.adapters.exchanges.binance.client import BinanceClient` | `venues-are-independent-peers` |

Run by hand once as well. Adding
`from sextant.adapters.exchanges.kraken.client import KrakenClient` to a module
under `src/sextant/engine/` and running `uv run lint-imports` produced:

```
Contracts: 4 kept, 1 broken.

----------------
Broken contracts
----------------

engine never sees a concrete adapter or the wiring layer
--------------------------------------------------------

sextant.engine is not allowed to import sextant.adapters:

-   sextant.engine._forbidden_probe -> sextant.adapters.exchanges.kraken.client (l.3)
```

Exit status 1. After deleting the file: `Contracts: 5 kept, 0 broken.`, exit
status 0.

---

## 2. The stack decision

Implemented as specified: Python 3.12, uv, ruff, mypy strict, pytest,
import-linter, and exactly the three permitted runtime dependencies.

**I have no material disagreement with the stack.** The .NET alternative is
argued and rejected in ADR-0001; the short version is that the compile-time
safety it offers is aimed at a class of bug that does not kill trading systems,
while the research ecosystem it lacks is the binding constraint on every phase
ahead. The three-dependency table is right, and `pyyaml` is correctly preferred
over a hand-rolled parser.

Four things I had to decide or deviate on. All are technical, none change a
product decision.

**1. `uv` is not installed on this machine.** `uv --version` is not found, and
it is absent from the usual install locations. Per the standing rule I did not
install it globally. To produce the lockfile and verify the acceptance criteria
I installed `uv` into a throwaway virtual environment inside the session
scratchpad, which changes nothing outside that directory. **The Sponsor needs to
run one command**, in PowerShell:

```powershell
winget install --id=astral-sh.uv -e
```

Until then, `uv run ...` will not work from a normal shell.

**2. `disallow_any_explicit` was removed from the mypy configuration.** I first
enabled it to enforce the "no `Any`" constraint mechanically. It reports an
error on *every* `pydantic.BaseModel` subclass, because `BaseModel` itself
declares `__pydantic_extra__: dict[str, Any]`. That is an incompatibility
between an optional strictness flag and a mandated dependency, not a finding
about our code. `strict = true` remains on, plus `warn_unreachable`,
`warn_unused_ignores` and `disallow_any_generics`. The ban on `Any`, `cast()`
and type-ignore comments in `src` is instead enforced by a test that tokenises
every source file, which is stricter in one useful way: it catches them in
comments and in code mypy never analyses.

**3. One deliberate type widening, in `app/config.py`.** `BaseSettings.__init__`
genuinely accepts `**values` at runtime - that is how pydantic-settings layers
init values underneath the environment - but mypy synthesises a strictly typed
`__init__` from the field annotations, which no mapping can satisfy. Rather than
a `cast()` or a `# type: ignore`, the merged YAML is passed through a
`Callable[..., Settings]` binding that describes the real signature. Validation
is unaffected: an unknown or mistyped key still fails loudly. I am flagging it
because it is the one place where I widened a type rather than satisfying it.

**4. PAPER with credentials cannot start today.** R7 says the
withdrawal-permission check applies in PAPER "only when credentials are actually
in use". I implemented that, and additionally treat an *undeterminable*
withdrawal permission as a failure in PAPER as well as in LIVE, not only in
LIVE. Since the venue probe is stubbed and returns `UNKNOWN`, the consequence is
that a paper run which requests `ACCOUNT_DATA` or any trading capability will
refuse to start until the probe is wired. Paper trading on public data with a
simulated portfolio - the mandatory path - works with no credentials at all and
is unaffected. My reasoning: a key with withdrawal permission is exactly as
dangerous in a paper run as in a live one, because the danger is the key, not
the mode. If the PO wants paper-with-account-data to be usable before the probe
exists, say so and I will downgrade the PAPER case to a warning.

---

## 3. Proposed architecture

### Module boundaries

```
                    app/  (the only place adapters meet ports)
                      |
        +-------------+--------------+
        |                            |
     ports/  (Protocols)          adapters/  (all I/O)
        ^                            |
        |                     exchanges/binance, exchanges/kraken,
     engine/  (all logic)      storage/, llm/, clocks
        |
     domain/  (pure types; imports nothing)
```

`domain` is the only package every other package may depend on, and it depends
on nothing. `engine` sees `domain` and `ports`. `adapters` see `domain` and
`ports`. `app` sees everything. No cycle is expressible.

### Port interfaces, as implemented

**`Clock`** - `now() -> Timestamp`. The reason backtest, paper and live are the
same program. `SystemClock` for paper and live, `SimulatedClock` for backtest;
the engine cannot distinguish them.

**`ExchangeClient`** - `venue`, `capabilities() -> CapabilitySet`,
`health() -> VenueHealth`, `instruments(at) -> Sequence[Instrument]`,
`get_bars(instruments, timeframe, start, end)`, `get_order_book(instrument)`,
`withdrawal_permission()`. Every data method takes its instruments explicitly.
`instruments(at)` is point-in-time and must include instruments that were listed
then and have since been delisted.

**`BarRepository`** - `read(instruments, timeframe, start, end, *, closed_only=True)`,
`write(bars) -> int` (idempotent), `latest_close_time(instrument, timeframe)`.
Keyed by `(venue, symbol, timeframe)`. `closed_only` defaults to `True` so the
safe read is the one you get by not thinking; consuming an open bar is opt-in
and visible at the call site.

**`CostModel`** - `estimate(instrument, side, quantity, reference_price, role, at) -> CostBreakdown`,
where the breakdown is `fee_bps`, `spread_bps`, `slippage_bps`, `funding_bps`,
kept itemised because they behave differently. Fees are contractual, spread and
slippage are market state, funding accrues with holding time rather than with
the trade. This is the port that makes "which venue is best for this strategy"
an output rather than an assumption: the same signal priced through two cost
models gives two different net results.

**`LLMAnalyst`** - `classify_regime(request) -> RegimeAssessment`,
`recommend(request) -> StrategyRecommendation`. Both responses forbid extra
fields and cannot express a size, a leverage, an exposure, a stop distance or a
risk budget. See ADR-0003.

### Data flow

```
  ExchangeClient / BarRepository
            |
            v
   Universe.members_at(as_of)          point-in-time; delistings retained
            |
            v
        features                        closed bars only; cross-sectional
            |
            +----------------> LLMAnalyst  (advisory, off the latency path)
            v                        |
         regime  <-------------------+     deterministic classifier is authoritative
            |
            v
        strategies                     produce a signal per instrument
            |
            v
        CostModel                      net expected edge, per venue
            |
            v
    Risk Engine (deterministic)        APPROVE | REDUCE | REJECT, with veto
            |
            v
        execution                      ExchangeClient, guarded by capabilities()
            |
            v
      DecisionRecord                   llm_decision and risk_verdict kept apart
```

The one-way property that matters: there is no arrow from `LLMAnalyst` to
`execution`. Everything the model produces is either recorded or consumed by the
Risk Engine, which decides alone.

---

## 4. Capability lifecycle

Requested explicitly. This defines the boundary and the lifecycle so later
phases can implement it without leaking venue or jurisdiction logic into the
domain. None of the "not yet implemented" rows are built in SEXTANT-001.

| | **Venue layer** | **Account layer** | **Jurisdiction layer** |
|---|---|---|---|
| **Answers** | Can the exchange do this at all? | Does this account's tier, verification and key permission allow it? | Is it permitted for this country of residence? |
| **Source of truth** | The venue's public API documentation | The venue's account settings and API key scopes | Regulation, and the venue's terms for that country |
| **Populated by** | A constant in the venue's adapter package, reviewed by hand | Configuration today (`exchanges.<venue>.account_capabilities`); a runtime probe later | Configuration only (`jurisdictions.<code>.allowed_capabilities`) |
| **Change frequency** | Rarely - a venue product release | Occasionally - a tier upgrade, a re-verification, a key rotation | Unpredictably - a regulatory change, a venue withdrawing a product from a country, or the account holder moving |
| **Configuration, probe, or both** | Code constant. Never configuration: a user must not be able to claim a venue supports something it does not | Both, eventually. Configuration is the declared expectation; the probe is the observed reality | Configuration only. There is no API that answers this |
| **Staleness detection** | **Not implemented.** Proposed: a dated `reviewed_on` field beside the declaration, and a CI warning past an age threshold | **Not implemented.** Proposed: probe at startup, compare with configuration, and fail startup on disagreement rather than silently preferring one | **Not implemented.** Proposed: a `reviewed_on` date per jurisdiction block, surfaced in `sextant status`, and refused outright in LIVE past an age threshold |
| **When it cannot be determined** | Cannot happen - it is a constant | Treat as **denied**. An unproven permission is not a permission | Treat as **denied**. A missing jurisdiction block already fails preflight today |

The uniform rule: **undeterminable means denied.** It is already how the
withdrawal-permission check behaves, and it is the only default that fails
safe. The cost is that a probe outage looks like a restriction; that is the
right trade, and the distinct `VenueUnavailable` error exists precisely so the
two can be told apart in a diagnostic.

---

## 5. Initial asset universe proposal

### The venue to start on

**Kraken.** The account exists there, it quotes in EUR, and its account
capabilities are the ones we can actually confirm. Binance is configured as an
equal peer with market-data capabilities only, pending research into what a
Portuguese retail account may actually do there. This is a starting point for
research, not a ranking: the point of the cost model is that the venue question
gets answered by measurement.

### Selection rules

Every rule below is computable at the decision date from information that
existed at the decision date. None refers to a realised return, to a ranking of
today's largest assets, or to anything requiring hindsight.

| # | Rule | Threshold | Bias it defends against |
|---|---|---|---|
| 1 | Quote currency is one the account can actually trade on that venue | EUR, USD, USDT | Backtesting pairs we cannot fund |
| 2 | Minimum listing age at the decision date | 180 days | Look-ahead, and listing-pump artefacts that are not repeatable |
| 3 | Rolling median quote volume over the preceding window | 250,000 quote units, 30-day window | Liquidity/selection bias - illiquid names show spectacular paper returns |
| 4 | Typical spread over the preceding window | <= 25 bps median | Cost realism; a tight backtest on a wide market is fiction |
| 5 | `min_notional` feasible against the account | <= 25% of the target position size | Backtesting positions we could not actually take |
| 6 | `lot_size` granularity feasible against the account | rounding error <= 1% of target position | Same, in the quantisation direction |
| 7 | Not in an excluded asset class | see below | Duplicated exposure and non-representative return processes |

Median rather than mean throughout, for volume and spread alike: both
distributions are dominated by outliers, and a mean lets one frantic day admit
an instrument that was untradable for the other twenty-nine.

Target position size, for rules 5 and 6: with `account_equity_quote` of 1,750
EUR and `max_positions` of 12, a target position is about 145 EUR. Rule 5
therefore requires `min_notional <= 36 EUR`. Both numbers are configuration
(`config/base.yaml`), not code.

### Excluded by construction

| Class | Examples | Why |
|---|---|---|
| Stablecoins | USDT, USDC, DAI, EURT | The return process is a peg, not an asset. They add no cross-sectional dispersion and would dominate any low-volatility ranking for reasons that have nothing to do with the strategy |
| Wrapped and duplicated assets | WBTC, WETH, renBTC | Near-perfectly correlated with the underlying. Including both double-counts one bet while appearing to be two, which inflates apparent breadth and silently concentrates risk |
| Leveraged tokens | any 3L/3S product | Path-dependent decay. They do not track the underlying over a multi-day horizon, so a signal computed on the underlying is not a signal on them |
| Staked and yield-bearing derivatives | stETH, sETH2 | Mostly the underlying plus an accrual, with an occasional discount that is a credit event rather than a price signal. Same duplication problem as wrapping, with an extra failure mode |

Configured as `universe.excluded_asset_classes`. The classification itself is
data, needing a maintained asset-class map; that map does not exist yet and is
listed in the risks.

### Target size and what happens when it is not met

30 to 50 instruments is a target, not a quota. Criteria are never loosened to
reach a headcount.

If the point-in-time rules yield 17 instruments in a period, the universe for
that period **is** 17, and the thinness is reported. Concretely:

- **Above ~25 instruments:** run normally.
- **15 to 25:** run, and flag the period as low-power in the results. A
  cross-sectional rank across 18 names has wide confidence intervals; the number
  goes in the report so the reader discounts it appropriately.
- **Below 15:** run, but exclude the period from any aggregate statistic that
  assumes cross-sectional breadth, including the Deflated Sharpe Ratio input.
  Report the exclusion and the count.
- **Never:** relax a threshold for the thin period alone. That is a
  hindsight-driven rule change, and it is exactly how a universe silently
  becomes a selection of what happened to work.

If thinness is chronic rather than occasional, the honest response is a rule
change applied uniformly to **every** period and the whole evaluation re-run -
for example widening the quote-currency set, or lowering the volume floor - and
that change is itself a parameter set that must be counted in the Deflated
Sharpe calculation. It is not free.

### Historical universe, including delisted assets

This is the part I am least able to promise, and I want to be explicit about
what I know versus what I expect. **No network call was made in this task**, so
nothing below is verified.

| Venue | Expected source for delisted instruments | Confidence |
|---|---|---|
| Kraken | The REST `AssetPairs` endpoint returns currently-listed pairs only, and OHLC returns a short recent window, so neither is sufficient. The expected source is Kraken's published historical trade-data downloads, which have historically included pairs that no longer trade | Low. Must be verified before the data phase is planned |
| Binance | `exchangeInfo` returns current symbols only, but the public market-data archive publishes per-symbol kline and trade files whose directories persist after a symbol stops trading | Medium. Must still be verified |

**Treat this as an unresolved risk, not a solved problem.** The first task of
the data phase should be to establish, by actually fetching, whether a delisted
pair's history is retrievable per venue. If it is not, there are three options
and the PO should choose: accept a survivorship-biased historical universe and
state the bias in every result; buy a vendor dataset; or restrict the backtest
window to a period we can reconstruct completely. The first option is the one I
would argue against, because a survivorship-biased backtest that clears the live
gates has cleared nothing.

### Refreshing membership over time

- **Cadence:** monthly, on the first UTC day of each month.
- **Information cut-off:** the previous day's close. No rule may read a bar that
  had not closed at the decision instant.
- **Entry:** an instrument that first satisfies every rule at a refresh becomes
  a member from that refresh onward.
- **Exit on rule failure:** a member that fails a rule at a refresh is removed
  from the next period, not retroactively from the current one.
- **Exit on delisting:** immediate at the delisting instant, with any open
  position marked out at the last traded price and the event recorded. This is
  the case that must never be silently deleted from history.
- **Buffer:** an instrument must satisfy the rules at two consecutive refreshes
  before entering, and fail at two consecutive refreshes before leaving. This
  suppresses churn around a threshold, and churn is pure cost.

Biases each part defends: monthly point-in-time recomputation defends against
look-ahead; retaining delistings and marking them out defends against
survivorship; the buffer defends against threshold-crossing noise being
mistaken for signal; and the fixed cadence, rather than "refresh when results
look wrong", defends against selection bias in the refresh itself.

---

## 6. Technical risks, most serious first

**1. Delisted historical data may not be obtainable per venue.** If it is not,
survivorship bias is structural and no amount of care downstream removes it.
Every strategy result would be optimistic by an unknown amount. This is first
because it is the only risk on this list that can invalidate everything built on
top of it. Mitigation: verify by fetching, in the first week of the data phase,
before any backtester is written.

**2. Statistical power may never be sufficient.** This is the risk the whole
multi-asset design exists to address, and it may still bite. With 30 to 50
instruments on a multi-day horizon, a year yields a few hundred trades - but
crypto instruments are heavily cross-correlated, so the *effective* independent
sample is far smaller than the trade count suggests. A Deflated Sharpe Ratio
computed honestly, counting every strategy and parameter set tried, may simply
never come out positive. That is a real possible outcome of this project and
planning should accommodate it rather than assume it away.

**3. Cost modelling may be optimistic in exactly the way that matters.** Fees
are knowable. Historical spread and slippage are not, without order-book data
that is expensive to obtain and store. A cost model calibrated from bar data
will underestimate the cost of trading illiquid names, which is precisely where
a cross-sectional strategy finds its apparent edge. This is the classic path by
which a backtest passes and paper trading fails.

**4. Binance account capabilities for a Portuguese retail account are
unknown.** Configured as market-data only pending research. If it turns out to
be market-data only in practice too, the "two independent venues" premise
weakens to one tradable venue plus one data source. The architecture handles
that without a code change, but the PO should know it is a live possibility.

**5. Capability staleness has no detection mechanism.** All three layers are
semi-static and none has a freshness check. A venue that quietly stops
supporting something, or a key whose scope was changed in a web UI, produces a
venue-side rejection at the worst moment rather than a clean local refusal.
Proposals are in section 4; none is implemented.

**6. Position sizing is marginal at this account size.** 1,750 EUR across up to
12 positions is about 145 EUR each. A 30% drawdown puts several positions near
venue minimums, at which point the strategy cannot be executed as designed and
the live results stop resembling the backtest. The `min_notional` rule defends
the entry case; it does not defend the drawdown case.

**7. LLM-in-the-loop results are not reproducible by default.** Model versions
change, and a backtest that calls a model is not replayable. Any evaluation
including LLM input needs recorded responses stored as fixtures and replayed,
with the model version pinned in run metadata. Otherwise a result cannot be
re-derived six months later, which fails the reproducibility gate.

**8. Development is on Windows, CI is on Linux.** Encoding and path divergence
is real - it surfaced during this task as a `cp1252` decode failure on a
subprocess capture. `.gitattributes` normalises line endings and CI runs on
Linux, so divergence surfaces on every push rather than at a bad moment.

**9. An asset-class map does not exist.** The exclusion rules for stablecoins,
wrapped assets, leveraged tokens and staked derivatives need a maintained
classification. Getting it wrong in the permissive direction admits duplicated
exposure that looks like breadth.

---

## 7. Dependencies needed in later phases

None of these are installed. Each arrives with the phase that needs it and
should be justified in that phase's ADR.

| Phase | Dependency | Why |
|---|---|---|
| Data | an HTTP client (`httpx`) | Venue REST calls with timeouts and connection reuse |
| Data | `ccxt`, or per-venue SDKs | Venue connectivity. Quarantined by contract to `adapters/exchanges`. Worth weighing: `ccxt` normalises two venues at the cost of a large surface and its own opinions |
| Data | `duckdb` or `pyarrow` + parquet | Bar storage keyed by `(venue, symbol, timeframe)`, columnar, retaining delistings |
| Research | `numpy`, and `polars` or `pandas` | Vectorised feature and cross-sectional computation. Recommend `polars`: stricter, and its lack of a silent index avoids a family of alignment bugs |
| Research | `scipy` | Deflated Sharpe Ratio and the accompanying statistics |
| LLM | `anthropic` | The LLMAnalyst adapter. Confined to `adapters/llm` |
| Testing | `hypothesis` | Property tests for cost accounting and for point-in-time invariants, where example-based tests are weak |

---

## 8. Testing strategy for the engine phases

Ordinary unit tests are assumed. What follows is specifically how backtest
*correctness* gets tested, because a backtester that is wrong in a flattering
direction produces confident, plausible, worthless numbers.

**Look-ahead bias.** Two independent mechanisms.
*Poisoned future:* run the engine over a dataset, then re-run with every bar
after instant T replaced by garbage, and assert every decision up to T is
byte-identical. Any leak from the future changes an earlier decision.
*Structural:* assert that a feature evaluated at `as_of` never reads a bar whose
`close_time > as_of`, by driving it through a repository that records its reads
and asserting on the recorded set.

**Survivorship bias.** Run the same strategy over the same window twice: once
with the true point-in-time universe including delistings, once with only
instruments still listed today. Assert the results differ, and assert the
delisted instruments actually appear in the first run's decisions. A test that
merely checks membership resolution is not enough - it must be shown that
excluding delistings changes the answer, otherwise the fixture is not exercising
the bias at all.

**Fee and funding accounting.** A property test: for any generated sequence of
trades, `gross_equity_curve - sum(per_trade_costs) == net_equity_curve` exactly,
in `Decimal`, to the smallest unit. Exactly, not approximately - the types make
that achievable and any tolerance would hide the errors worth finding. Funding
gets its own test tying accrual to holding time rather than to trade count.

**Partially formed bars.** Assert the repository's default read excludes open
bars; assert `Bar.require_closed()` raises on an open bar; and run one full
engine pass over a dataset whose final bar is open, asserting it is not
consumed. This one is already partly covered: `OpenBarConsumed` exists and is
tested.

**Reproducibility.** Same seed, same data, same configuration must produce a
byte-identical `DecisionRecord` stream. Implemented as a golden-file test that
fails on any diff. This is also what catches accidental dependence on dictionary
ordering, on wall-clock time, or on an unseeded generator.

**Clock equivalence.** Drive identical logic under `SimulatedClock` and under a
recorded `SystemClock` trace, and assert the decisions match. This is what makes
the claim "backtest and live run the same code" testable rather than
architectural.

**Venue agnosticism, measured.** Run one strategy priced through two venues'
cost models. Assert the *gross* signals are identical and the *net* results
differ. Identical gross proves the strategy does not depend on the venue;
differing net proves the cost model is actually doing work.

**Cross-sectional correctness.** Assert that a rank or z-score computed across a
universe slice changes when an instrument is added or removed. A cross-sectional
feature that is stable under universe changes is secretly per-symbol.

---

## 9. Decisions taken, and what was rejected

Each of these was mine to make technically. The rejected alternative is stated
so the PO can overrule cheaply.

**Venue is an opaque slug, not an enum.** Rejected: a `Venue` enum listing
binance and kraken. An enum in the domain is one `match` statement away from
venue branching, and it forces a domain edit for every venue added. The cost is
that a typo in a venue name is caught at configuration load rather than at
import.

**Denial precedence is venue, then account, then jurisdiction.** Rejected:
reporting every denying layer. When more than one layer denies, one is named.
The order runs from the most fundamental fact to the most changeable one. The
effective set is a plain intersection and is unaffected.

**Jurisdiction is keyed by jurisdiction code, not by `(jurisdiction, venue)`.**
Rejected: a per-venue-per-jurisdiction override table. The finer model is more
accurate in reality - EEA derivatives access genuinely differs by venue - but
keying anything by venue name reintroduces exactly what the model exists to
prevent. If accuracy demands it later, the extension stays data: an optional
override block keyed by `(jurisdiction, venue)`, resolved generically.

**A shared `BaseExchangeClient` rather than duplicated adapters.** Rejected:
fully independent adapters with no shared code. The base holds only the
capability arithmetic and unimplemented port methods; it contains no venue
branch, and the independence contract still holds because neither venue package
imports the other. The risk is that the base becomes where branching creeps in;
the source-hygiene test is the guard.

**A run names its venue in its profile file, not in `base.yaml`.** Rejected: a
default venue in the shared base. A default in the shared file is a global
default by another name, and every run would inherit it without stating it.
Requiring it per profile means adding a profile forces the question to be
answered.

**`SEXTANT_ALLOW_LIVE` is not a settings field.** Rejected: modelling it as
configuration. As a field it would be one YAML edit away from being on, and it
would appear in a diff as a routine value change. As an environment variable
read directly from the process environment, turning live on is an act performed
in a shell, deliberately, and it cannot be committed.

**Preflight skips rather than passes checks that do not apply.** Rejected: a
uniform set of checks with a mode-dependent severity. A skipped check reports
why it was skipped; a check that always runs and always passes teaches everyone
to ignore it. The report distinguishes the two.

**The credential requirement is derived from capabilities, not from mode.** A
run needs credentials when its required capabilities intersect
`CREDENTIALED_CAPABILITIES`, or when the mode is LIVE. Rejected: a per-mode
boolean. Deriving it means paper-on-public-data needs no keys without anyone
configuring that, and a paper run that quietly starts using account data cannot
forget to demand them.

**Log redaction filters live on the handler, not on the logger.** A logger's own
filters are skipped for records emitted by its children, so redaction attached
to the `sextant` logger would silently not apply to `sextant.app`,
`sextant.adapters` or anything else that actually logs. Tested directly.

**Bar close time is derived, not stored.** `close_time = open_time + timeframe.duration`.
Rejected: storing both. Two stored fields can disagree, and the disagreement
would be invisible.

---

## 10. Open questions for the Product Owner

Only genuinely product-level questions. Everything technical was decided and is
recorded above.

1. **If delisted history proves unobtainable free, what is the fallback?**
   Accept and declare survivorship bias, buy a vendor dataset (there is a real
   cost), or restrict the backtest window to a fully reconstructible period? My
   recommendation is to restrict the window rather than accept the bias, but the
   trade-off is the PO's.

2. **Quote-currency policy.** EUR only, matching the account and avoiding FX and
   stablecoin credit risk but yielding a materially thinner universe? Or include
   USD and USDT for breadth, accepting that USDT introduces a peg risk that is
   not a trading signal? This directly changes the answer to "how many
   instruments do we have".

3. **Is spot-only acceptable for the foreseeable roadmap?** The jurisdiction
   layer currently denies margin and futures for PT. Confirming spot-only lets
   the funding-rate work and the futures capability be deferred entirely.

4. **Confirm the account parameters.** 1,750 EUR equity and a maximum of 12
   concurrent positions are currently configuration defaults I chose from the
   stated 1,500-2,000 range. Both drive the `min_notional` feasibility rule and
   therefore the universe size.

5. **Should LLM analysis be in scope for the first backtests at all?** Including
   it early costs reproducibility work (recorded responses, pinned model
   versions) before there is a deterministic baseline to compare it against. My
   recommendation is to build and evaluate the deterministic pipeline first, and
   add the analyst as a measured increment once there is something to measure it
   against. That is a scope call, not a technical one.

6. **Which venue leads the data phase?** Kraken alone first, or both venues in
   parallel? Both is more work up front and answers the venue question sooner;
   Kraken alone gets to a first measured result faster.

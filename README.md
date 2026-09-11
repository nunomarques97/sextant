# Sextant

A multi-strategy, multi-venue quantitative crypto trading system.

> **This software does not place real orders in its current state.**
> There is a walk-forward backtester and there are sixteen pre-registered research
> variants, none of which made money. Nothing places an order: the only network
> calls in the repository fetch public historical archives, no private endpoint is
> wired, and LIVE mode cannot start because the withdrawal-permission probe is not
> wired and preflight treats "cannot be determined" as unsafe.

## Status

**SEXTANT-006 is running: six strategy families, one at a time, each
pre-registered before it is run. Family F1 is done and the answer is no.**

The task asks one question. Is there any crypto strategy family with an edge
robust enough to justify continuing to build the system? Family **F1,
cash-and-carry** - long spot, short the perpetual, same base, equal notional -
was chosen first because it is the family with an actual economic mechanism
behind it: the funding stream a perpetual short receives.

**Nine variants across four cost cells, all 36 trials charged before the engine
ran, and every one of them lost money** over 56 out-of-sample months. The best
lost 5.74% of the account and the worst 91.81%. No variant clears criterion 1:
none beat the 95th percentile of its own exposure-matched null while also
earning a positive net return. **The verdict for F1 is (B).**

**The funding is real and the basis takes it back.** The best-funded variant
received 387.33 EUR of funding on 1,500 of equity, and its price legs gave up
most of it; costs then exceeded what was left. That is the finding, and it is a
statement about the trade rather than about the fees: a cash-and-carry is paid
for carrying basis risk, and here the pay and the risk cancel.

**Two defects made the first execution void, and both are now regression-tested.**
The engine was built without the published funding schedule, so every funding
line was zero; and the delisting haircut was registered on either side and wired
on one, so a delisted short read as a windfall. Both were registered values that
the drift guard verified and no code path read. Every registered parameter is now
perturbed by a test that asserts the output moves. The void run's rows stay in the
trial registry and are counted in full by the Deflated Sharpe Ratio.

- The numbers, and the six criteria:
  [`docs/SPIKE-006-F1-RESULTS.md`](docs/SPIKE-006-F1-RESULTS.md)
- What was going to be tested, written before it was:
  [`docs/PRE-REGISTRATION-006-F1.md`](docs/PRE-REGISTRATION-006-F1.md) and
  [`config/spike-006-f1.yaml`](config/spike-006-f1.yaml)

Commands: `uv run sextant spike-006-f1 verify` checks the code against the
registered specification and reaches no network; `run` executes the grid;
`report` renders it; `depth` acquires the order-book sample **only** when the
registered capacity rule asks for one.

### What came before

**SEXTANT-005 - the question was asked properly, and the answer is no.**

The project set out to find whether a momentum or trend-following effect in crypto
survives realistic transaction costs, honest point-in-time universe construction,
walk-forward out-of-sample evaluation and an honest count of every variant tried.
Sixteen variants were specified and committed **before any data was downloaded**,
then run over 52 out-of-sample months of Binance spot history in EUR.

**Every one of the eighty variant-cells lost money.** The best lost 28.40% of the
account and the worst lost 99.77%. EUR cash returned 0% and beat all eighty.
Bitcoin, bought and held at the same costs, returned +86.31% and beat all eighty by
at least 114 points. Every variant lost money *before a single fee was charged*, so
no cost assumption could be relaxed to rescue it. The Deflated Sharpe Ratio is
0.0000 across the headline panel at an honest trial count of 253.

**The verdict is (B): insufficient evidence, close the trading project.**
[`docs/VERDICT-005.md`](docs/VERDICT-005.md) states it, states what a sceptic
should attack first, and states plainly where the registered verdict rule's letter
and the evidence diverge - the rule says (C), and the document explains why (C)
would be false here rather than quietly taking the softer letter.

**Two findings are worth keeping whatever happens to the project.** Ten held names
bought 2.3 effectively independent bets, because crypto names move together at a
correlation of 0.36 - the same failure the Kraken window showed at 0.804 against
Bitcoin, now measured on two venues over two disjoint windows. And a 52-month
out-of-sample window can only resolve an annualised Sharpe above about 0.94, which
is the honest limit of what any study this size could ever have claimed.

- The verdict and its evidence: [`docs/VERDICT-005.md`](docs/VERDICT-005.md)
- Every number behind it: [`docs/SPIKE-005-RESULTS.md`](docs/SPIKE-005-RESULTS.md)
- What was going to be tested, written before it was:
  [`docs/PRE-REGISTRATION-005.md`](docs/PRE-REGISTRATION-005.md) and
  [`config/spike-005.yaml`](config/spike-005.yaml)
- Which bytes the results were computed from:
  [`docs/binance-archive-checksums.md`](docs/binance-archive-checksums.md)
- Every strategy and parameter set ever evaluated, append-only and hash-chained:
  [`research/trial-registry.jsonl`](research/trial-registry.jsonl)

Commands: `uv run sextant binance all` acquires and ingests the archive;
`uv run sextant spike-005 run` runs the whole grid and `uv run sextant spike-005
report` renders it.

**SEXTANT-004 - the walk-forward backtesting engine, and what no edge looks
like.** There is an engine, a real cost model and a calibrated null. Walk-
forward is its only mode: fitting returns parameters and never an equity curve,
so no object in the system holds in-sample performance. The engine ranks the
point-in-time executable universe at every rebalance and allocates across it,
driven by the `Clock` and `BarRepository` ports, with every euro of accounting in
`Decimal` and the identity `gross - fees - spread - slippage - funding - FX -
delisting = net` asserted exactly rather than to a tolerance.

**The Kraken window was too short to establish edge, and that was quantified
rather than asserted.** Twenty-four monthly out-of-sample observations give an
annualised Sharpe standard error of about 0.71, wider than the entire spread of
the rejection thresholds. That is what prompted SEXTANT-005 to acquire a longer
window on a second venue rather than argue about the first.

- What chance produces, net of costs, with the rejection filter stated in the
  only terms it may be read in:
  [`docs/NULL-BASELINE.md`](docs/NULL-BASELINE.md)
- The gate itself, and the paper phase's obligation to measure the real
  maker/taker fill ratio: [`docs/LIVE-GATES.md`](docs/LIVE-GATES.md)
- The benchmark configuration, committed before any of it ran:
  [`config/benchmarks.yaml`](config/benchmarks.yaml)
- Why numpy, scipy and polars, where each is confined, and one performance claim
  withdrawn after measurement:
  [`docs/adr/0006-research-dependencies-numpy-polars-scipy.md`](docs/adr/0006-research-dependencies-numpy-polars-scipy.md)

## What it is

The system is designed around four constraints that are expensive to retrofit:

- **Exchange-agnostic strategies.** Binance and Kraken are first-class,
  independent adapters. Neither substitutes for the other. No code anywhere
  branches on a venue's name; what an account may do on a venue is the
  intersection of three configured capability layers.
- **Costs are first-class.** Fees, spread, slippage and funding are modelled per
  venue. No strategy is ever evaluated on gross PnL, so which venue suits a
  strategy is an output of backtesting rather than an assumption.
- **Multi-asset and cross-sectional.** There is no ambient "current symbol"
  anywhere. Sample size is bought through breadth of universe, because with a
  handful of assets and a multi-day horizon there are too few independent trades
  per year to tell one strategy from another.
- **Deterministic risk.** A language model may recommend a direction, a
  strategy, a confidence and a regime. It may never determine size, leverage,
  exposure, stop distance or any risk budget. That boundary is enforced by the
  response schemas, not by documentation.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) for environments and dependencies

Install uv on Windows, in PowerShell:

```powershell
winget install --id=astral-sh.uv -e
```

## Install

```powershell
git clone <repository-url> sextant
cd sextant
uv sync
```

## Run

```powershell
uv run sextant status          # resolved configuration and preflight, no side effects
uv run sextant run             # full startup sequence; stops, because there is no engine
uv run sextant --profile paper status

# Research spike (SEXTANT-002). The collect stages reach public endpoints and
# take minutes; measure is offline and regenerates docs/universe-tables.md.
uv run sextant spike collect-binance
uv run sextant spike collect-kraken
uv run sextant spike measure
```

Every command above works with no `.env` file and no credentials in the
environment. The spike reads public market data only.

## Checks

Every one of these must pass before any work is reported as done:

```powershell
uv run ruff check
uv run ruff format --check
uv run mypy --strict src
uv run lint-imports
uv run pytest --cov
```

`lint-imports` is the important one. It enforces the layering contract in
[`.importlinter`](.importlinter): the domain imports nothing else in the system,
the engine never sees an adapter, exchange SDKs are quarantined inside their
adapter, and the two venue adapters cannot import each other.

## The mode ladder

| Mode | What runs | Credentials | Orders |
|---|---|---|---|
| `BACKTEST` | historical data, simulated clock | never required | none |
| `PAPER` | live public data, simulated portfolio | only if the run needs account data or trading | none |
| `LIVE_SPOT` | real orders, spot only | mandatory, and proven unable to withdraw | real |
| `FUTURES` | later, and only if the gates allow it | - | - |

`BACKTEST` is the default. An unset or unrecognised profile resolves to
`BACKTEST` and never to `LIVE`.

Starting in `LIVE` requires all three of: `mode: live` in configuration,
`SEXTANT_ALLOW_LIVE=1` in the process environment, and a passing preflight.
Missing any one is a hard startup failure. Beyond those mechanical gates, live
trading is blocked until every criterion in
[`docs/LIVE-GATES.md`](docs/LIVE-GATES.md) is demonstrated and signed off in
writing.

## Layout

```
config/            base.yaml plus one file per profile
src/sextant/
  domain/          pure types; imports nothing else in the system
  ports/           Protocols: ExchangeClient, BarRepository, Clock, CostModel, LLMAnalyst
  adapters/        everything that touches the outside world
    exchanges/     binance/ and kraken/, independent peers
  engine/          features, regime, strategies, risk, execution (skeletons)
  app/             configuration, credentials, preflight, logging, wiring, CLI
tests/             unit and integration
docs/adr/          architecture decision records
```

## Documentation

- [`CLAUDE.md`](CLAUDE.md) - the rules every session must follow
- [`docs/PHASE-0-FINDINGS.md`](docs/PHASE-0-FINDINGS.md) - architecture proposal, universe proposal, ranked risks
- [`docs/LIVE-GATES.md`](docs/LIVE-GATES.md) - what must be true before live trading is considered
- [`docs/adr/`](docs/adr/) - decisions and the alternatives that were rejected
- [`docs/REMOTE-SETUP.md`](docs/REMOTE-SETUP.md) - the two commands still needed to push and to install uv

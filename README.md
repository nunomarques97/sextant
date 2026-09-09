# Sextant

A multi-strategy, multi-venue quantitative crypto trading system.

> **This software does not place real orders in its current state.**
> There is no strategy, no indicator, no backtester and no network call to any
> exchange in this repository. LIVE mode cannot start: the withdrawal-permission
> probe is not wired, and preflight treats "cannot be determined" as unsafe.

## Status

**SEXTANT-003 - Kraken historical ingestion and a point-in-time listing
calendar.** Kraken's quarterly OHLCVT archives are read into a parquet bar
store, and the diff between consecutive quarters is a listing calendar sourced
from file presence rather than from any price series. A delisting is recorded as
an interval, never as a date, so membership answers *listed*, *not listed* or
*undetermined*. There is still no strategy, no indicator, no backtester, no cost
model and no private endpoint.

- What each venue can supply, revised: Kraken's NO-GO is superseded, with the
  API findings kept intact beside the correction:
  [`docs/DATA-AVAILABILITY.md`](docs/DATA-AVAILABILITY.md)
- The universe tables over the archive window, and the delisting-haircut
  sensitivity at 0%, 20% and 50%:
  [`docs/kraken-archive-tables.md`](docs/kraken-archive-tables.md)
- Why parquet and duckdb, and how they are quarantined:
  [`docs/adr/0005-bar-storage-parquet-and-duckdb.md`](docs/adr/0005-bar-storage-parquet-and-duckdb.md)

Commands: `uv run sextant archive scan | calendar | ingest | measure` builds the
store and the tables from archives in `data/`, reaching no network.
`uv run sextant snapshot-universe` records today's venue membership, idempotent
per UTC day, so future delistings never need reconstructing.

**Two of thirteen quarterly archives are held.** The Google Drive quota blocks
the rest folder-wide and they must be downloaded by hand. Until then the method
is demonstrated and the venue's suitability is not: a two-quarter window is
shorter than the 180-day listing-age rule, so every research universe measured
on it is zero for arithmetic reasons.

**SEXTANT-002 - data-availability spike and the public market-data read
path.** Both venue adapters now implement `health`, `instruments` and
`get_bars` against real public endpoints, read-only. No credential is used, no
private endpoint is called and no order can be placed. There is still no
strategy, no indicator, no backtester and no storage engine.

- What history each venue can actually supply, measured rather than expected:
  [`docs/DATA-AVAILABILITY.md`](docs/DATA-AVAILABILITY.md)
- The month-by-month universe tables behind it:
  [`docs/universe-tables.md`](docs/universe-tables.md)
- The architecture proposal and the running risk register:
  [`docs/PHASE-0-FINDINGS.md`](docs/PHASE-0-FINDINGS.md)

**SEXTANT-001 - repository bootstrap.** Engineering foundation: package layout,
an enforced layering contract, the capability model, layered configuration,
credentials handling, preflight, structured logging and CI.

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

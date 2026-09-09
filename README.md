# Sextant

A multi-strategy, multi-venue quantitative crypto trading system.

> **This software does not place real orders in its current state.**
> There is no strategy, no indicator, no backtester and no network call to any
> exchange in this repository. LIVE mode cannot start: the withdrawal-permission
> probe is not wired, and preflight treats "cannot be determined" as unsafe.

## Status

**SEXTANT-001 - repository bootstrap.** Engineering foundation only: package
layout, an enforced layering contract, the capability model, layered
configuration, credentials handling, preflight, structured logging and CI.

The architecture proposal for review is in
[`docs/PHASE-0-FINDINGS.md`](docs/PHASE-0-FINDINGS.md).

## What it is

The system is designed around four constraints that are expensive to retrofit:

- **Exchange-agnostic strategies.** Binance and Kraken are first-class,
  independent adapters. Neither is the other's fallback. No code anywhere
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
```

Both commands work with no `.env` file and no credentials in the environment.

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

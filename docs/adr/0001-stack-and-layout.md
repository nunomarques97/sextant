# ADR-0001: Stack and package layout

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** project maintainer

## Context

Sextant is a multi-strategy quantitative crypto trading system that must
eventually be able to trade live against two independent venues, conditional on
objective gates. The forces at play:

- **Research velocity dominates.** Most of the work ahead is backtesting,
  statistical evaluation and cost modelling, not throughput engineering. The
  target horizon is multi-day, on a universe of tens of instruments, with a
  small retail account. Nothing about that is latency-sensitive.
- **The ecosystem is not neutral.** Every serious tool for market data
  handling, statistics, walk-forward evaluation and exchange connectivity is
  written for Python. Rebuilding that surface in another language is a cost paid
  on every phase, forever, for no research benefit.
- **The expensive mistakes are architectural, not linguistic.** Look-ahead
  bias, survivorship bias, a strategy that quietly depends on one venue, a
  language model that sets position size. None of these are prevented by a
  compiler.
- **One developer, long gaps between changes.** Conventions decay. Only
  mechanically enforced rules survive.

## Decision

Python 3.12+ managed by uv, with a ports-and-adapters layout and a layering
contract enforced in CI by import-linter.

Runtime dependencies are limited to three, each justified:

| Dependency | Why |
|---|---|
| `pydantic` v2 | Typed boundaries: configuration, LLM I/O schemas, exchange DTOs |
| `pydantic-settings` | The environment-variable layer and typed settings assembly |
| `pyyaml` | Parsing the layered YAML configuration |

Development tooling: ruff (lint and format), mypy in strict mode, pytest with
coverage, import-linter.

Layout:

```
src/sextant/
  domain/     pure types; imports nothing else in the system
  ports/      Protocols: ExchangeClient, BarRepository, Clock, CostModel, LLMAnalyst
  adapters/   everything that touches the outside world
    exchanges/binance/, exchanges/kraken/, storage/, llm/, clocks.py
  engine/     features, regime, strategies, risk, execution
  app/        configuration, credentials, preflight, logging, wiring, CLI
```

## Options considered

### Option A: Python 3.12 + uv + ports and adapters (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Low. One language, one toolchain. |
| Cost | Zero licensing; uv makes environments reproducible. |
| Research fit | Highest. Every statistical and market-data library is here. |
| Type safety | Good under mypy strict, but opt-in and erasable at runtime. |

**Pros:** the whole downstream ecosystem (numpy, pandas/polars, statistical
tooling, exchange clients) is available when each phase needs it; fastest path
from idea to measured result; strict mypy plus import-linter recovers most of
what a compiler would have given us for the failure modes that matter here.

**Cons:** no compile-time guarantee; runtime performance is worse by roughly an
order of magnitude on tight loops; discipline is imposed by tooling rather than
by the language, so the tooling itself must be non-negotiable in CI.

### Option B: .NET (C#)

| Dimension | Assessment |
|---|---|
| Complexity | Moderate. Strong tooling, but a second ecosystem to bridge. |
| Cost | Zero licensing; significantly higher development time here. |
| Research fit | Poor. The quantitative research stack is thin. |
| Type safety | Excellent, compile-time, non-erasable. |

**Pros:** a real compiler; `decimal` is a first-class primitive; genuinely
strong concurrency; the layering contract could be enforced by assembly
references rather than by a linter.

**Cons:** the research libraries are not there. Walk-forward evaluation,
Deflated Sharpe Ratio, cost modelling and universe construction would all be
hand-rolled or bridged back to Python anyway, which reintroduces the second
ecosystem at the worst possible boundary. Exchange client libraries are less
mature. The compile-time safety it buys is real but is aimed at a class of bug
(null dereference, type confusion) that is not what kills a trading system; the
bugs that kill trading systems are look-ahead bias and unmodelled costs, and C#
prevents neither.

### Option C: Rust

**Pros:** the strongest correctness and performance story of the three.

**Cons:** research ergonomics are the worst of the three, and the project is not
latency-bound, so its principal advantage buys nothing we need. Development time
per experiment would rise sharply at the exact phase where iteration speed is
the binding constraint.

## Trade-off analysis

The decisive question is what the system is actually constrained by. It is not
constrained by execution speed: a multi-day horizon on tens of instruments makes
frequency uneconomic before it makes it slow, because fees dominate. It is
constrained by how many honest experiments can be run and how reliably bad ones
are rejected.

That reframes type safety. The value of a compiler here is not that it prevents
crashes; it is that it prevents silent semantic drift. Two thirds of that value
is recoverable in Python, and the layout above spends real effort recovering it:

- mypy strict, with `Any`, `cast()` and blanket ignores banned in `src` and a
  test that greps for them;
- `Decimal`-only monetary types that reject float construction, and timestamps
  that reject naive datetimes, both at construction;
- import-linter contracts that make "strategies are exchange-agnostic" a
  build-breaking fact rather than a code-review habit.

What remains unrecovered is runtime enforcement: a determined caller can still
pass the wrong thing at runtime, and only a test will catch it. That is the
accepted cost.

## Consequences

**Easier:** adding a venue is a package plus a configuration entry; adding a
research dependency is one line and one ADR; every layer above `adapters` is
testable with no network, no keys and no venue.

**Harder:** performance work, if it is ever needed, will require care rather
than being free; the layering rules depend on CI actually running, so a green
build is load-bearing and `lint-imports` must never be made optional.

**To revisit:** if a later phase turns out to need sub-second execution
latency, the execution adapter is the one component that could be rewritten in
another language behind the existing port without touching the engine. That is
a consequence of the layout, not a plan.

## Action items

1. [x] Configure uv, ruff, mypy strict, pytest, import-linter
2. [x] Encode the five layering contracts in `.importlinter` and prove one fails
3. [x] Ban `Any` / `cast()` / `# type: ignore` in `src` with a test
4. [ ] Re-evaluate the dependency table at the start of the data phase

# Sextant

Multi-strategy, multi-venue quantitative crypto trading system. Binance and
Kraken as independent peers. Paper trading is mandatory; live is gated.

## Stack & essentials

Python 3.12+, uv, pydantic v2 + pydantic-settings + pyyaml. Nothing else at
runtime: a new dependency needs an ADR in the phase that introduces it.

Commands (PowerShell, from the repo root):
- Run: `uv run sextant status` / `uv run sextant run [--profile backtest|paper|live]`
- Test: `uv run pytest --cov`
- Typecheck/lint: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src`
- **Layering:** `uv run lint-imports`

## Invariants

Breaking any of these is a defect, not a style choice.

1. **Layering.** `domain` imports nothing else in the system. `engine` and
   `ports` never import `adapters`. `app` is the only place adapters are wired
   to ports. Exchange SDKs live only in `adapters/exchanges`. Binance and Kraken
   may not import each other. Enforced by `.importlinter`.
2. **No venue branching.** There is no `if venue == "..."` anywhere. What an
   account may do is `capabilities()`, the intersection of the venue, account
   and jurisdiction layers. Jurisdiction is configuration data, never code.
   A venue name must never appear in `domain`, `ports` or `engine`.
3. **No ambient symbol.** Every operation takes its `Instrument` or
   `Sequence[Instrument]` explicitly. A call that resolves a symbol from
   configuration or global state is a defect.
4. **Decimal money, UTC time.** Prices, quantities, fees and PnL are `Decimal`;
   floats are rejected at construction. Timestamps are timezone-aware UTC; naive
   datetimes are rejected. Wall-clock reads happen only in `adapters`; everything
   else uses the `Clock` port.
5. **LLM/Risk boundary.** A model may recommend action, strategy, confidence,
   regime and free-form context. It may never determine size, leverage,
   exposure, stop distance or any risk budget. The response schemas cannot
   express those fields and `assert_no_risk_fields` fails the build if one is
   added. Free-form output is never parsed as an instruction.
6. **Live is gated.** LIVE requires `mode: live`, `SEXTANT_ALLOW_LIVE=1` in the
   environment, and a passing preflight. Unset configuration resolves to
   BACKTEST, never LIVE. Beyond the mechanical gates, `docs/LIVE-GATES.md` is
   the contract and only the Sponsor can sign it off.
7. **Secrets.** Keys come from the environment or a git-ignored `.env`. Never in
   YAML, never in a log, never in a commit, never in a report.
8. **Costs.** No strategy is ever evaluated on gross PnL. Fees, spread,
   slippage and funding go through the `CostModel` port.

## Conventions

- No `Any`, no `cast()`, no `# type: ignore` in `src`. If mypy is genuinely
  wrong, document it in the findings instead of suppressing it.
- Point-in-time only: universe rules use information available at the decision
  date. Delisted instruments stay in the candidate set.
- Bars carry an explicit `is_closed` flag. Never consume an open bar.
- Commits are small and coherent, one concern each, present-tense subject.

## Verification

Done means: `ruff check`, `ruff format --check`, `mypy --strict src`,
`lint-imports` and `pytest` all pass, with the output in the report. A report
separates what was confirmed by running something from what could not be
verified. Never report done without evidence.

## Where things live

- Product context & decisions: `docs/PHASE-0-FINDINGS.md`, `docs/adr/`
- Feature specs: implementation briefs from the Product Owner (not in-repo)
- Current state: `README.md` "Status"
- Architecture/technical notes: `docs/adr/`, `.importlinter`
- Live-trading contract: `docs/LIVE-GATES.md`
- Work tracking: n/a
- Project skills/hooks: n/a
- Tooling configured here: uv, ruff, mypy, pytest, import-linter, GitHub Actions
  - catalog: `C:\Users\User\Desktop\PLAYBOOK\TOOLING.md`
- Design reference: n/a - no UI exists. Run `ui-kickoff` before the first screen.
- Operating playbook: `C:\Users\User\Desktop\PLAYBOOK` (read only when a process
  question arises, never routinely)

## Operating model

Sponsor sets direction; Claude (PO) owns product decisions and writes
implementation briefs; Claude Code implements and verifies. Claude Code does not
change product decisions - it reports the problem with evidence and returns the
decision to the PO.

# Sextant

Multi-strategy, multi-venue quantitative crypto trading system. Binance and
Kraken as independent peers. Paper trading is mandatory; live is gated.

## Stack & essentials

Python 3.12+, uv, pydantic v2 + pydantic-settings + pyyaml, httpx, pyarrow and
duckdb, plus numpy, scipy and polars for the research layer. Nothing else at
runtime: a new dependency needs an ADR in the phase that introduces it, and each
of the last three is quarantined by an `.importlinter` contract (ADR 0006).

Commands (PowerShell, from the repo root):
- Run: `uv run sextant status` / `uv run sextant run [--profile backtest|paper|live]`
- Test: `uv run pytest --cov`
- Typecheck/lint: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src`
- **Layering:** `uv run lint-imports`

## Invariants

Breaking any of these is a defect, not a style choice.

1. **Layering.** `domain` imports nothing else in the system. `engine` and
   `ports` never import `adapters`. `app` is the only place adapters are wired
   to ports. Exchange SDKs and `httpx` live only in `adapters/exchanges`;
   `domain`, `ports` and `engine` cannot reach them by any path. Binance and
   Kraken may not import each other. Enforced by `.importlinter`.
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
9. **History is sourced, never assumed.** Every listing window carries a
   `Provenance`: what the venue asserted, what we reconstructed by a stated
   method, and what nothing established. A rule that cannot verify its input
   returns *not evaluable* - never admit, never reject. An adapter that cannot
   answer a point-in-time question raises `PointInTimeUnavailable` naming the
   remedy; it never answers with today's survivors.
10. **A failure is never an empty result.** A refused request raises
   `VenueRequestRejected`, a transient one raises `VenueUnavailable`, and an
   empty answer is empty. Collapsing the three is how a survivorship-biased
   dataset gets built without anybody noticing.
11. **A test may never write to real project data.** Every path a test can
   write to is a parameter pointing at `tmp_path`: the bar store, the listing
   calendar, the committed checksums, the trial registry, the generated reports
   and the configuration directory. A function that writes to a fixed path is a
   function a test cannot safely call, so the path is a parameter with no
   default pointing anywhere real. This is a standing invariant rather than a
   habit because the instance that prompted it - a test that overwrote the
   committed archive checksums - was caught by luck, and the class is broader
   than the instance: a test that appends to the trial registry corrupts the
   one number the Deflated Sharpe Ratio rests on, and nothing would say so.
12. **A cost assumption is never reported as a measurement.** Spread, slippage
   and the maker/taker fill mix are configured values, not observations. The
   types refuse to carry one without the prose that justifies it, every report
   line labels them, and the fill-mix assumption is reported at three settings
   so a conclusion that depends on it is visible.
13. **No result is produced on data it was fitted on.** The engine has one mode,
   walk-forward. Fitting returns parameters and never an equity curve, so there
   is no object anywhere in the system that holds in-sample performance.

## Conventions

- No `Any`, no `cast()`, no `# type: ignore` in `src`. If mypy is genuinely
  wrong, document it in the findings instead of suppressing it.
- Point-in-time only: universe rules use information available at the decision
  date. Delisted instruments stay in the candidate set.
- Money is `Decimal` everywhere in the domain and the accounting. The one
  crossing into float lives in `engine/statistics/boundary.py`; no other module
  may import both an array library and a monetary type, and a test asserts it.
- Bars carry an explicit `is_closed` flag. Never consume an open bar.
- Commits are small and coherent, one concern each, present-tense subject.

## Verification

Done means: `ruff check`, `ruff format --check`, `mypy --strict src`,
`lint-imports` and `pytest` all pass, with the output in the report. A report
separates what was confirmed by running something from what could not be
verified. Never report done without evidence.

## Where things live

- Product context & decisions: `docs/PHASE-0-FINDINGS.md`, `docs/adr/`
- What data each venue can actually supply: `docs/DATA-AVAILABILITY.md`
- Feature specs: implementation briefs from the Product Owner (not in-repo)
- Current state: `README.md` "Status"
- Architecture/technical notes: `docs/adr/`, `.importlinter`
- Live-trading contract: `docs/LIVE-GATES.md`
- The calibrated null and how the rejection filter is used: `docs/NULL-BASELINE.md`
- **The SEXTANT-005 verdict on whether momentum has an edge: `docs/VERDICT-005.md`**
  (answer: no. Every number behind it: `docs/SPIKE-005-RESULTS.md`; what was going
  to be tested, written before it was: `docs/PRE-REGISTRATION-005.md` and
  `config/spike-005.yaml`)
- **SEXTANT-006, one family at a time.** F1, cash-and-carry: verdict (B), nine
  variants, every one losing over 56 out-of-sample months.
  `docs/PRE-REGISTRATION-006-F1.md` and `config/spike-006-f1.yaml` were committed
  before the grid ran; `docs/SPIKE-006-F1-RESULTS.md` is every number;
  `research/spike-006-f1.json` is what it was computed from, with
  `research/spike-006-f1-depth.json` (the capacity sample rule C3 asked for),
  `research/spike-006-f1-spread.json` (the quoted-spread sample rule S1 fired for),
  `research/spike-006-f1-estimator.json` (rule E1's spread estimator, refused by its
  own acceptance test), `research/spike-006-f1-extended.json` (rule M1's mid and thin
  band sample), `research/spike-006-f1-bands.json` (rule B1's re-cut and the band
  occupancy that decided M1) and `research/spike-006-f1-contraction.json` beside it.
  Why the two standard low-frequency spread estimators cannot work on this market:
  `docs/SPREAD-ESTIMATORS.md`. The task verdict across all
  six families will be `docs/VERDICT-006.md`, written once every family is run or
  reported as not reached.
- Which bytes the Binance results were computed from:
  `docs/binance-archive-checksums.md`
- Every strategy and parameter set ever evaluated: `research/trial-registry.jsonl`
  (append-only, hash-chained, committed)
- Pre-registered benchmark configuration: `config/benchmarks.yaml`
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

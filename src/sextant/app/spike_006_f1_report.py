"""Rendering F1's result file as the document a reader checks rather than trusts.

Reads ``research/spike-006-f1.json`` and writes ``docs/SPIKE-006-F1-RESULTS.md``.
It computes nothing that decides anything: the criteria, the verdict letter and the
break-even turnovers were all fixed by :mod:`sextant.app.spike_006_f1_analysis` and
by the registered specification, and this module's whole job is to put them on a
page in an order a reader can follow.

Three rules the layout obeys
----------------------------

**No net figure without its cost breakdown.** Invariant 8, applied to the page and
not only to the type.

**Every assumption is labelled where it appears.** Spread, slippage and the fill mix
are configured values. A page that prints them beside measured returns without
saying which is which is how an assumption becomes a fact.

**The quarterly variant's rebalance count travels with its result.** Section 28.3.
It appears in every table that variant appears in, because a result resting on a
third as many decisions is not comparable to its siblings on the number alone.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from sextant.app.spike_006_f1 import (
    ACCOUNT_EQUITY,
    CADENCE_PAIR,
    EXECUTION_FEE_OF_EQUITY_BPS,
    MARGIN_FRACTION,
    MAX_POSITIONS,
    REGISTERED_VERSION,
    RESEARCH_FEE_OF_EQUITY_BPS,
    RESULTS_PATH,
    SPREAD_BOUND_RATIO,
    THINNER_EVIDENCE_VARIANT,
    execution_sensitivity,
    headline_cell,
    round_trip_fee_bps,
)
from sextant.app.spike_006_f1_analysis import (
    CostLines,
    Rescue,
    Toll,
    analyse,
    assumption_could_be_carrying_the_verdict,
    capacity_report,
    combined_toll,
    currency_corrections,
    depth_sample_is_needed,
    excluding_month,
    rescues,
    spread_acquisition,
    the_currency_line_scaled_with_turnover,
    tolls,
)
from sextant.app.spike_006_f1_bands import BANDS_RESULTS
from sextant.app.spike_006_f1_contraction import CONTRACTION_RESULTS
from sextant.app.spike_006_f1_depth import DEPTH_RESULTS
from sextant.app.spike_006_f1_estimator import ESTIMATOR_RESULTS
from sextant.app.spike_006_f1_extended import EXTENDED_RESULTS
from sextant.app.spike_006_f1_spread import SPREAD_RESULTS
from sextant.domain.time import Timestamp
from sextant.engine.execution.breakeven import BreakEvenUndefined, d2a_holds

REPORT_PATH = Path("docs") / "SPIKE-006-F1-RESULTS.md"

NEWLINE = chr(10)


def render(
    results_path: Path = RESULTS_PATH,
    report_path: Path = REPORT_PATH,
    depth_path: Path = DEPTH_RESULTS,
    contraction_path: Path = CONTRACTION_RESULTS,
    spread_path: Path = SPREAD_RESULTS,
    estimator_path: Path = ESTIMATOR_RESULTS,
    extended_path: Path = EXTENDED_RESULTS,
    bands_path: Path = BANDS_RESULTS,
) -> Path:
    """Read the result file and write the report beside it."""
    with results_path.open(encoding="utf-8") as handle:
        payload = _mapping(json.load(handle))
    depth = (
        _mapping(json.loads(depth_path.read_text(encoding="utf-8")))
        if depth_path.is_file()
        else None
    )
    contraction = (
        _mapping(json.loads(contraction_path.read_text(encoding="utf-8")))
        if contraction_path.is_file()
        else None
    )
    spread = (
        _mapping(json.loads(spread_path.read_text(encoding="utf-8")))
        if spread_path.is_file()
        else None
    )
    estimator = (
        _mapping(json.loads(estimator_path.read_text(encoding="utf-8")))
        if estimator_path.is_file()
        else None
    )
    extended = (
        _mapping(json.loads(extended_path.read_text(encoding="utf-8")))
        if extended_path.is_file()
        else None
    )
    bands = (
        _mapping(json.loads(bands_path.read_text(encoding="utf-8")))
        if bands_path.is_file()
        else None
    )
    # Every derived block is recomputed from the file's own monthly series rather than
    # read back. The runner writes them too, for a reader of the file, but a page that
    # trusted them would silently print an older schema's field names for a run made
    # before a reporting fix - which is exactly what happened once already.
    payload = {**payload, "variants": [row.as_json() for row in analyse(payload)]}
    sections = [
        _preamble(payload),
        _provenance_section(payload),
        _budget_section(payload),
        _window_section(payload),
        _cost_section(payload),
        _headline_section(payload),
        _cadence_section(payload),
        _decomposition_section(payload),
        _toll_section(payload),
        _rescue_section(payload),
        _measured_spread_section(payload, spread),
        _estimator_section(estimator),
        _extended_section(extended),
        _recut_section(bands),
        _regime_section(payload),
        _recent_section(payload),
        _contraction_section(payload, contraction),
        _sign_section(payload),
        _deflation_section(payload),
        _break_even_section(payload),
        _criteria_section(payload),
        _samples_section(payload),
        _depth_section(depth),
        _verdict_section(payload),
        _amendment_twelve_section(payload),
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(NEWLINE.join(sections), encoding="utf-8", newline=NEWLINE)
    print(f"[f1] wrote {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# Typed access to the payload, without Any and without cast
# ---------------------------------------------------------------------------


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"expected a block, found {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, found {type(value).__name__}")
    return value


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _percent(value: object) -> str:
    number = _number(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def _fixed(value: object, places: int = 3) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.{places}f}"


def _money(value: object) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:,.2f}"


def _mark(value: object) -> str:
    """A criterion's answer: met, not met, or an honest 'could not say'."""
    if value is None:
        return "n/a"
    return "yes" if value else "no"


def _variants(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [_mapping(item) for item in _sequence(payload["variants"])]


def _headline_rows(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    cell = headline_cell().label
    rows = [row for row in _variants(payload) if _text(row["cell_id"]) == cell]
    return sorted(rows, key=lambda row: _text(row["variant"]))


def _label(row: Mapping[str, object]) -> str:
    """A variant's name, carrying its rebalance count where section 28.3 requires it."""
    name = _text(row["variant"])
    count = row.get("rebalances")
    if name == THINNER_EVIDENCE_VARIANT and count is not None:
        return f"`{name}` ({count} rebalances)"
    return f"`{name}`"


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------


def _preamble(payload: Mapping[str, object]) -> str:
    verdict = _mapping(payload["verdict"])
    return NEWLINE.join(
        [
            "# SEXTANT-006 family F1: cash-and-carry, spot against the perpetual",
            "",
            "Every number here was produced by one run of the pre-registered grid and is",
            "reproducible from `research/spike-006-f1.json`, which carries the monthly return",
            "series each figure was computed from. The specification is",
            "`docs/PRE-REGISTRATION-006-F1.md` and `config/spike-006-f1.yaml`, both committed",
            "before the numbers existed; the commit that carries them is cited below and its",
            "ancestry is checkable with `git merge-base --is-ancestor`.",
            "",
            f"- pre-registration: **{_text(payload['registered_version'])}**",
            f"- engine version: `{_text(payload['engine_version'])}`",
            f"- run made from commit: `{_text(payload['code_version'])}`",
            f"- everything denominated in **{_text(payload['denomination'])}**",
            f"- **verdict: ({_text(verdict['letter'])})**",
            "",
            _version_note(_text(payload["registered_version"])),
            "",
            "---",
            "",
        ]
    )


def _version_note(ran_against: str) -> str:
    """Why the version cited above is behind the one in the repository.

    Stated in the document rather than reconciled quietly. The run read the
    specification as it stood when it started; two amendments were registered while it
    was still running, both of them rules about counting, authority and acquisition
    rather than about any computed quantity. A reader who checks out the current
    specification and finds a different version number is entitled to know why without
    having to reconstruct it from git.
    """
    if ran_against == REGISTERED_VERSION:
        return ""
    return NEWLINE.join(
        [
            f"> **This run read `{ran_against}`; the specification is now "
            f"`{REGISTERED_VERSION}`.** Amendments 7 and 8 were registered while the grid",
            "> was running and neither changes a computed value: amendment 7 fixes how a void",
            "> execution is counted in the trial registry, and amendment 8 fixes who may",
            "> declare one void and makes the spread sample conditional on rule S1. No",
            "> variant, cell, criterion, threshold, seed count or budget differs between the",
            "> two versions, and every figure below would be identical under either. The",
            "> ordering still holds: both amendments were committed before any figure of this",
            "> run had been read.",
        ]
    )


def _provenance_section(payload: Mapping[str, object]) -> str:
    block = _mapping(payload["registration_provenance"])
    return NEWLINE.join(
        [
            "## 1. Pre-registration ordering",
            "",
            "The drift guard proves the code matches the configuration. It cannot prove the",
            "configuration was not edited alongside the code after a result was seen. Git",
            "ordering can, and these are the two facts that establish it.",
            "",
            "| fact | value |",
            "|---|---|",
            f"| specification | `{_text(block['config_path'])}` |",
            f"| its commit | `{_text(block['config_commit_sha'])}` |",
            f"| committed at | {_text(block['config_committed_at'])} |",
            f"| commit subject | {_text(block['config_commit_subject'])} |",
            f"| clean at run time | {_mark(block.get('config_was_clean_at_run_time'))} |",
            f"| repository head at run time | `{_text(block['head_sha_at_run_time'])}` |",
            "",
            "The run refuses to start if the configuration is untracked or has uncommitted",
            "changes, because in either case no committed SHA describes the bytes the run",
            "read, and citing one anyway would be a false statement about the ordering.",
            "",
            "Run `sextant spike-006-f1 ordering` after this document is committed to print",
            "the ancestry check between the configuration's commit and the results' commit.",
            "",
            "That command reports the **newest** commit touching the configuration, which since",
            "amendment 8 has been a later one than the commit whose bytes this run read: every",
            "amendment from 8 onward was registered after this family's figures existed, says so",
            "in its own section, and governs an acquisition or a later family rather than an F1",
            "figure. So the command answers NO and that is the expected answer. The property",
            "that matters is the one the table above records: the SHA captured at run time, which",
            "describes the bytes the run actually read and is an ancestor of the results commit.",
            "Anything in the configuration newer than that SHA could not have reached this run.",
            "",
        ]
    )


def _budget_section(payload: Mapping[str, object]) -> str:
    block = _mapping(payload["budget"])
    registry = _mapping(block["from_the_committed_registry"])
    post_hoc = _sequence(registry["post_hoc"])
    lines = [
        "## 2. The trial budget, and what it refused",
        "",
        "The budget is the exact grid rather than a ceiling with a reserve behind it: a",
        "reserve is what gets spent after a result is seen. The runner charges before the",
        "engine runs, so a trial outside the declared allowance is refused before it can",
        "produce a number anybody has seen.",
        "",
        "| | |",
        "|---|---|",
        f"| declared | {_text(block['declared'])} trials |",
        f"| variants | {len(_sequence(block['variants']))} |",
        f"| cost cells | {len(_sequence(block['cost_cells']))} |",
        f"| charged this run | {_text(block['charged_this_run'])} |",
        f"| recorded in the committed registry | {_text(registry['registered'])} |",
        f"| remaining | {_text(registry['remaining'])} |",
        f"| null and benchmark constructs, not charged | {_text(registry['nulls_not_charged'])} |",
        "",
    ]
    if post_hoc:
        lines.extend(
            [
                "**Post-hoc trials outside the registered budget:**",
                "",
                *[f"- `{_text(item)}`" for item in post_hoc],
                "",
            ]
        )
    else:
        lines.extend(
            [
                "No post-hoc trial was recorded against this family. The nine variants and the",
                "four cells are what ran, and nothing else.",
                "",
            ]
        )
    return NEWLINE.join(lines)


def _window_section(payload: Mapping[str, object]) -> str:
    window = _mapping(payload["window"])
    span = _mapping(window["out_of_sample"])
    universe = _mapping(payload["universe"])
    pairs = [
        int(_text(_mapping(item)["pairs"]))
        for item in _sequence(universe["pairs_at_each_rebalance"])
    ]
    ordered = sorted(pairs)
    median = ordered[len(ordered) // 2] if ordered else 0
    return NEWLINE.join(
        [
            "## 3. The window and the universe",
            "",
            "| | |",
            "|---|---|",
            f"| first usable month | {_text(window['first_usable_month'])} |",
            f"| last usable month end | {_text(window['last_usable_month_end'])} |",
            f"| usable months | {_text(window['usable_months'])} |",
            f"| out-of-sample | {_text(span['start'])[:10]} to {_text(span['end'])[:10]} |",
            f"| scored months | {_text(window['scored_months'])} |",
            f"| walk-forward folds | {len(_sequence(_mapping(window['folds'])['folds']))} |",
            f"| carry pairs per rebalance | {min(pairs) if pairs else 0} to "
            f"{max(pairs) if pairs else 0}, median {median} |",
            "",
            "The window was resolved by the rule in section 7 from listing metadata and FX",
            "availability alone. No return was consulted to choose it.",
            "",
        ]
    )


def _cost_section(payload: Mapping[str, object]) -> str:
    cells = [_mapping(item) for item in _sequence(payload["cells"])]
    lines = [
        "## 4. What each cell charges",
        "",
        "Fees are the venue's published schedules and differ by leg. Spread, slippage and",
        "the maker/taker fill mix are **assumptions**, not measurements, and are labelled as",
        "such wherever a figure computed under them appears. They are charged on **both**",
        "legs, so a carry round trip costs twice what a single-leg strategy pays.",
        "",
        f"**The spread figure is a registered UPPER BOUND, about {_ratio_text()} times the",
        "measured deep-band half-spread of 0.53 bps.** Amendment 12, rule A12.7: it is never",
        "an estimate, a conservative estimate or a calibration, because a reader who takes it",
        "for any of those takes it for something that was measured. It is the number every",
        "criterion in this document is evaluated against, and section 7.5 is what it is a",
        "bound on.",
        "",
        "| cell | spot maker/taker | futures maker/taker | fill mix | spread & slippage | role |",
        "|---|---|---|---|---|---|",
    ]
    for cell in cells:
        role = (
            "headline, every criterion judged here"
            if cell.get("is_headline")
            else ("runs the nulls" if cell.get("runs_nulls") else "reported only")
        )
        multiplier = _text(cell["spread_and_slippage_multiplier"])
        lines.append(
            f"| `{_text(cell['label'])}` "
            f"| {_text(cell['spot_maker_bps'])} / {_text(cell['spot_taker_bps'])} bps "
            f"| {_text(cell['futures_maker_bps'])} / {_text(cell['futures_taker_bps'])} bps "
            f"| {_text(cell['fill_mix'])} "
            f"| assumption x{multiplier} "
            f"| {role} |"
        )
    lines.extend(
        [
            "",
            "One full-book round trip, in basis points of one leg's notional:",
            "",
            f"- research schedule (`{headline_cell().label}`): "
            f"**{round_trip_fee_bps(headline_cell()):.2f} bps**, which is "
            f"**{RESEARCH_FEE_OF_EQUITY_BPS:.2f} bps of equity** at a margin fraction of "
            f"{MARGIN_FRACTION}",
            f"- execution schedule (`{execution_sensitivity().label}`): "
            f"**{round_trip_fee_bps(execution_sensitivity()):.2f} bps**, which is "
            f"**{EXECUTION_FEE_OF_EQUITY_BPS:.2f} bps of equity**",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _headline_section(payload: Mapping[str, object]) -> str:
    rows = _headline_rows(payload)
    lines = [
        f"## 5. The nine variants in the headline cell (`{headline_cell().label}`)",
        "",
        "Net of every cost line. The Sharpe's standard error is beside it because over a",
        "window this length it is usually the most important number on the page.",
        "",
        "| variant | net return | Sharpe | std err | 95th pct of its null | months | rebalances |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {_label(row)} "
            f"| {_percent(row['net_return'])} "
            f"| {_fixed(row['sharpe_annualised'])} "
            f"| {_fixed(row['sharpe_standard_error'])} "
            f"| {_fixed(row['exposure_matched_null_p95'])} "
            f"| {_text(row['observations'])} "
            f"| {_text(row.get('rebalances'))} |"
        )
    lines.extend(
        [
            "",
            "**The null is the exposure-matched one**: as many carry pairs as the variant held",
            "at that rebalance, drawn at random, leaving the same fraction idle. It inherits the",
            "variant's exposure path exactly and differs from it only in which pairs it holds,",
            "which is what makes it the null that answers whether choosing these assets did",
            "anything.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _cadence_section(payload: Mapping[str, object]) -> str:
    cadence = _mapping(payload["cadence"])
    counts = _mapping(cadence["rebalance_counts"])
    monthly, quarterly = CADENCE_PAIR
    rows = {_text(row["variant"]): row for row in _headline_rows(payload)}
    pair = [rows.get(monthly), rows.get(quarterly)]
    lines = [
        "## 6. Cadence: the one-factor comparison",
        "",
        "Amendment 6 registered `carry-rank90-10-quarterly` as `carry-rank90-10` with the",
        "rebalance frequency changed and nothing else: same signal, same lookback, same",
        "position count, same universe, same cost cells. Whatever separates these two rows",
        "is cadence.",
        "",
        "| | net return | Sharpe | rebalances | idle months before first rebalance |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in pair:
        if row is None:
            continue
        lines.append(
            f"| `{_text(row['variant'])}` "
            f"| {_percent(row['net_return'])} "
            f"| {_fixed(row['sharpe_annualised'])} "
            f"| {_text(row.get('rebalances'))} "
            f"| {_text(row.get('idle_months_before_first_rebalance', 0))} |"
        )
    quarterly_count = _number(counts.get(quarterly))
    monthly_count = _number(counts.get(monthly))
    lines.extend(
        [
            "",
            f"The quarterly variant consulted its signal **{_text(counts.get(quarterly))}** times",
            f"against **{_text(counts.get(monthly))}** for its monthly twin over the same scored",
            "window. Its evidence rests on that many decisions and not on the month count, which",
            "is why the figure appears beside its result everywhere it is printed.",
            "",
        ]
    )
    if quarterly_count and monthly_count:
        lines.extend(
            [
                "Section 28.3 anticipated roughly 23 against roughly 70. Those were counts over",
                "the 69-month usable window; the scored window is shorter, so the realised",
                f"counts are {int(quarterly_count)} against {int(monthly_count)}. The ratio is",
                "the one that was registered; the absolute figures are smaller because twelve",
                "months are spent fitting and are never scored.",
                "",
            ]
        )
    lines.extend(
        [
            "Between rebalances the quarterly variant's book can only shrink, never grow: a",
            "sourced delisting still forces an exit, a pair leaving the carry universe is",
            "dropped and never re-bought, and the freed capital sits idle rather than",
            "concentrating the rest. Section 28.6.",
            "",
            "### Cadence and turnover are not monotonically related",
            "",
            "Amendment 6 was argued for on the assumption that a slower cadence trades less.",
            "**That assumption is wrong as stated, and the correction is recorded here rather",
            "than dropped.** On a controlled fixture whose liquidity ranking rotates, the",
            "quarterly variant turned over *more* than its monthly twin, not less: 9132.49",
            "against 9074.48 of notional traded.",
            "",
            "The mechanism is drift. Skipping two decision instants does not remove the trades",
            "those instants would have made, it defers them. The held weights drift from the",
            "target for three months instead of one, and the single correction at the end of",
            "the quarter can exceed the two corrections that were skipped. Whether it does",
            "depends on how fast the ranking moves relative to the cadence, which is a",
            "property of the market rather than of the schedule.",
            "",
            "This makes the quarterly cell worth **more** than the amendment claimed, not less.",
            "A monotonic relationship could have been reasoned about from the fee schedule",
            "alone, and the variant would have been an expensive way to confirm arithmetic.",
            "A non-monotonic one cannot be: where the fee-optimal cadence sits is a",
            "measurement, and this is the cell that measures it. The regression test asserts",
            "that cadence changes turnover and deliberately does not assert a direction.",
            "",
        ]
    )
    lines.extend(_realised_turnover(payload))
    return NEWLINE.join(lines)


def _realised_turnover(payload: Mapping[str, object]) -> list[str]:
    """What the two cadences actually traded, which is the point of measuring it.

    The fixture and the archive disagree in *direction*, and both are printed. That
    disagreement is the finding: whether a slower cadence trades less depends on how
    fast the ranking moves relative to the cadence, and neither fixture nor archive
    settles it in general.
    """
    monthly, quarterly = CADENCE_PAIR
    runs = {
        _text(row["construct"]): row
        for row in _sequence(payload["deterministic"])
        if isinstance(row, dict)
        and _text(row.get("cell_id")) == headline_cell().label
        and _text(row.get("kind")) == "variant"
    }
    if monthly not in runs or quarterly not in runs:
        return []
    fast, slow = _mapping(runs[monthly]), _mapping(runs[quarterly])
    fast_turnover = Decimal(_text(fast["turnover"]))
    slow_turnover = Decimal(_text(slow["turnover"]))
    fast_costs = _mapping(fast["costs"])
    slow_costs = _mapping(slow["costs"])
    if fast_turnover == 0:
        return []
    share = (slow_turnover / fast_turnover * 100).quantize(Decimal("0.1"))
    return [
        "On the archive, in this window, it went the other way from the fixture:",
        "",
        "| | rebalances | notional traded | fees | spread | slippage | total cost |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| `{monthly}` | {_text(fast.get('rebalances'))} | {_money(fast_turnover)} "
        f"| {_money(fast_costs['fees'])} | {_money(fast_costs['spread'])} "
        f"| {_money(fast_costs['slippage'])} | {_money(fast_costs['total'])} |",
        f"| `{quarterly}` | {_text(slow.get('rebalances'))} | {_money(slow_turnover)} "
        f"| {_money(slow_costs['fees'])} | {_money(slow_costs['spread'])} "
        f"| {_money(slow_costs['slippage'])} | {_money(slow_costs['total'])} |",
        "",
        f"The quarterly variant traded **{share}%** of its monthly twin's notional here, so",
        "on this universe the slower cadence did trade less. On the rotating fixture it",
        "traded more. Both are real and they do not contradict each other: the direction",
        "depends on how fast the ranking moves relative to the cadence, which is precisely",
        "why it had to be measured rather than assumed.",
        "",
    ]


def _decomposition_section(payload: Mapping[str, object]) -> str:
    rows = _headline_rows(payload)
    lines = [
        "## 7. Where the return came from",
        "",
        "Four ways of cutting the same book. **Combined** is the variant. **Timing** holds the",
        "whole carry universe at the variant's own invested fraction: its timing, none of its",
        "selection. **Selection** holds the variant's own pairs scaled to full investment: its",
        "selection, none of its timing. **Funding** is the realised settlement stream, which is",
        "the return rather than a cost line and is never blended into fees.",
        "",
        "| variant | combined | timing | selection | price legs | + funding | - charges | = net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        block = _mapping(row["decomposition"])
        lines.append(
            f"| {_label(row)} "
            f"| {_percent(block['combined_net_return'])} "
            f"| {_percent(block['timing_effect_net_return'])} "
            f"| {_percent(block['selection_effect_net_return'])} "
            f"| {_money(block['price_pnl'])} "
            f"| {_money(block['funding_received_net'])} "
            f"| {_money(block['charges_excluding_funding'])} "
            f"| {_money(block['net_pnl'])} |"
        )
    lines.extend(
        [
            "",
            "The first three columns are returns on the account. The last four are amounts in",
            "account currency on the registered starting equity and they add up as written:",
            "price legs plus funding less charges is net. **Charges exclude funding**, because",
            "the ledger books a receipt as a negative cost and its own total is therefore",
            "already net of the carry; subtracting that total from a carry that also contains",
            "the funding would count the funding twice.",
            "",
            "**This is the family's whole result in one table.** The funding stream is real and",
            "in most variants it is large. The price legs of a hedged pair should hold only the",
            "basis, and they give most of it back. What the two leave is then smaller than the",
            "cost of trading it.",
            "",
        ]
    )
    return NEWLINE.join(lines)


#: The multipliers the spread-and-slippage sensitivity is reported at. One of them is
#: zero, which is not a plausible world and is there as a bound: a run that loses with
#: both lines deleted cannot be rescued by any spread measurement whatsoever.
SENSITIVITY_MULTIPLIERS = (Decimal(1), Decimal("0.5"), Decimal("0.25"), Decimal(0))


def _toll_section(payload: Mapping[str, object]) -> str:
    """What the charges are made of, and how much of that is an assumption.

    Section 7 shows the charges as one number. That number is where the family dies,
    so its composition decides what the verdict claims: a toll made of published fees
    is a statement about this account, and a toll made of spread and slippage is a
    statement resting on two configured figures.
    """
    cell = headline_cell().label
    items = tolls(payload, cell=cell)
    if not items:
        return ""
    combined = combined_toll(items)
    parts: tuple[tuple[str, Callable[[CostLines], Decimal]], ...] = (
        ("exchange fees", lambda line: line.fees),
        ("spread (assumed)", lambda line: line.spread),
        ("slippage (assumed)", lambda line: line.slippage),
        ("FX conversion", lambda line: line.conversion),
        ("delisting haircut", lambda line: line.delisting),
    )
    # The variant whose carry cleared the most before any charge: the most favourable
    # case in the grid, and therefore the one whose toll decides the most.
    best = max(items, key=lambda item: item.lines.gross_before_costs)
    lines = [
        "### 7.1 What the toll is made of",
        "",
        "The charges column is where this family dies, so here it is itemised. **Funding is",
        "not in these totals**: a receipt is booked as a negative cost line and the funding",
        "stream sits on the return side of the identity above.",
        "",
        f"The second pair of columns is `{best.variant}`, whose carry cleared the most",
        f"before any charge: {_money(best.lines.gross_before_costs)} EUR of price move plus",
        "funding. It is the most favourable case in the grid.",
        "",
        "| part | all nine, EUR | share | the best case, EUR | share |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, pick in parts:
        whole, one = pick(combined), pick(best.lines)
        lines.append(
            f"| {name} "
            f"| {_money(whole)} | {_share(whole, combined.charges)} "
            f"| {_money(one)} | {_share(one, best.lines.charges)} |"
        )
    lines.extend(
        [
            f"| **total charged** | **{_money(combined.charges)}** | 100% "
            f"| **{_money(best.lines.charges)}** | 100% |",
            "",
            "**Two of the five are assumptions, and together they are "
            f"{_share(combined.assumed, combined.charges)} of the toll.** Spread and slippage",
            "are configured values under invariant 12 rather than measurements. Published",
            f"exchange fees are {_share(combined.fees, combined.charges)} of the toll, and the",
            f"conversion leg is {_share(combined.conversion, combined.charges)}, larger than",
            "the fees themselves.",
            "",
            "**So this verdict does not say the venue's fee schedule ate the premium.** It says",
            "the total cost of trading it did, and the majority of that total is two numbers",
            "this project assumed rather than measured. That belongs in the verdict in those",
            "words, and it is the honest reading of what F1 establishes.",
            "",
            "Per variant, in the headline cell:",
            "",
            "| variant | fees | spread | slippage | FX | delisting | total | assumed share |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in sorted(items, key=lambda entry: entry.variant):
        line = item.lines
        lines.append(
            f"| {_variant_label(item.variant)} "
            f"| {_money(line.fees)} | {_money(line.spread)} | {_money(line.slippage)} "
            f"| {_money(line.conversion)} | {_money(line.delisting)} "
            f"| {_money(line.charges)} | {_share(line.assumed, line.charges)} |"
        )
    lines.extend(["", *_sensitivity(items)])
    return NEWLINE.join(lines)


#: Counts up to the size of this grid, as words. A table is figures and a sentence is
#: prose, and "2 of the nine" reads as a typo rather than as a count.
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _count(value: int) -> str:
    """A small count as a word, falling back to the figure where there is no word."""
    return _WORDS[value] if 0 <= value < len(_WORDS) else str(value)


def _sensitivity(items: Sequence[Toll]) -> list[str]:
    """The spread-and-slippage sensitivity, labelled as a sensitivity throughout."""
    lines = [
        "### 7.2 What a cheaper assumption would have produced",
        "",
        "**A sensitivity, not a result.** Every variant below is still costed at the",
        "registered assumption everywhere else in this document, and no criterion is",
        "recomputed here. The columns scale the spread and slippage lines by a multiplier and",
        "leave everything else exactly as it ran.",
        "",
        "The arithmetic is exact in the charges and approximate in the path. No registered",
        "variant reads a cost when it decides, so a cheaper world would have held the same",
        "pairs in the same weights; it would also have compounded a larger equity into every",
        "later position, so the true figure at a lower assumption is a little better than",
        "this. The multiplier at which each variant breaks even is in the last column.",
        "",
        "| variant | as run | half | a quarter | none at all | breaks even at |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in sorted(items, key=lambda entry: entry.variant):
        line = item.lines
        cells = "".join(f"| {_money(line.net_at(k))} " for k in SENSITIVITY_MULTIPLIERS)
        flip = line.flip_multiplier
        flips = "-" if flip is None else f"{flip:.3f}"
        lines.append(f"| {_variant_label(item.variant)} {cells}| {flips} |")
    flipping = [
        item
        for item in items
        if item.lines.flip_multiplier is not None and item.lines.flip_multiplier > 0
    ]
    stuck = len(items) - len(flipping)
    lines.extend(
        [
            "",
            "**A multiplier at or below zero means the run loses with spread and slippage",
            f"deleted entirely.** {_count(stuck).capitalize()} of the {_count(len(items))} are in",
            "that position, and for them no spread measurement of any kind could change the",
            "sign: their carry does not cover the exchange fees, the conversion leg and the",
            "delisting haircut on their own.",
            "",
        ]
    )
    if flipping:
        named = ", ".join(f"`{item.variant}`" for item in flipping)
        worst = max(
            item.lines.flip_multiplier
            for item in flipping
            if item.lines.flip_multiplier is not None
        )
        amounts = [_money(item.lines.net_at(Decimal(0))) for item in flipping]
        earned = (
            " and ".join([", ".join(amounts[:-1]), amounts[-1]]) if len(amounts) > 1 else amounts[0]
        )
        lines.extend(
            [
                f"**{_count(len(flipping)).capitalize()} of the {_count(len(items))} do flip: "
                f"{named}.** They turn positive only when",
                f"spread and slippage fall to about {worst:.0%} of the assumption, a reduction of",
                f"roughly {1 - worst:.0%}. Even with both lines deleted they earn {earned} EUR on",
                "1,500 of equity over fifty-six months, which is low single-digit per cent in",
                "total rather than a year. That is a sign change and not an edge: neither comes",
                "near criterion 1, whose bar is the 95th percentile of the variant's own null.",
                "",
            ]
        )
    lines.extend(
        [
            "### 7.3 Rule S1, and the circularity it had to break",
            "",
            "**Amendment 8's version of rule S1** acquired the spread sample if some variant",
            "earned a positive net return at research fees. No variant did, so it was false.",
            "**But the assumed spread is inside the charges that produced that negative net**,",
            "so the assumption helped prevent the measurement that could have corrected it.",
            "",
            "**Amendment 9 replaces the test with the counterfactual** and the bar is criterion",
            "1 itself: set the assumed cost to zero, re-evaluate, and acquire when some variant",
            "would then clear a positive net return *and* a Sharpe above its own null. A sign",
            "change alone is not a rescue.",
            "",
        ]
    )
    return lines


def _variant_label(name: str) -> str:
    """A variant's name, with the rebalance count where section 28.3 requires it."""
    if name == THINNER_EVIDENCE_VARIANT:
        return f"`{name}` (18 rebalances)"
    return f"`{name}`"


def _share(part: Decimal, whole: Decimal) -> str:
    """One part as a percentage of a total, or a dash over nothing."""
    if whole == 0:
        return "-"
    return f"{part / whole * 100:.1f}%"


def _deflation_at_zero(items: Sequence[Rescue]) -> list[str]:
    """Criterion 2 asked of the counterfactual, for the variants that cleared criterion 1."""
    rows: list[str] = []
    for item in items:
        block = item.deflated_at_zero
        if block is None:
            continue
        rows.append(
            f"| {_variant_label(item.variant)} "
            f"| {_fixed(block.observed_sharpe_per_period, 4)} "
            f"| {_fixed(block.expected_maximum_sharpe_per_period, 4)} "
            f"| {_fixed(block.deflated_sharpe_ratio, 4)} "
            f"| {_mark(item.survives_deflation_at_zero)} |"
        )
    return rows


def _rescue_section(payload: Mapping[str, object]) -> str:
    """Rule S1 as amendment 9 states it, computed rather than asserted.

    Its own section rather than a paragraph, because the rule's answer and the answer
    its author expected differ, and a reader has to be able to see both with the
    figures that separate them.
    """
    cell = headline_cell().label
    items = rescues(payload, cell=cell)
    if not items:
        return ""
    fires = assumption_could_be_carrying_the_verdict(items)
    clearing = [item for item in items if item.clears_criterion_one]
    lines = [
        "### 7.4 Rule S1: could the assumed cost be carrying the verdict?",
        "",
        "Amendment 9, registered **after** these figures had been read and labelled as such",
        "everywhere it appears. It governs an acquisition and can move no number in this",
        "document: every variant stays costed at the registered assumption in every cell,",
        "under invariant 12.",
        "",
        "Spread and slippage are set to zero and the run re-evaluated. The bar is criterion 1",
        "applied to that counterfactual: a positive net return **and** a Sharpe above the 95th",
        "percentile of the variant's own exposure-matched null.",
        "",
        "| variant | net as run | net at zero | Sharpe at zero | its null's p95 | clears |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in items:
        lines.append(
            f"| {_variant_label(item.variant)} "
            f"| {_percent(item.net_return)} "
            f"| {_percent(item.net_return_at_zero)} "
            f"| {_fixed(item.sharpe_at_zero)} "
            f"| {_fixed(item.null_p95)} "
            f"| {_mark(item.clears_criterion_one)} |"
        )
    lines.extend(
        [
            "",
            f"**Rule S1 as written: {_yes(fires)}.** "
            + (
                f"{_count(len(clearing)).capitalize()} of the {_count(len(items))} variants "
                "clear criterion 1 once the assumed cost is removed, so by the registered rule "
                "the spread sample is to be acquired."
                if fires
                else "No variant clears criterion 1 once the assumed cost is removed, so no "
                "spread measurement could change this family's answer. The sample is not "
                "acquired, and that is a measured statement rather than a procedural one."
            ),
            "",
        ]
    )
    bars = sorted(item.null_p95 for item in items if item.null_p95 is not None)
    if fires and bars:
        lines.extend(
            [
                "#### The rule fires, and its author expected it not to",
                "",
                "**This is reported rather than resolved, because the two readings disagree.**",
                "The rule was registered on the reasoning that a variant landing at a small",
                "positive figure is *rescued by rounding rather than by the assumption*, and",
                "would fail to clear its own null. On this data it does clear it.",
                "",
                "**The reason is that the null bar is negative.** The exposure-matched null is",
                "itself losing over this window, at a 95th-percentile Sharpe between",
                f"{_fixed(bars[0])} and {_fixed(bars[-1])}, so a counterfactual Sharpe near",
                "zero clears it comfortably. Criterion 1 is a",
                "comparison against chance in this market, not an absolute bar, and at zero",
                "assumed cost these two variants beat chance while earning almost nothing.",
                "",
                "In absolute terms the two are still tiny: they earn 18.70 and 39.48 EUR on",
                "1,500 of equity across 56 months. Whether that is *the assumption carrying the",
                "verdict* or *rounding* is precisely what the two readings disagree about.",
                "",
                "**The verdict letter does not move either way, and that is computed rather",
                "than argued.** Criterion 1 is one of six. Asking criterion 2 of the same",
                "counterfactual:",
                "",
                "| variant | Sharpe at zero, per month | expected max under the trial count "
                "| DSR | clears 0.95 |",
                "|---|---:|---:|---:|---|",
                *_deflation_at_zero(clearing),
                "",
                "Both columns are per month, which is the unit the deflation works in: an",
                "annualised 0.09 is a monthly 0.026. Both deflated Sharpes are **0.0000**",
                "against a threshold of 0.95, because the expected maximum under the registry's",
                "honest trial count is about seventy times the counterfactual's own Sharpe.",
                "**So even with spread and slippage",
                "deleted entirely, no variant clears all six criteria and F1's verdict stays",
                "(B).** What the acquisition could buy is a measured cost line beside a variant",
                "that beats a losing null while earning 1.5 per cent over four and a half years.",
                "",
                "#### How it was settled, and what now applies",
                "",
                "**The rule as written was honoured: the sample was acquired.** The Product",
                "Owner's instruction was to follow the rule as written rather than to amend a",
                "rule to avoid the action it requires after seeing that it requires it. The",
                "drafting defect is recorded in pre-registration section 32.1 and attributed",
                "to its author rather than edited away.",
                "",
                "**From F2 the floor is anchored to zero, and it would have left this",
                "unfired.** Amendment 11 settles rule S1: the counterfactual Sharpe must exceed",
                "**zero** by one standard error of its own estimate *and* clear its null. Both",
                "clauses. Amendment 10's first form of the floor was anchored to the null, and",
                "on these figures the two disagree:",
                "",
                "| variant | Sharpe at zero | 1 standard error | its null's p95 "
                "| null-anchored bar | clears zero by 1 s.e. |",
                "|---|---:|---:|---:|---:|---|",
                *_anchor_table(clearing),
                "",
                "**Adding one standard error to a bar at -1.26 gives -0.78, which no",
                "profitable strategy needs to reach.** That is why the anchor moved: the",
                "exposure-matched null is itself losing over this window, so a bar stated",
                "relative to it is a bar below zero. Both forms stay in the record and both stay",
                "computable, because a superseded bar nobody can compute is one a later reader",
                "has to take on trust.",
                "",
                "**The choice could not save anybody any work, which is the only reason it is",
                "safe to have made after the figures existed.** The sample the zero-anchored",
                "floor would have prevented had already been acquired, measured and reported in",
                "section 7.5 before the anchor was settled.",
                "",
            ]
        )
    lines.extend(
        [
            "**The counterfactual is modelled, not measured.** The removed cost is added back in",
            "equal instalments across the scored months, on each month's opening equity along",
            "the realised path. That is exact in the total and in the sign of the net return,",
            "and approximate in the volatility, because the real charge follows each month's",
            "turnover. From F2 the runner records per-month assumed costs and the same test is",
            "computed exactly. The conclusion here does not rest on the approximation: the two",
            "clearing Sharpes sit more than a full point above their null bars.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _anchor_table(items: Sequence[Rescue]) -> list[str]:
    """The two floors side by side, on the variants that made the rule fire."""
    rows: list[str] = []
    for item in items:
        bar = (
            None
            if item.null_p95 is None or item.standard_error_at_zero is None
            else item.null_p95 + item.standard_error_at_zero
        )
        rows.append(
            f"| {_variant_label(item.variant)} "
            f"| {_fixed(item.sharpe_at_zero)} "
            f"| {_fixed(item.standard_error_at_zero)} "
            f"| {_fixed(item.null_p95)} "
            f"| {_fixed(bar)} "
            f"| {_mark(item.clears_zero_by_a_standard_error)} |"
        )
    return rows


def _estimator_section(estimator: Mapping[str, object] | None) -> str:
    """Rule E1: the estimated spread that was registered, computed, and refused.

    Empty when no calibration exists. When one does it reports all three clauses whatever
    they say, because a rule whose result is only printed when it passes is not a rule.
    """
    if estimator is None:
        return ""
    clauses = _mapping(estimator["clauses"])
    ordering = _mapping(clauses["ordering"])
    magnitude = _mapping(clauses["magnitude"])
    positivity = _mapping(clauses["positivity"])
    registered = _mapping(positivity["registered_estimator"])
    comparison = _mapping(positivity["comparison_estimator"])
    adopted = bool(estimator["all_three_hold"])
    lines = [
        "### 7.6 Rule E1: an estimated spread, and why it is not adopted",
        "",
        "**The band structure is the defect section 7.5 found, and a better constant cannot",
        "repair it.** The bands are cut on quote turnover, spread does not respond to",
        "turnover, and two orders of magnitude of measured spread sit inside a single band.",
        "So amendment 11 registered a replacement: an estimate per instrument and per period,",
        "from daily high, low and close, which the archive holds for every instrument across",
        "the whole window.",
        "",
        "**The estimator, the comparison and the acceptance test were committed before this",
        "was computed.** Abdi and Ranaldo (2017) is the registered estimator; Corwin and",
        "Schultz (2012) is computed beside it and is never substituted for it. Three clauses,",
        "all of which must hold.",
        "",
        "| clause | statistic | bar | holds |",
        "|---|---:|---:|---|",
        f"| ordering, Spearman's rho across the six symbols "
        f"| {_fixed(_number(ordering['statistic']))} "
        f"| at least {_text(ordering['floor'])} | {_mark(bool(ordering['holds']))} |",
        f"| magnitude, median absolute log2 of estimated over measured "
        f"| {_fixed(_number(magnitude['statistic']))} | at most {_text(magnitude['ceiling'])} "
        f"| {_mark(bool(magnitude['holds']))} |",
        f"| positivity, share of instrument-periods estimated positive "
        f"| {_share_of(registered['share_strictly_positive'])} "
        f"| at least {_percent_of(positivity['floor'])} | {_mark(bool(positivity['holds']))} |",
        "",
        f"**Rule E1: {'ADOPTED' if adopted else 'NOT ADOPTED'}.** "
        + (
            "All three clauses hold, so the estimate becomes the default spread cost from F2."
            if adopted
            else "Two of the three clauses fail. The banded **assumption** is kept, labelled "
            "an assumption exactly as it is now. No third estimator is tried, no clause is "
            "relaxed, and the comparison estimator is not promoted."
        ),
        "",
        "#### What the estimator actually produced",
        "",
        "| symbol | chosen as | measured | estimated | estimated / measured | comparison |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for entry in _sequence(_mapping(estimator["calibration"])["per_symbol"]):
        row = _mapping(entry)
        lines.append(
            f"| `{_text(row['symbol'])}` "
            f"| {_text(row['chosen_as'])} "
            f"| {_bps(_optional_text(row['measured_median_quoted_spread_bps']))} "
            f"| {_bps(_optional_text(row['estimated_median_bps']))} "
            f"| {_multiple(row['estimated_over_measured'])} "
            f"| {_bps(_optional_text(row['comparison_estimated_median_bps']))} |"
        )
    lines.extend(
        [
            "",
            "**The ordering clause holds and the magnitude clause fails by a factor of 85.**",
            "That combination is itself the finding: the estimator ranks these six symbols",
            "almost correctly - one adjacent swap - while being one to three orders of",
            "magnitude out on the level. It carries information about which instrument is",
            "wider, and none about how wide.",
            "",
            "#### Why, and why a longer window is not the answer",
            "",
            "**Each two-day term estimates the squared spread plus noise whose scale is the",
            "daily variance.** So whether the estimator can resolve anything is a ratio, not a",
            "judgement. Resolving a squared spread at one standard error needs the term count",
            "to reach the square of that ratio:",
            "",
            "| symbol | scatter of the two-day terms | squared measured spread "
            "| two-day pairs needed |",
            "|---|---:|---:|---:|",
        ]
    )
    for entry in _sequence(_mapping(estimator["resolution"])["per_symbol"]):
        row = _mapping(entry)
        lines.append(
            f"| `{_text(row['symbol'])}` "
            f"| {_scientific(row['two_day_term_standard_deviation'])} "
            f"| {_scientific(row['measured_squared_proportional_spread'])} "
            f"| {_scientific(row['two_day_pairs_needed_to_resolve_it'])} |"
        )
    lines.extend(
        [
            "",
            "**`BTCUSDT` would need about two thousand million million two-day pairs**, which",
            "is some five trillion years of daily bars. A thirty-day window against a",
            "thirty-year one is not the difference, and no choice of period rescues this.",
            "",
            "**The implementation is not the explanation, and that is tested rather than",
            "asserted.** `tests/unit/test_spread_estimator.py` simulates a quote-driven market",
            "with a known spread and recovers it from **both** estimators at 100 and at 50",
            "basis points, then shows both break down as the spread falls relative to the",
            "volatility. These estimators were validated on equities, where a spread of tens of",
            "basis points sits against daily moves of one or two per cent. A perpetual quoting",
            "0.036 bps against four per cent daily volatility is four orders of magnitude",
            "outside that regime.",
            "",
            "#### What the comparison estimator did, since it is not promoted",
            "",
            f"Corwin-Schultz ranks the six identically, at rho "
            f"{_fixed(_number(ordering['comparison_statistic']))}, and returns a strictly",
            f"positive figure in {_share_of(comparison['share_strictly_positive'])} of",
            "instrument-periods, which would clear the positivity clause the registered",
            "estimator fails. **It is still not adopted, and nothing was left on the table:**",
            "its levels are 48 to 2,622 times the measurement, worse on the magnitude clause",
            "than the estimator that was registered. Its higher positivity share is not a",
            "virtue here. It floors less often because it is systematically large, and a cost",
            "model that always charges about 100 bps is not better than one that sometimes",
            "charges nothing.",
            "",
            f"**Of the instrument-periods asked for, "
            f"{_count_of(registered['too_few_pairs_to_estimate'])} had too few usable pairs**",
            f"to estimate at all, against {_count_of(registered['instrument_periods_asked_for'])}",
            "that could be estimated, across",
            f"{_count_of(registered['instruments_scanned'])} stored perpetuals and",
            "sixty-nine month ends. That is a listing-history artefact rather than a data",
            "defect: most of these instruments did not exist for most of the window. Section",
            "33.4 registered the substitution for that case and it is the banded assumption,",
            "labelled as one.",
            "",
            "#### What this leaves open, and it is the Product Owner's to settle",
            "",
            "**Amendment 11 superseded amendment 10's measured default in favour of an",
            "estimator that has now failed its own test.** Amendment 10 had made the measured",
            "deep-band figure the default from F2; section 33.4 replaced that on the reasoning",
            "that six symbol-days cannot be a default for a five-year window, and registered an",
            "estimator instead. The estimator is refused. So the registered consequence stands",
            "- the assumption is kept - and it is worth saying plainly what the assumption is:",
            "**10 bps of half-spread on the deep band, against a measured 0.53.**",
            "",
            "**Three options exist and the Developer chooses none of them.** Keep the",
            "assumption, as rule E1's failure clause says. Revert to amendment 10's measured",
            "deep-band figure, which rule E1 superseded but did not disprove. Or run F2 at both",
            "and report the pair. All three are computed and none is substituted; the choice",
            "belongs to the Product Owner before F2 runs.",
            "",
            "#### What none of this touches",
            "",
            "**No F1 figure.** Every cell is costed at the registered assumption and stays",
            "that way. Section 7.4 shows F1's verdict survives deleting the assumed cost",
            "entirely, which is a stronger statement than any estimate of it could be.",
            "",
            "**Slippage.** It is an intraday quantity, daily bars cannot calibrate it, and it",
            "remains an assumption at its registered figures.",
            "",
            "**The spot leg, and the mid and thin bands.** The calibration is against perpetual",
            "quotes in the deep band, because that is what section 12 registered.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _multiple(value: object) -> str:
    """A ratio, as a multiple. `-` when it does not exist."""
    number = _number(_optional_text(value))
    return "-" if number is None else f"{number:,.1f}x"


def _scientific(value: object) -> str:
    """A figure that spans fifteen orders of magnitude, in the only notation that fits."""
    text = _optional_text(value)
    if text is None:
        return "-"
    return f"{float(text):.3e}"


def _share_of(value: object) -> str:
    """A share held as a fraction, printed as a percentage."""
    number = _number(_optional_text(value))
    return "-" if number is None else f"{number * 100:.1f}%"


def _percent_of(value: object) -> str:
    """A registered fraction, printed as a percentage, without arithmetic on text."""
    number = _number(_text(value))
    return "-" if number is None else f"{number * 100:.0f}%"


def _count_of(value: object) -> str:
    """An integer with thousands separators."""
    text = _optional_text(value)
    return "-" if text is None else f"{int(float(text)):,}"


def _optional_text(value: object) -> str | None:
    """The text of a value that may be null in the file."""
    return None if value is None else _text(value)


def _extended_section(extended: Mapping[str, object] | None) -> str:
    """Rule M1: the two bands rule S1 never reached, and the one it could not reach either.

    Empty when no sample exists. When one does, the coverage table comes before the
    measurement, because a band median printed above the fact that only one of its four
    symbols published is a number that will be quoted without the fact.
    """
    if extended is None:
        return ""
    coverage = _mapping(extended["coverage_by_band"])
    assumed = _mapping(extended["assumed_spread_bps_by_band"])
    measured = _mapping(extended["measured_median_bps_by_band"])
    lines = [
        "### 7.7 Rule M1: the mid and thin bands, measured and not measured",
        "",
        "**All six of rule S1's symbols are in the *deep* band**, because at 2023-05-15 every",
        "member of the carry universe cleared the deep band's floor. So the mid and thin",
        "defaults were extrapolation from a band they are not in.",
        "",
        "**Occupancy decided this had to run, and it was computed before anything was",
        "downloaded.** At the cost model's own floors, across all 69 rebalance instants: the",
        "mid band is occupied at 40 of them and the thin band at 9. Neither is empty in",
        "practice, so neither default is unused, so both are worth measuring.",
        "",
        f"**{_count_of(extended['symbol_days_measured'])} symbol-days, "
        f"{_text(extended['megabytes_fetched'])} MB, "
        f"{_count_of(extended['verified_against_the_publisher'])} verified against the",
        "publisher's own SHA-256.** Same protocol as rule S1: the same archive tree, the same",
        "two windows, the same streaming reduction, the same six days.",
        "",
        "| band | selected at | symbols | published every day | symbol-days "
        "| measured as registered |",
        "|---|---|---:|---:|---:|---|",
    ]
    for entry in _sequence(extended["selections"]):
        selection = _mapping(entry)
        band = _text(selection["band"])
        row = _mapping(coverage[band])
        lines.append(
            f"| {band} "
            f"| {_text(selection['selected_at'])} "
            f"| {_count_of(row['symbols_selected'])} "
            f"| {_count_of(row['symbols_that_published_every_registered_day'])} "
            f"| {_count_of(row['symbol_days_measured'])} of "
            f"{_count_of(row['symbol_days_requested'])} "
            f"| {_mark(bool(row['meets_the_registered_requirement']))} |"
        )
    lines.extend(
        [
            "",
            "**The thin band could not be measured on the registered days, and is reported as",
            "not measured rather than averaged.** Its earliest instant with four members is",
            "2025-12-01, two years after the registered day set, and three of its four symbols",
            "had not listed on those days. One of four published all six. Rule M1's own clause",
            "says an unpublished symbol-day is never replaced, so no day and no symbol was",
            "substituted and the band median is **null**.",
            "",
            "**That is a finding about when the thin band is populated, not only about a",
            "gap.** No rebalance instant before 2025-12-01 holds even four thin-band members,",
            "which is what the selection rule searched for and is computed rather than",
            "inferred. The band is occupied at 9 of 69 instants and thinly enough at most of",
            "them that four names cannot be found. A cost model still needs its thin figure",
            "for those instants, and measuring it needs a day set chosen for when the band is",
            "populated rather than for when rule S1 happened to sample.",
            "**Which day set that should be is the Product Owner's to settle**, because",
            "choosing one after seeing that the registered set failed is the move this",
            "apparatus exists to prevent.",
            "",
            "#### What the mid band measures",
            "",
            "| | mid band |",
            "|---|---:|",
            f"| assumed half-spread, per leg, a registered upper bound | "
            f"{_text(assumed.get('mid'))} bps |",
            f"| measured quoted spread, median across its four symbols | "
            f"{_bps(_optional_text(measured.get('mid')))} |",
            f"| the comparable half of it | {_bps(_halved(_optional_text(measured.get('mid'))))} |",
            "",
            "**The same ratio as the deep band, on a different band.** The deep assumption is",
            "about 19 times its measurement and the mid assumption about 21 times its own. Two",
            "bands measured independently, two orders of magnitude apart in spread, and the",
            "assumption is out by the same factor on both.",
            "",
            "#### The dispersion, which is the finding rather than the average",
            "",
            "| symbol | band | median quoted spread |",
            "|---|---|---:|",
        ]
    )
    bands_of: dict[str, list[str]] = {}
    for entry in _sequence(extended["selections"]):
        selection = _mapping(entry)
        for item in _sequence(selection["symbols"]):
            symbol = _text(_mapping(item)["symbol"])
            bands_of.setdefault(symbol, []).append(_text(selection["band"]))
    per_symbol = _mapping(extended["measured_median_bps_by_symbol"])
    for symbol in sorted(per_symbol):
        lines.append(
            f"| `{symbol}` "
            f"| {' and '.join(bands_of.get(symbol, ['-']))} "
            f"| {_bps(_optional_text(per_symbol[symbol]))} |"
        )
    doubled = _sequence(extended["symbols_selected_into_two_bands"])
    lines.extend(
        [
            "",
            "**Inside the mid band alone the measured spreads run from 1.77 to 7.24 basis",
            "points, a factor of four.** The deep band spanned two orders of magnitude. A",
            "single figure per band does not represent either of them, which is what rule B1",
            "is for.",
            "",
            *(
                [
                    f"**`{_text(doubled[0])}` appears in two bands, and that is not an error.**",
                    "A band is a property of an instrument at an instant: it was mid in",
                    "2023-07 and thin in 2025-12. It contributes to both bands' figures rather",
                    "than to whichever was written last, and it is measured once.",
                    "",
                ]
                if doubled
                else []
            ),
        ]
    )
    return NEWLINE.join(lines)


def _recut_section(bands: Mapping[str, object] | None) -> str:
    """Rule B1: what the bands should be cut on, decided by a test rather than by taste."""
    if bands is None:
        return ""
    recut = _mapping(bands["recut"])
    chosen = recut["chosen_quantity"]
    lines = [
        "### 7.8 Rule B1: the bands are cut on the wrong variable",
        "",
        "**The bands are cut on quote turnover and spread does not respond to turnover.** That",
        "is what sections 7.5 and 7.7 measured, and a band whose members do not share a spread",
        "is not a band; it is an average with a label. So four candidate quantities were ranked",
        "against the measured half-spread across every sampled symbol, by Spearman's rho with a",
        "permutation p-value at a registered seed, and the cut goes to the strongest that",
        f"clears {_text(recut['registered_level'])}.",
        "",
        "Eleven symbols: rule S1's six and rule M1's five.",
        "",
        "| candidate quantity | rho | p | clears |",
        "|---|---:|---:|---|",
    ]
    for entry in _sequence(recut["candidates"]):
        item = _mapping(entry)
        lines.append(
            f"| {_text(item['quantity'])} "
            f"| {_fixed(_number(_optional_text(item['spearman_rho'])), 3)} "
            f"| {_fixed(_number(_optional_text(item['permutation_p_value'])), 4)} "
            f"| {_mark(bool(item['clears_the_registered_level']))} |"
        )
    lines.extend(
        [
            "",
            f"**The cut goes to {_text(chosen) if chosen is not None else 'nothing'}.** "
            + (
                "Three of the four clear, and the one the bands are cut on today is the "
                "weakest of them: quote turnover reaches rho -0.65 at p 0.038, against "
                "relative tick size at rho 0.900 and p 0.0008. Turnover is not uninformative "
                "about spread; it is simply not what determines it."
                if chosen is not None
                else "No candidate cleared the registered level, so nothing is re-cut and that "
                "is the finding rather than an omission."
            ),
            "",
            "**Why a tick should be the answer is not a mystery.** A spread cannot be narrower",
            "than one price increment, and on the deepest instruments it is exactly that:",
            "`BTCUSDT`'s derived tick is 0.0352 basis points against a measured quoted spread",
            "of 0.0357, and `ETHUSDT`'s is 0.0535 against 0.0541. Both are sitting on the",
            "venue's own floor, where the tick *is* the spread, and turnover predicts spread",
            "only to the extent that it predicts which instruments sit there.",
            "",
            "#### The weakest part of this result, stated before anybody has to find it",
            "",
            "**The tick is derived rather than looked up, and the derivation can only",
            "overestimate.** It is the greatest common divisor of the published high, low and",
            "close over thirty days, which is a *multiple* of the true increment and equals it",
            "only when the sample happens to use every one. Thirty days of three prices is",
            "often not enough.",
            "",
            f"**And it demonstrably overestimated on {_tick_offender_count(recut)} of the",
            "eleven symbols**, because their derived tick is *wider than their own measured",
            f"quoted spread*, which is impossible: {_tick_offenders(recut)}.",
            "",
            "**What that costs and what it does not.** A rank correlation survives an",
            "overestimate that is monotone in the true tick, so the ordering result stands as",
            "an ordering result. The **edge value** does not: a band boundary quoted in tick",
            "units cannot be taken from a divisor inferred from prices, and needs the venue's",
            "own instrument metadata. **Acquiring that metadata is the obvious next step and it",
            "is not taken here**, because the tick a cost model needs is the tick at the",
            "decision instant and the venue publishes today's, which is a point-in-time problem",
            "of exactly the kind invariant 9 exists for.",
            "",
            "**A sceptic should attack this first.** The candidate that won is the one whose",
            "measurement is weakest, and the two symbols where the derivation is demonstrably",
            "clean are also the two whose spread is most obviously tick-bound, which is the",
            "shape of a result that could be partly circular.",
            "",
            f"**The evidence supports "
            f"{_count_of(recut['bands_supported_by_the_evidence'])} bands, not three.** Eleven",
            "symbols could have supported three at the registered minimum of three per band,",
            "so this time the ceiling is the separation rather than the sample: the two bands'",
            "median half-spreads are 0.68 and 1.49 basis points, a factor of 2.19 against a",
            "required 2. A third cut does not separate and is not made.",
            "",
            "| band | members |",
            "|---|---|",
        ]
    )
    for index, group in enumerate(_sequence(recut["band_members"])):
        members = ", ".join(f"`{_text(item)}`" for item in _sequence(group))
        label = "tighter" if index == 0 else "wider"
        lines.append(f"| {label} | {members} |")
    lines.extend(
        [
            "",
            "**The new cut crosses the old one, which is the whole point.** `SPELLUSDT` was a",
            "mid-band instrument on turnover and lands in the tighter band on tick size;",
            "`API3USDT` was deep and lands in the wider one. A partition cut on the wrong",
            "variable does not merely lose precision, it puts instruments on the wrong side.",
            "",
            "**This changes no F1 figure.** F1 ran and was judged on the turnover-cut bands.",
            "Rule B1 applies from F2, and the thin band's missing measurement is a reason to",
            "settle the day set before it does.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _tick_offender_count(recut: Mapping[str, object]) -> int:
    """How many symbols the tick derivation demonstrably overestimated."""
    block = _mapping(recut["tick_derivation_is_an_upper_bound"])
    return len(_sequence(block["symbols_whose_derived_tick_exceeds_their_measured_spread"]))


def _tick_offenders(recut: Mapping[str, object]) -> str:
    """The symbols whose derived tick is impossible, named in the prose that says so."""
    block = _mapping(recut["tick_derivation_is_an_upper_bound"])
    names = [
        f"`{_text(item)}`"
        for item in _sequence(block["symbols_whose_derived_tick_exceeds_their_measured_spread"])
    ]
    if not names:
        return "none of them"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _measured_spread_section(
    payload: Mapping[str, object], spread: Mapping[str, object] | None
) -> str:
    """What rule S1 bought: a measured spread, beside the assumption it does not replace.

    Empty when no sample exists. When one does, the first thing it says is what it
    cannot do, because a measured figure printed next to a computed one invites the
    reading that the computed one has moved.
    """
    if spread is None:
        return ""
    bands = _mapping(spread["measured_median_bps_by_band"])
    assumed = _mapping(spread["assumed_spread_bps_by_band"])
    slippage = _mapping(spread["assumed_slippage_bps_by_band"])
    lines = [
        "### 7.5 The measured spread, beside the assumption",
        "",
        "**This changes no figure in this document and is not permitted to.** Every variant",
        "is costed at the registered assumption in every cell, under invariant 12, and F1's",
        "verdict was settled before this was measured. Section 7.4 shows the verdict does not",
        "move even with the whole assumed cost deleted, which is a stronger statement than any",
        "measurement of it could make.",
        "",
        f"Rule S1 fired, so section 12's sample was acquired: "
        f"**{_text(spread['symbol_days_measured'])} symbol-days** of top-of-book quotes, "
        f"{_text(spread['megabytes_fetched'])} MB, "
        f"{_text(spread['verified_against_the_publisher'])} of them verified against the",
        "publisher's own SHA-256.",
        "",
        "| symbol | chosen as | days | quotes | median quoted spread | range across days "
        "| 00:00-00:05 UTC |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    picked = {
        _text(_mapping(item)["symbol"]): _mapping(item) for item in _sequence(spread["symbols"])
    }
    per_symbol: dict[str, list[Mapping[str, object]]] = {}
    for entry in _sequence(spread["per_symbol_day"]):
        item = _mapping(entry)
        per_symbol.setdefault(_text(item["symbol"]), []).append(item)
    for symbol in sorted(per_symbol):
        rows = per_symbol[symbol]
        whole = [_mapping(row["whole_day"]) for row in rows]
        opening = [_mapping(row["opening_window"]) for row in rows]
        quotes = sum(int(_text(block["quotes"])) for block in whole)
        chosen = picked.get(symbol, {})
        lines.append(
            f"| `{symbol}` "
            f"| {_text(chosen.get('chosen_as'))} "
            f"| {len(rows)} "
            f"| {quotes:,} "
            f"| {_bps(_median_text([block['median_bps'] for block in whole]))} "
            f"| {_range_text([block['median_bps'] for block in whole])} "
            f"| {_bps(_median_text([block['median_bps'] for block in opening]))} |"
        )
    deep = _mapping(bands["deep"]) if "deep" in bands else None
    lines.extend(
        [
            "",
            "Every figure is the **quoted** spread in basis points of the midpoint,",
            "time-weighted within the day and then taken as the median across days. No quote",
            "in the sample was crossed or locked.",
            "",
            "#### What it says against the assumption",
            "",
            "The configured figure is a **half-spread charged per leg**, so the comparable",
            "measured quantity is half the quoted spread.",
            "",
            "| | deep band |",
            "|---|---:|",
            f"| assumed half-spread, per leg | {_text(assumed.get('deep'))} bps |",
            f"| measured quoted spread, median across the six | "
            f"{_bps(None if deep is None else _text(deep['whole_day']))} |",
            f"| the comparable half of it | "
            f"{_bps(_halved(None if deep is None else _text(deep['whole_day'])))} |",
            f"| assumed slippage, per leg, measured by nothing here | "
            f"{_text(slippage.get('deep'))} bps |",
            "",
            "**The assumption is roughly twenty times the measured half-spread on this band,**",
            "and for the two deepest symbols it is several hundred times: `BTCUSDT` quotes at",
            "0.03 basis points and `ETHUSDT` at 0.05. The assumption was chosen to be",
            "conservative rather than representative, and on this evidence it is very",
            "conservative indeed for liquid perpetuals.",
            "",
            "**The band is the more interesting finding.** All six sampled symbols fall in the",
            "cost model's *deep* band, because every carry-universe member at 2023-05-15 turns",
            "over more than that band's floor. Inside that one band the measured spread ranges",
            "from 0.03 to 4.72 basis points, two orders of magnitude. **A single figure per band",
            "cannot represent that**, and the band boundaries are cut on turnover rather than on",
            "anything the spread responds to.",
            "",
            "#### Three things it cannot say",
            "",
            "**Nothing about slippage.** The quoted spread is what rested at the top of the",
            "book; slippage is what an order does to it. The slippage assumption is untouched",
            "by this measurement and remains an assumption.",
            "",
            "**Nothing about the mid and thin bands.** The sample reaches only the deep band,",
            "so those assumptions stay assumptions and are labelled as such wherever they",
            "appear.",
            "",
            "**Nothing about the spot leg.** These are perpetual quotes. A cash-and-carry needs",
            "both legs to fill, and the spot book is measured by nothing here.",
            "",
            "**The opening window is empty on 2023-05-16 for every symbol**, because that is the",
            "tree's first published day and its coverage begins at about 11:50 UTC. Twelve of",
            "the twenty-four hours are covered and the five minutes after midnight are not, so",
            "that day's opening figure is null rather than zero.",
            "",
            "**What becomes of this measurement is settled in section 7.6 and not here.**",
            "Amendment 10 made it the default cost for the deep band from F2. Amendment 11",
            "superseded that with an estimator, on the reasoning that six symbol-days cannot be",
            "a default for a five-year window, and rule E1 then refused the estimator. So the",
            "registered consequence is that the assumption is kept, and what the default should",
            "be from F2 is an open question with three computed options. Section 7.6.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _bps(value: str | None) -> str:
    """A spread in basis points, at the precision the measurement supports."""
    number = _number(value)
    return "-" if number is None else f"{number:.4f} bps"


def _range_text(values: Sequence[object]) -> str:
    """The lowest and highest of a set of daily figures, so instability is visible."""
    numbers = sorted(
        Decimal(_text(item)) for item in values if item is not None and _text(item) != ""
    )
    if not numbers:
        return "-"
    if len(numbers) == 1:
        return f"{numbers[0]:.4f}"
    return f"{numbers[0]:.4f} to {numbers[-1]:.4f}"


def _median_text(values: Sequence[object]) -> str | None:
    """The median of the figures that exist, as text, or None if none do."""
    numbers = sorted(
        Decimal(_text(item)) for item in values if item is not None and _text(item) != ""
    )
    if not numbers:
        return None
    middle = len(numbers) // 2
    if len(numbers) % 2 == 1:
        return str(numbers[middle])
    return str((numbers[middle - 1] + numbers[middle]) / Decimal(2))


def _halved(value: str | None) -> str | None:
    """Half of a quoted spread: the quantity the per-leg assumption is comparable to."""
    return None if value is None else str(Decimal(value) / Decimal(2))


def _regime_section(payload: Mapping[str, object]) -> str:
    counts = _mapping(payload["regime_month_counts_scored"])
    conclusive = _sequence(payload["conclusive_regimes"])
    rows = _headline_rows(payload)
    labels = sorted(_text(item) for item in conclusive)
    lines = [
        "## 8. Regime stability",
        "",
        "Months are counted over the **scored** window. Counting them over the whole usable",
        "window would let the twelve fitting months decide whether a regime is conclusive for",
        "a result they were never part of. A month's regime is the label that was knowable",
        "when its position was opened, not when it closed.",
        "",
        "| regime | scored months | conclusive |",
        "|---|---:|---|",
    ]
    for label in sorted(counts):
        lines.append(f"| {label} | {_text(counts[label])} | {'yes' if label in labels else 'no'} |")
    if labels:
        lines.extend(
            [
                "",
                "| variant | " + " | ".join(labels) + " |",
                "|---|" + "---:|" * len(labels),
            ]
        )
        for row in rows:
            regimes = _mapping(row["regime_returns"])
            cells = []
            for label in labels:
                entry = regimes.get(label)
                if isinstance(entry, dict):
                    block = _mapping(entry)
                    cells.append(f"{_percent(block['net_return'])} ({_text(block['months'])}m)")
                else:
                    cells.append("-")
            lines.append(f"| {_label(row)} | " + " | ".join(cells) + " |")
    lines.append("")
    return NEWLINE.join(lines)


def _recent_section(payload: Mapping[str, object]) -> str:
    window = _mapping(payload["window"])
    rows = _headline_rows(payload)
    lines = [
        f"## 9. The most recent {_text(window['recent_window_months'])} scored months",
        "",
        "Criterion 6, added by amendment 1. It asks whether the edge is confined to the early",
        "part of the window. The null it is measured against is **the same draws** as the",
        "full-window null, restricted to the same trailing months: not a fresh set of draws,",
        "which would be a different experiment.",
        "",
        "| variant | months | net return | Sharpe | 95th pct, same draws | holds |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        recent = _mapping(row["recent_window"])
        criteria = _mapping(row["criteria"])
        lines.append(
            f"| {_label(row)} "
            f"| {_text(recent['months'])} "
            f"| {_percent(recent['net_return'])} "
            f"| {_fixed(recent['sharpe_annualised'])} "
            f"| {_fixed(recent['null_p95_same_draws'])} "
            f"| {_mark(criteria.get('6_recent_window_holds'))} |"
        )
    lines.append("")
    return NEWLINE.join(lines)


def _significant(value: object) -> str:
    """A figure at a width that shows it, whatever its magnitude.

    The composition table puts a contract age in days beside a funding rate of about
    four hundredths of a basis point. A fixed number of decimals prints one of them as
    zero, and a difference printed as zero beside an interval that excludes zero reads
    as a contradiction rather than as a rounding.
    """
    number = _number(value)
    if number is None:
        return "-"
    if number == 0:
        return "0"
    if abs(number) >= 1:
        return f"{number:,.2f}"
    return f"{number:.3g}"


def _contraction_section(
    payload: Mapping[str, object], contraction: Mapping[str, object] | None
) -> str:
    """Amendment 26.1: the largest contraction, by composition and not only by size.

    Placed here because the month it concerns falls inside the recent window criterion
    6 is judged on, which is the only place a single month's composition could change
    a reading. It changes nothing: criterion 6 is judged on the full series exactly as
    registered, and these figures sit beside it.
    """
    if contraction is None:
        return ""
    at = _text(contraction["at"])[:10]
    lines = [
        "### 9.1 The largest contraction, and the headline without it",
        "",
        f"The rebalance with the biggest month-on-month fall in pair count is **{at}**: "
        f"{_text(contraction['pairs_before'])} pairs before, "
        f"{_text(contraction['pairs_after'])} after, "
        f"{_text(contraction['pairs_excluded'])} excluded. Amendment 26.1 requires that a",
        "contraction be described by *composition* rather than by size, because a smaller",
        "slice that is representative and a smaller slice that is not are different facts.",
        "",
        "| attribute | admitted | excluded | difference | 95% interval | systematic |",
        "|---|---:|---:|---:|---|---|",
    ]
    differences = _mapping(contraction["differences"])
    for name in sorted(differences):
        item = _mapping(differences[name])
        excludes = bool(item["interval_excludes_zero"])
        lines.append(
            f"| {name} ({_text(item['statistic'])}) "
            f"| {_significant(item['left'])} "
            f"| {_significant(item['right'])} "
            f"| {_significant(item['difference'])} "
            f"| {_significant(item['interval_low'])} to {_significant(item['interval_high'])} "
            f"| {_yes(excludes)} |"
        )
    lines.extend(
        [
            "",
            _text(contraction["difference_convention"]).capitalize(),
            "",
            f"**{_text(contraction['verdict'])}**",
            "",
            _text(contraction["mechanism"]),
            "",
            f"*{_text(contraction['mechanism_status'])}*",
            "",
        ]
    )
    rows = excluding_month(
        payload, Timestamp.parse(_text(contraction["at"])), cell=headline_cell().label
    )
    if rows:
        lines.extend(
            [
                "Every variant's headline in the headline cell, with that month and without it.",
                "Both figures are compounded from the same monthly series, so the comparison is",
                "like for like:",
                "",
                "| variant | net return | without | difference | Sharpe | without |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            name = (
                f"`{row.variant}`"
                if row.variant != THINNER_EVIDENCE_VARIANT
                else f"`{row.variant}` (quarterly)"
            )
            lines.append(
                f"| {name} "
                f"| {_percent(row.net_return)} "
                f"| {_percent(row.net_return_without)} "
                f"| {_percent(row.difference)} "
                f"| {_fixed(row.sharpe)} "
                f"| {_fixed(row.sharpe_without)} |"
            )
        lines.extend(
            [
                "",
                f"**What this decides: {_text(contraction['what_this_decides'])[:1].lower()}"
                f"{_text(contraction['what_this_decides'])[1:]}**",
                "",
                "The verdict is the one computed on the full series. Dropping a month because",
                "its composition is inconvenient is the move this apparatus exists to prevent.",
                "The column is here so a reader can see how much of the headline that month",
                "carried, which on these figures is under one percentage point everywhere.",
                "",
            ]
        )
    return NEWLINE.join(lines)


def _sign_section(payload: Mapping[str, object]) -> str:
    cells = [_text(_mapping(item)["label"]) for item in _sequence(payload["cells"])]
    by_variant: dict[str, dict[str, str]] = {}
    for row in _variants(payload):
        by_variant.setdefault(_text(row["variant"]), {})[_text(row["cell_id"])] = _percent(
            row["net_return"]
        )
    lines = [
        "## 10. Sign stability across the cost regimes",
        "",
        "Criterion 5. A variant whose net return changes sign between two cost assumptions is",
        "a variant whose result is the assumption rather than the market.",
        "",
        "| variant | " + " | ".join(f"`{cell}`" for cell in cells) + " |",
        "|---|" + "---:|" * len(cells),
    ]
    for variant in sorted(by_variant):
        values = by_variant[variant]
        lines.append(
            f"| `{variant}` | " + " | ".join(values.get(cell, "-") for cell in cells) + " |"
        )
    lines.append("")
    return NEWLINE.join(lines)


def _deflation_section(payload: Mapping[str, object]) -> str:
    trials = _mapping(payload["trials"])
    rows = _headline_rows(payload)
    lines = [
        "## 11. Deflation, and what the search cost",
        "",
        "The Deflated Sharpe Ratio asks what the best of this many searches would have",
        "produced by chance alone. The trial count is the honest one from the append-only",
        "hash-chained registry, nulls included, because a null draw is a search whether or",
        "not anybody hoped it would win.",
        "",
        f"- trials including null constructs: **{_text(trials['including_nulls'])}**",
        f"- trials excluding null constructs: **{_text(trials['excluding_nulls'])}**",
        "",
        "| variant | Sharpe | expected max under the null | PSR | DSR | clears 0.95 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        block = row.get("deflated_sharpe")
        criteria = _mapping(row["criteria"])
        if not isinstance(block, dict):
            lines.append(f"| {_label(row)} | - | - | - | - | n/a |")
            continue
        dsr = _mapping(block)
        lines.append(
            f"| {_label(row)} "
            f"| {_fixed(row['sharpe_annualised'])} "
            f"| {_fixed(dsr['expected_maximum_sharpe_per_period'], 4)} "
            f"| {_fixed(dsr['probabilistic_sharpe_ratio'], 4)} "
            f"| {_fixed(dsr['deflated_sharpe_ratio'], 4)} "
            f"| {_mark(criteria.get('2_survives_deflation'))} |"
        )
    lines.extend(
        [
            "",
            "The expected maximum is in per-observation units and is the benchmark the observed",
            "Sharpe is deflated against. Its inputs - the trial count, the measured variance of",
            "the null's Sharpe distribution, the observation count, the skewness and the",
            "kurtosis - are all in the result file beside the output, because a report showing",
            "only the output invites exactly the argument this project exists to avoid.",
            "",
        ]
    )
    return NEWLINE.join(lines)


#: Amendment 5's illustrative carry, in basis points a year, and the two break-evens
#: it implies. Registered as an illustration before anything ran, and reported beside
#: the realised figures because that is the comparison the amendment invites.
ILLUSTRATIVE_CARRY_BPS = Decimal(400)


def _turnover_table(payload: Mapping[str, object]) -> list[str]:
    """Realised turnover for every variant, against both sets of break-evens.

    The arithmetic does not close without it: a break-even is a threshold and says
    nothing at all until a realised figure is put beside it.
    """
    rows = _headline_rows(payload)
    research = ILLUSTRATIVE_CARRY_BPS / RESEARCH_FEE_OF_EQUITY_BPS
    execution = ILLUSTRATIVE_CARRY_BPS / EXECUTION_FEE_OF_EQUITY_BPS
    lines = [
        "",
        "### Realised turnover, every variant",
        "",
        "Round trips a year, from the fee line the engine charged, by the registered",
        "definition: fees as basis points of equity a year, divided by the",
        f"{_fixed(RESEARCH_FEE_OF_EQUITY_BPS, 2)} basis points one full-book round trip costs at",
        "research fees.",
        "",
        f"Amendment 5 registered its break-evens against an illustrative {ILLUSTRATIVE_CARRY_BPS}",
        f"basis points of gross carry a year: **{research:.1f}** round trips at research fees and",
        f"**{execution:.1f}** at execution fees. The realised carry is not that figure, so both",
        "comparisons are shown.",
        "",
        f"| variant | round trips/yr | vs {research:.1f} | vs {execution:.1f} |",
        "|---|---:|---|---|",
    ]
    for row in rows:
        block = _break_even(row)
        if block is None:
            continue
        realised = _number(block["realised_round_trips_per_year"])
        if realised is None:
            continue
        turnover = Decimal(str(realised))
        lines.append(
            f"| {_label(row)} | {realised:.2f} "
            f"| {_inside(turnover, research)} | {_inside(turnover, execution)} |"
        )
    lines.extend(
        [
            "",
            "Every variant sits inside the illustrative research break-even and most sit",
            "outside the execution one. That comparison is against an assumed 4% carry rather",
            "than against what these variants earned, and the realised break-evens above,",
            "computed from the realised gross carry, are the binding pair.",
        ]
    )
    return lines


def _inside(realised: Decimal, threshold: Decimal) -> str:
    """Whether realised turnover sits inside a break-even, in words rather than a mark."""
    return "inside" if realised <= threshold else "**outside**"


def _break_even(row: Mapping[str, object]) -> Mapping[str, object] | None:
    """One variant's break-even block as the analysis computed it.

    Read, never recomputed. The arithmetic lives in the analysis so that the figure
    on the page and the figure in the result file cannot disagree, and so that a
    reader can check it against the monthly series in the same file.
    """
    block = row.get("break_even")
    return None if not isinstance(block, dict) else _mapping(block)


def _break_even_section(payload: Mapping[str, object]) -> str:
    sensitivity = _mapping(payload["execution_sensitivity"])
    lines = [
        "## 12. Break-even turnover at the execution venue",
        "",
        "Amendments 4 and 5. The research grid runs at one venue's published schedule; the",
        "only venue this account can trade is another, whose spot leg is four to eight times",
        "more expensive. This is a **single clearly-labelled sensitivity**, read by no",
        "criterion, consuming no variant budget. **No result at the research venue is evidence",
        "about execution at the other one.**",
        "",
    ]
    if not sensitivity.get("ran"):
        lines.extend([f"Not run: {_text(sensitivity.get('reason'))}", ""])
        return NEWLINE.join(lines)

    best = _text(sensitivity["variant"])
    rows = {_text(row["variant"]): row for row in _headline_rows(payload)}
    row = rows.get(best)
    lines.extend(
        [
            f"The re-cost was run on **`{best}`**, chosen by the rule fixed before the grid",
            f"ran: {_text(sensitivity['chosen_by'])}.",
            "",
            "| | round trips per year |",
            "|---|---:|",
        ]
    )
    outcome = None if row is None else _break_even(row)
    if outcome is None:
        lines.extend(
            [
                "| break-even at research fees | could not be formed |",
                "| break-even at execution fees | could not be formed |",
                "| realised turnover | could not be formed |",
                "",
                "The gross return or the fee line the arithmetic needs was not available, so no",
                "break-even is reported. It is not reported as zero: a tight threshold and no",
                "threshold are different facts about a strategy.",
                "",
            ]
        )
        return NEWLINE.join(lines)

    lines.extend(
        [
            f"| break-even at research fees ({RESEARCH_FEE_OF_EQUITY_BPS:.2f} bps of equity) "
            f"| {_text(outcome['break_even_at_research_fees'])} |",
            f"| break-even at execution fees ({EXECUTION_FEE_OF_EQUITY_BPS:.2f} bps of equity) "
            f"| {_text(outcome['break_even_at_execution_fees'])} |",
            f"| realised turnover | {_text(outcome['realised_round_trips_per_year'])} |",
            f"| gross carry, annualised | {_text(outcome['gross_return_bps_per_year'])} bps |",
            "",
            _text(outcome["sentence"]),
            "",
            *_turnover_table(payload),
            "",
            "**Both break-evens are upper bounds.** Spread and slippage also scale with",
            "turnover, are identical at both venues, and are excluded from the fee arithmetic,",
            "so the break-even on total cost is strictly lower than either figure. The",
            "conversion leg is excluded too, but **not for the reason amendment 5 gives**:",
            "see the correction below.",
            "",
            "### 12.1 A correction to amendment 5's justification",
            "",
            "Section 27.2 excludes the conversion leg from the fee arithmetic and says it is",
            "*charged twice for a whole run rather than per rebalance, so folding a fixed cost",
            "into a per-round-trip figure would misattribute it to turnover*. **That premise is",
            "wrong.** The engine charges the conversion twice per *position*, on the way in and",
            "on the way out, so it scales with traded notional exactly as a fee does. In this",
            "run the conversion line is 10.0000 basis points of turnover for every one of the",
            "nine variants, to four decimal places, which is the registered rate and not a",
            "coincidence.",
            "",
            "**What this changes, and what it does not.** The registered *definition* of the",
            "break-even is unaffected: it was defined on the fee line alone and that is what was",
            "computed, so no figure in this document moves. What changes is the reading. The",
            "excluded conversion is not a fixed overhead sitting outside the turnover question;",
            "it is a turnover-scaling charge about half again the size of the exchange fees",
            "themselves, and its exclusion makes the break-evens a **looser** upper bound than",
            "section 27.2 claims. A reader recomputing at another fee schedule should add it to",
            "the fee line rather than treat it as a constant.",
            "",
            "This is a defect in a justification, not in a computation, and it is reported",
            "rather than repaired in place: amendment 5 was registered before the run and its",
            "text is not edited afterwards. It is not a voiding reason under section 29.2 -",
            "every registered parameter was read by the code path that ran, and the conversion",
            "rate the ledger charged is the registered one.",
            "",
            "### Declared expectation D2",
            "",
            "Registered before the runner produced anything: *the binding constraint on this",
            "family is holding period, not signal quality.*",
            "",
            _d2_lines(payload, outcome),
            "",
        ]
    )
    return NEWLINE.join(lines)


def _shown(value: Decimal | None) -> str:
    """A turnover figure, or the reason there is not one."""
    return (
        "undefined: no positive gross carry to cover fees with" if value is None else f"{value:.2f}"
    )


def _d2_lines(payload: Mapping[str, object], outcome: Mapping[str, object]) -> str:
    """D2a and D2b, each confirmed, refuted or unresolved."""
    rows = _headline_rows(payload)
    turnovers: list[Decimal] = []
    clearing: list[Decimal] = []
    for row in rows:
        computed = _break_even(row)
        if computed is None:
            continue
        realised = Decimal(_text(computed["realised_round_trips_per_year"]))
        turnovers.append(realised)
        criteria = _mapping(row["criteria"])
        if criteria.get("all_six_hold"):
            clearing.append(realised)
    try:
        a = d2a_holds(clearing_turnovers=tuple(clearing), all_turnovers=tuple(turnovers))
    except BreakEvenUndefined:
        a = None
    held = outcome.get("d2b_holds")
    b = None if held is None else bool(held)
    return NEWLINE.join(
        [
            "- **D2a** (a clearing variant is at or below the median realised turnover of the",
            f"  nine): **{_verdict_word(a)}**"
            + ("" if clearing else " - no variant cleared all six, so it cannot be evaluated"),
            "- **D2b** (the best variant's execution break-even is fewer than 12 round trips a",
            f"  year): **{_verdict_word(b)}**",
            "",
            "D2 carries no weight in the verdict. It was stated so that a story told after the",
            "fact about fees and holding periods can be checked against a prediction made",
            "before it.",
        ]
    )


def _samples_section(payload: Mapping[str, object]) -> str:
    """Rules C3 and S1: which of the two order-book samples the results require.

    Both rules were registered as computed conditions on this file before any figure
    in it had been read, for the same reason: an acquisition decided after a number
    exists is an acquisition the number decided.

    Recomputed here from the result file rather than read out of it. The runner writes
    both decisions into the JSON for a reader of the file, but a report that trusted
    that block would print nothing at all for a file written by an earlier runner, and
    silently. Same functions, one path, no chance of the page and the file disagreeing.
    """
    rows = analyse(payload)
    capacity = capacity_report(payload, rows)
    headline = headline_cell().label
    lines = [
        "## 14. What the two sample rules decided",
        "",
        "Neither order-book sample is a trial and neither can change a variant's result.",
        "Both are acquisitions, and both were made conditional on this file **before it",
        "existed**: rule C3 for depth, in section 17, and rule S1 for spread, in section 30.",
        "",
        "### Rule C3, capacity (depth)",
        "",
        f"Shown in the headline cell, `{headline}`. The requirement below is computed across",
        "all four registered cells, so a variant that earned inside the window in any of them",
        "would still call the sample for.",
        "",
        "| variant | scored months inside the depth window | mean net inside | mean net outside"
        " | outcome |",
        "|---|---:|---:|---:|---|",
    ]
    for item in capacity:
        if item.cell != headline:
            continue
        lines.append(
            f"| `{item.variant}` "
            f"| {item.depth_months} "
            f"| {_percent(item.mean_inside)} "
            f"| {_percent(item.mean_outside)} "
            f"| {item.verdict.value} |"
        )
    required = depth_sample_is_needed(capacity)
    lines.extend(
        [
            "",
            f"**Depth sample required: {_yes(required)}.** "
            + (
                "At least one variant lands on a measured outcome, so section 12's "
                "twenty-symbol, seventeen-day bookDepth sample is acquired."
                if required
                else "No variant lands on a measured outcome, so capacity is reported as "
                "UNESTABLISHED and the bookDepth sample is not acquired. Acquiring data in "
                "order to print that word would be acquiring data the registered rule does "
                "not read."
            ),
            "",
            "UNESTABLISHED does not mean this family has no capacity. It means this dataset",
            "cannot say what it is, which is the same class of statement as invariant 9's",
            "*not evaluable*, and it is not softened.",
            "",
        ]
    )
    spread = spread_acquisition(rows)
    best = (
        "none"
        if spread.best_variant is None
        else f"`{spread.best_variant}` in `{spread.best_cell}`"
    )
    lines.extend(
        [
            "### Rule S1, the spread sample",
            "",
            "| | |",
            "|---|---:|",
            f"| runs considered, at research fees | {spread.considered} |",
            f"| of those, with a positive net return | {spread.positive_count} |",
            f"| best run | {best} |",
            f"| its net return | {_percent(spread.best_net_return)} |",
            "",
            f"**Spread sample acquired: {_yes(spread.acquire)}.** "
            + (
                "A variant earns at research fees, so the spread assumption is precisely the "
                "cost that could kill it and section 12's 36 symbol-days are acquired."
                if spread.acquire
                else "No registered variant earns a positive net return at research fees. "
                "Spread is an assumed cost and measuring it can only make a variant look "
                "worse, so the measurement would refine a cost line on a book that does not "
                "earn. The 1.8 to 3.2 GB is not downloaded."
            ),
            "",
            "Either way the configured spread remains labelled an assumption under invariant",
            "12. An unmeasured spread is never reported as a measured one, and a decision not",
            "to measure is not a claim that the assumption was right.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _unpublished(depth: Mapping[str, object]) -> str:
    """Which symbol-days the venue never published, named rather than counted.

    A gap described only by its size is a gap a reader cannot check.
    """
    absent = depth.get("days_not_published")
    if not isinstance(absent, dict) or not absent:
        return "None, in fact: every requested symbol-day was published."
    parts = [
        f"`{symbol}` on {', '.join(str(day) for day in days)}"
        for symbol, days in sorted(absent.items())
        if isinstance(days, list) and days
    ]
    return "; ".join(parts) + "."


def _depth_section(depth: Mapping[str, object] | None) -> str:
    """What the acquired sample measured, and the three things it cannot say.

    Empty when no sample exists, which is the ordinary case: rule C3 asks for one only
    when a variant earned inside the depth window, and the section is written by the
    acquisition rather than by the grid.
    """
    if depth is None:
        return NEWLINE.join(
            [
                "## 15. Capacity, measured",
                "",
                "No depth sample exists. Section 14's rule C3 did not ask for one, so nothing",
                "was acquired and no capacity figure is reported as measured.",
                "",
            ]
        )
    leg = ACCOUNT_EQUITY.amount / (Decimal(MAX_POSITIONS) * (Decimal(1) + MARGIN_FRACTION))
    per_symbol = [_mapping(item) for item in _sequence(depth["per_symbol"])]
    thinnest = min(
        (item for item in per_symbol if item["median_across_days_within_1pct"] is not None),
        key=lambda item: Decimal(_text(item["median_across_days_within_1pct"])),
        default=None,
    )
    lines = [
        "## 15. Capacity, measured",
        "",
        "Rule C3 asked for this and section 12 fixed its shape before anything ran.",
        f"**{_text(depth['symbol_days_fetched'])} symbol-days** were acquired of "
        f"{_text(depth['symbol_days_requested'])} requested, "
        f"{_text(depth['megabytes_fetched'])} MB, and every one of them was verified",
        "against the publisher's own SHA-256. The `bookTicker` spread sample of the same",
        "section was **not** acquired, because rule S1 was false.",
        "",
        f"- window: **{_text(depth['depth_window'])}** (rule C1: no capacity figure omits it)",
        f"- symbols: the **{_text(depth['symbol_count'])}** deepest carry-universe perpetuals "
        f"by trailing 30-day median quote turnover at {_text(depth['symbols_selected_at'])}",
        f"- days: the first of each month, {len(_sequence(depth['days_requested']))} of them",
        "- figures: resting notional on **both sides**, cumulative to the stated distance",
        "  from mid, median across the day's minutes and then across the days",
        "",
        "| symbol | days measured | days with an opening window | median within 1% | within 5% |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in sorted(per_symbol, key=lambda entry: _text(entry["symbol"])):
        lines.append(
            f"| `{_text(item['symbol'])}` "
            f"| {_text(item['days_measured'])} "
            f"| {_text(item['days_with_an_opening_window'])} "
            f"| {_money(item['median_across_days_within_1pct'])} "
            f"| {_money(item['median_across_days_within_5pct'])} |"
        )
    lines.extend(
        [
            "",
            "Across every measured day: "
            f"**{_money(depth['median_of_every_measured_day_within_1pct'])}** within 1 per cent "
            f"and **{_money(depth['median_of_every_measured_day_within_5pct'])}** within 5, "
            "in USDT.",
            "",
            "### What it means at this account, and what it does not",
            "",
            f"One leg of one pair is **{leg:,.2f} EUR** at the registered equity, position count",
            "and margin fraction. Against the thinnest of the twenty that is a fraction of a",
            "basis point of what rests within one per cent of mid:",
            "",
        ]
    )
    if thinnest is not None:
        floor = Decimal(_text(thinnest["median_across_days_within_1pct"]))
        share = (leg / floor * 100).quantize(Decimal("0.0001"))
        lines.extend(
            [
                f"| thinnest of the twenty | `{_text(thinnest['symbol'])}` |",
                "|---|---:|",
                f"| its median resting notional within 1% | {_money(floor)} |",
                f"| one leg, as a share of it | {share}% |",
                "",
                "No FX conversion is applied to that comparison. The depth is in USDT and the",
                "leg in EUR, and no plausible rate moves a figure of this size by an order of",
                "magnitude. The units are stated rather than blended.",
                "",
            ]
        )
    lines.extend(
        [
            "**Depth is not what stops this family.** One leg is under two hundredths of a",
            "per cent of what rests within one per cent of mid on the thinnest symbol sampled,",
            "so no capacity constraint could have produced the returns in section 5. What does",
            "stop it is in sections 7 and 12: the funding stream is real and is roughly",
            "cancelled by the basis, and the costs then exceed what is left.",
            "",
            "**Three things this sample cannot say.** These are the twenty *deepest* members,",
            "so the median across them is an upper bound on what a median universe member",
            "offers, and the universe ran to 340 pairs. It measures the **perpetual leg only**,",
            "and a cash-and-carry needs both legs to fill. And it is seventeen days inside a",
            "seventeen-month stretch of a window running from 2021 to 2026: rule C2 makes any",
            "figure outside that window an extrapolation, and none is offered here.",
            "",
            "**Three days were never published.** "
            + _unpublished(depth)
            + " A day the venue did not publish is absent from the median rather than "
            "counted as a zero, and the day count beside each figure says how many it "
            "was taken over.",
            "",
            "**The opening window is mostly absent.** The venue's publication frequently starts",
            "hours into the day, so the 00:00-00:05 UTC figures the statistic asks for exist on",
            "about ten of the seventeen days. Where they are absent the figure is null, never",
            "zero: no snapshot is not an empty book.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _yes(value: bool) -> str:
    """A boolean as the word a reader reads, so no section spells it differently."""
    return "yes" if value else "no"


def _verdict_word(value: bool | None) -> str:
    if value is None:
        return "unresolved"
    return "confirmed" if value else "refuted"


def _criteria_section(payload: Mapping[str, object]) -> str:
    rows = _headline_rows(payload)
    lines = [
        "## 13. The six criteria",
        "",
        "All six must hold, in the headline cell, for a variant to count as working. A",
        "criterion that could not be evaluated reads `n/a`, which is not a pass.",
        "",
        "| variant | 1 null+sign | 2 deflation | 3 selection | 4 regimes | 5 sign "
        "| 6 recent | all six | N_eff |",
        "|---|---|---|---|---|---|---|---|---:|",
    ]
    for row in rows:
        criteria = _mapping(row["criteria"])
        lines.append(
            f"| {_label(row)} "
            f"| {_mark(criteria.get('1_beats_exposure_matched_null_and_is_positive'))} "
            f"| {_mark(criteria.get('2_survives_deflation'))} "
            f"| {_mark(criteria.get('3_win_is_selection'))} "
            f"| {_mark(criteria.get('4_regime_stability'))} "
            f"| {_mark(criteria.get('5_sign_stable_across_cost_regimes'))} "
            f"| {_mark(criteria.get('6_recent_window_holds'))} "
            f"| **{_mark(criteria.get('all_six_hold'))}** "
            f"| {_fixed(criteria.get('effective_observations'), 1)} |"
        )
    lines.extend(
        [
            "",
            "**Criterion 1 carries a sign condition** that SEXTANT-005's did not. That task",
            "recorded its criterion 1 misfiring: in a cross-section where almost everything",
            "fell, a variant could clear *beats the 95th percentile of its own null* by losing",
            "less than chance lost, and two did, one while destroying most of the account. The",
            "correction requires a positive net return as well, and it makes the criterion",
            "stricter rather than looser.",
            "",
            "`N_eff` is the effective observation count after the series' own autocorrelation.",
            "",
            *_strengthened_criterion(rows),
        ]
    )
    return NEWLINE.join(lines)


def _strengthened_criterion(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Amendment 11's criterion 1, re-read against figures it did not govern.

    A supplementary reading and never a re-scoring, so the first line says which form
    produced the verdict. Empty when the result file predates the block, because a section
    that silently prints nothing is worse than one that is not there.
    """
    blocks = [
        (row, _mapping(_mapping(row["criteria"])["supplementary_1_strengthened"]))
        for row in rows
        if "supplementary_1_strengthened" in _mapping(row["criteria"])
    ]
    if not blocks:
        return []
    out = [
        "### 13.1 Criterion 1 strengthened: a supplementary reading, not a re-scoring",
        "",
        "**F1 was judged on criterion 1 as registered when it ran**: a Sharpe above the 95th",
        "percentile of the variant's own exposure-matched null, **and** a strictly positive net",
        "return. That is the form the table above uses and the form the verdict rests on.",
        "Nothing below changes a letter.",
        "",
        '**Amendment 11 strengthens the absolute clause from F2.** "Strictly positive" would',
        "pass a variant earning 18.70 EUR on 1,500 across 56 months, which is a positive number",
        "and is not a return distinguishable from nothing. From F2 the clause requires the mean",
        "scored-month return to exceed **one standard error of itself**: the mean over its own",
        "standard deviation, times the root of the observation count, above one.",
        "",
        "| variant | mean-return t | above 1 | 1 as registered | 1 strengthened |",
        "|---|---:|---|---|---|",
    ]
    for row, block in blocks:
        criteria = _mapping(row["criteria"])
        out.append(
            f"| {_label(row)} "
            f"| {_fixed(_number(_optional_text(block['mean_return_t_statistic'])), 2)} "
            f"| {_mark(block['return_is_distinguishable_from_zero'])} "
            f"| {_mark(criteria.get('1_beats_exposure_matched_null_and_is_positive'))} "
            f"| {_mark(block['criterion_1_would_hold'])} |"
        )
    out.extend(
        [
            "",
            "**It changes nothing here, and it could not have.** No variant clears criterion 1",
            "in the headline cell under the weaker form, and the strengthened clause is strictly",
            "narrower: a mean exceeding its own standard error is positive, so everything the",
            "new clause admits the old clause already admitted. The change can only ever remove",
            "a pass, on any data, in any family.",
            "",
            "**That property is the whole reason it is safe to register after these figures were",
            "read.** An amendment that could only ever make a bar harder cannot have been chosen",
            "to let something through. Amendment 11 registers it as rule P1 rather than as a",
            "patch to this one criterion, because the same confusion between *better than a",
            "losing null* and *makes money* has now appeared in three places: SEXTANT-004's",
            "exposure-matched null, criterion 1 as written here, and both of the first two",
            "attempts at a floor for rule S1.",
            "",
        ]
    )
    return out


def _ratio_text() -> str:
    """The headline-to-bound multiple, from the registered value rather than retyped."""
    return f"{SPREAD_BOUND_RATIO.normalize():f}"


def _amendment_twelve_section(payload: Mapping[str, object]) -> str:
    """Section 17: what this document's two largest cost lines rest on.

    Added rather than edited in, under rule A12.7. Everything above it is the result as it
    was computed and committed; this says what two of its lines are made of, and both
    answers were reached after that result existed. A correction that rewrites the thing it
    corrects leaves no record of either.
    """
    cell = headline_cell().label
    items = currency_corrections(payload, cell=cell)
    if not items:
        return ""
    scaled = the_currency_line_scaled_with_turnover(items)
    charged = sum((item.as_charged for item in items), Decimal(0))
    corrected = sum((item.corrected for item in items), Decimal(0))
    lines = [
        "## 17. Amendment 12: what the two largest cost lines rest on",
        "",
        "**Added, not edited.** Everything above is the result as it was computed and",
        "committed. Amendment 12 was registered after it existed, and this section says what",
        "two of its cost lines are made of without changing one of them. Section 34 of the",
        "pre-registration is the rule; this is what the rule found.",
        "",
        "### 17.1 The spread line is an upper bound, and the verdict does not rest on it",
        "",
        "**The largest charge in section 7.1 is spread, and it is computed at a registered",
        f"UPPER BOUND of 10 bps per leg: about {_ratio_text()} times the 0.53 bps half-spread",
        "section 7.5 measured.** It is not an estimate and not a calibration. Rule E1 tried to",
        "replace it with something estimated and section 7.6 records that the estimator was",
        "refused, so the bound stands as the number every criterion here was evaluated",
        "against.",
        "",
        "**F1's (B) stands on the counterfactual and not on the assumption.** Section 7.4",
        "deletes spread and slippage entirely - not to 0.53, to zero - and **seven of the nine",
        "variants still lose**. The two that cross zero earn 18.70 and 39.48 EUR on 1,500",
        "across 56 months and fail criterion 2 with a Deflated Sharpe of 0.0000 against a bar",
        "of 0.95.",
        "",
        "**That is why the verdict survives this amendment unchanged, and it is the only",
        "reason worth having.** An honest (B) that would survive the cost assumption being",
        "wrong is worth more than one that merely was not challenged. Had the family's answer",
        "moved when the assumed cost was deleted, amendment 12's spread-contingent outcome is",
        "exactly what would have applied - and it would have deferred the verdict rather than",
        "closing it.",
        "",
        "**So rule H1 did not fire and no historical quote acquisition was attempted.** It is",
        "conditional on a family being recorded spread-contingent and none has been. F1",
        "cannot be: it fails at the headline, at the bound, and at zero. The rule stands",
        "unfired rather than unsatisfied, and the work it would authorise - three days of",
        "establishing what the venue published, in the Stage 0 style - is not done",
        "speculatively.",
        "",
        "### 17.2 The currency line was double-counted, and here is what it should have been",
        "",
        "**Rule A12.9 asked how the conversion is charged and found a defect.** The invariant",
        "it registers is that the total currency charge must scale with the number of times",
        "capital actually crosses currency and must not scale with turnover. Converting to the",
        "account's currency for reporting is reporting, not a charge.",
        "",
        f"**It scaled with turnover.** {_yes(scaled).capitalize()}: every one of the nine",
        "variants was charged exactly 10 basis points of its own turnover, which is the",
        "signature of a per-trade charge. The conversion was applied inside every trade, on",
        "that trade's notional. Rotating between two instruments quoted in the same foreign",
        "currency crosses no boundary at all - selling one into the quote asset and buying",
        "another out of it is one currency throughout - so every rebalance after the first was",
        "charged for a crossing that did not happen.",
        "",
        "| variant | turnover | charged | should be | double-counted "
        "| net as run | net corrected |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in items:
        lines.append(
            f"| {_variant_label(item.variant)} "
            f"| {_money(item.turnover)} "
            f"| {_money(item.as_charged)} "
            f"| {_money(item.corrected)} "
            f"| {_money(item.removed)} "
            f"| {_money(item.net_pnl)} "
            f"| {_money(item.corrected_net_pnl)} |"
        )
    multiple = charged / corrected if corrected else Decimal(0)
    lines.extend(
        [
            "",
            f"**Across the nine: {_money(charged)} charged against {_money(corrected)} for two",
            f"crossings each, a factor of {multiple:.1f}.** The correct charge is ten basis",
            "points of the opening equity when capital enters the quote currency and ten of the",
            "closing equity when it leaves. Two crossings, on the capital that crossed - not on",
            "the notional, which for a levered market-neutral book is several times the capital",
            "and never touches the account's own currency at all.",
            "",
            "**Every variant still loses with the whole double count returned**, which is the",
            "only question that matters for the verdict letter. The family-level finding is",
            "unmoved for the same reason: the price legs gave back 99.1 per cent of the funding",
            "received, and a charge correction on the other side of that does not create a",
            "premium that was not there.",
            "",
            "**The correction is first order and is labelled so.** Returning the charge also",
            "returns the compounding it cost along the way, and the exit crossing would then",
            "convert a slightly larger closing equity. Both are under a euro on these figures.",
            "",
            "**The engine is fixed and F1 is not rerun.** The charge is now made where it is",
            "incurred, six tests hold the invariant, and five of them fail when the per-trade",
            "charge is put back - which was checked by putting it back. F1's result file is",
            "left exactly as it was committed: rerunning it would consume one of the two",
            "registered re-executions to restate a verdict that does not move.",
            "",
            "**What it does change is the composition of section 7.1.** The currency leg was",
            "reported there as 20.9 per cent of the toll and larger than the venue's own fees.",
            "That finding was correct about the ledger and wrong about the world: the line was",
            "inflated by a defect, and a euro-funded account trading USDT-quoted instruments",
            "pays a currency toll far smaller than F1's charges suggested. Section 31's",
            "instruction to report the currency leg on its own line in every family stands, and",
            "is now more useful rather than less: it is what made the defect visible.",
            "",
        ]
    )
    return NEWLINE.join(lines)


def _what_the_verdict_rests_on(payload: Mapping[str, object]) -> list[str]:
    """What this letter claims and what it does not, given the toll's composition.

    A negative verdict is not one statement. "The premium does not exist" and "the
    premium exists and the cost of taking it exceeds it" are different findings with
    different consequences, and which one this is depends on what the charges are made
    of. Section 7.1 computes that, and the verdict has to carry it rather than leave a
    reader to derive it eight sections earlier.
    """
    items = tolls(payload, cell=headline_cell().label)
    if not items:
        return []
    combined = combined_toll(items)
    earning = [item for item in items if item.lines.gross_before_costs > 0]
    stuck = [
        item
        for item in items
        if item.lines.flip_multiplier is None or item.lines.flip_multiplier <= 0
    ]
    across = combined.market_gain + combined.funding_received
    return [
        "### What this verdict rests on",
        "",
        "**The family-level result is the headline, not the best variant's.** Across the",
        f"{_count(len(items))} variants the book received {_money(combined.funding_received)} in",
        f"funding and the price legs gave back "
        f"{_share(-combined.market_gain, combined.funding_received)}",
        f"of it, leaving **{_money(across)}** on 1,500 of equity before a single charge. That is",
        "the finding:",
        "**the premium is compensation for the basis risk that earns it, priced close to",
        "exactly.** The best single variant cleared",
        f"{_money(max(item.lines.gross_before_costs for item in items))}, and reporting that",
        "figure in front would give the opposite impression from the same run.",
        "",
        f"**The premium is real.** {_count(len(earning)).capitalize()} of the "
        f"{_count(len(items))} variants cleared a positive carry before any charge: for those,",
        "the funding received exceeded what the price legs gave back. This is the first",
        "positive gross result anywhere in this project, and it is what theory predicts for a",
        "hedged carry. F1 does not say the effect is absent.",
        "",
        f"**It dies in the toll, and {_share(combined.assumed, combined.charges)} of that toll",
        "is assumed rather than measured.** Spread and slippage are configured values under",
        f"invariant 12; published exchange fees are only "
        f"{_share(combined.fees, combined.charges)} of what was charged. So this verdict is",
        "**not** the statement that a retail fee schedule consumed the premium, and it is",
        "not purely a statement about the market either. It rests substantially on two",
        "numbers this project chose, and section 7.2 states what it would have produced had",
        "they been chosen lower.",
        "",
        f"**What survives that caveat.** {_count(len(stuck)).capitalize()} of the "
        f"{_count(len(items))} variants lose with spread and slippage deleted entirely, so",
        "for those the assumption changes nothing at all. Two do turn positive, and by rule",
        "S1 as amendment 9 writes it they clear criterion 1 in that counterfactual. **They",
        "still fail criterion 2 with a deflated Sharpe of 0.0000 against a bar of 0.95**, so",
        "no variant clears all six even with the assumed cost deleted. **(B) is therefore",
        "robust to the assumption it rests on**, which is the claim that had to be checked",
        "before the letter could be trusted. Section 7.4 carries the arithmetic.",
        "",
        "**The currency leg is reported separately wherever it appears, and always will be.**",
        f"At {_share(combined.conversion, combined.charges)} of the toll against",
        f"{_share(combined.fees, combined.charges)} in exchange fees, a euro-funded account",
        "trading instruments quoted elsewhere pays a currency toll larger than the venue's",
        "own. It scales with turnover rather than sitting fixed per run, and it will land the",
        "same way on every family quoted away from the account's currency. Section 31.5.",
    ]


def _verdict_section(payload: Mapping[str, object]) -> str:
    verdict = _mapping(payload["verdict"])
    ones = _sequence(verdict["variants_clearing_criterion_1"])
    alls = _sequence(verdict["variants_clearing_all_six"])
    return NEWLINE.join(
        [
            "## 16. Verdict for family F1",
            "",
            f"### ({_text(verdict['letter'])})",
            "",
            _text(verdict["reason"]),
            "",
            "- variants clearing criterion 1: "
            + (", ".join(f"`{_text(item)}`" for item in ones) if ones else "none"),
            "- variants clearing all six: "
            + (", ".join(f"`{_text(item)}`" for item in alls) if alls else "none"),
            "",
            *_what_the_verdict_rests_on(payload),
            "",
            "This is one family's verdict, not the task's. The task's verdict lives in",
            "`docs/VERDICT-006.md` and is written once every family has been run or reported",
            "as not reached.",
            "",
        ]
    )


__all__ = ["REPORT_PATH", "render"]

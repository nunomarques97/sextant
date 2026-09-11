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
from collections.abc import Mapping, Sequence
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
    THINNER_EVIDENCE_VARIANT,
    execution_sensitivity,
    headline_cell,
    round_trip_fee_bps,
)
from sextant.app.spike_006_f1_analysis import (
    analyse,
    capacity_report,
    depth_sample_is_needed,
    spread_acquisition,
)
from sextant.app.spike_006_f1_depth import DEPTH_RESULTS
from sextant.engine.execution.breakeven import BreakEvenUndefined, d2a_holds

REPORT_PATH = Path("docs") / "SPIKE-006-F1-RESULTS.md"

NEWLINE = chr(10)


def render(
    results_path: Path = RESULTS_PATH,
    report_path: Path = REPORT_PATH,
    depth_path: Path = DEPTH_RESULTS,
) -> Path:
    """Read the result file and write the report beside it."""
    with results_path.open(encoding="utf-8") as handle:
        payload = _mapping(json.load(handle))
    depth = (
        _mapping(json.loads(depth_path.read_text(encoding="utf-8")))
        if depth_path.is_file()
        else None
    )
    sections = [
        _preamble(payload),
        _provenance_section(payload),
        _budget_section(payload),
        _window_section(payload),
        _cost_section(payload),
        _headline_section(payload),
        _cadence_section(payload),
        _decomposition_section(payload),
        _regime_section(payload),
        _recent_section(payload),
        _sign_section(payload),
        _deflation_section(payload),
        _break_even_section(payload),
        _criteria_section(payload),
        _samples_section(payload),
        _depth_section(depth),
        _verdict_section(payload),
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
        "| variant | combined | timing | selection | funding | gross | costs |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        block = _mapping(row["decomposition"])
        lines.append(
            f"| {_label(row)} "
            f"| {_percent(block['combined_net_return'])} "
            f"| {_percent(block['timing_effect_net_return'])} "
            f"| {_percent(block['selection_effect_net_return'])} "
            f"| {_money(block['funding_received_net'])} "
            f"| {_money(block['gross_pnl'])} "
            f"| {_money(block['total_costs'])} |"
        )
    lines.extend(
        [
            "",
            "Funding, gross and costs are in account currency on the registered starting",
            "equity. A funding figure larger than the combined return means the settlement",
            "stream earned more than the book kept, and the difference is basis and costs.",
            "",
        ]
    )
    return NEWLINE.join(lines)


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
            "**Both break-evens are upper bounds.** Spread and slippage also scale with",
            "turnover, are identical at both venues, and are excluded from the fee arithmetic,",
            "so the break-even on total cost is strictly lower than either figure. The",
            "conversion leg is excluded too: it is charged twice for a whole run rather than",
            "per rebalance, and folding a fixed cost into a per-round-trip figure would",
            "misattribute it to turnover.",
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
        ]
    )
    return NEWLINE.join(lines)


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
            "This is one family's verdict, not the task's. The task's verdict lives in",
            "`docs/VERDICT-006.md` and is written once every family has been run or reported",
            "as not reached.",
            "",
        ]
    )


__all__ = ["REPORT_PATH", "render"]

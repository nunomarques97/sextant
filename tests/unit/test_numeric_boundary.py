"""Money never reaches an array library, except in one named function.

Safeguard 2 of the SEXTANT-004 brief, made mechanical. The rule:

    No module under ``src`` may import both an array library (numpy, scipy,
    polars) and a monetary type (``Decimal``, ``Price``, ``Quantity``,
    ``Notional``).

with exactly one exemption, ``engine.statistics.boundary``, which is the
crossing itself.

The rule is stated as an import-level property rather than as a call-level one
because import-level is what a test can check exhaustively over a whole tree. A
module that cannot name ``Decimal`` cannot pass one to ``numpy.mean``, and a
module that cannot name ``numpy`` cannot pass anything to it at all. It is a
sufficient condition for the property that matters, not a necessary one, and
that is the right side to err on.

The second half of this module checks the boundary function's own behaviour: it
converts in one direction, it refuses degenerate input rather than inventing a
number, and its reverse is a separate, named operation that never reconstructs
money.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from sextant.domain.money import Notional
from sextant.engine.statistics.boundary import (
    STATISTIC_PLACES,
    DegenerateCurve,
    ReturnSeries,
    equity_curve_as_returns,
    returns_as_series,
    statistic_as_decimal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "sextant"

ARRAY_LIBRARIES = frozenset({"numpy", "scipy", "polars"})
MONETARY_NAMES = frozenset({"Decimal", "Price", "Quantity", "Notional"})

#: The single crossing. Relative to ``src``, POSIX-separated.
BOUNDARY = "engine/statistics/boundary.py"


def python_files() -> list[Path]:
    """Every module under ``src``, in a stable order."""
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def imported_names(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """The top-level packages and the imported symbol names in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    packages: set[str] = set()
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                packages.add(node.module.split(".")[0])
            for alias in node.names:
                symbols.add(alias.name)
    return frozenset(packages), frozenset(symbols)


def test_no_module_holds_both_an_array_library_and_a_monetary_type() -> None:
    """The boundary exists in one place, and this is what makes that true."""
    offenders: list[str] = []
    for path in python_files():
        relative = path.relative_to(SRC).as_posix()
        if relative == BOUNDARY:
            continue
        packages, symbols = imported_names(path)
        arrays = packages & ARRAY_LIBRARIES
        money = symbols & MONETARY_NAMES
        if arrays and money:
            offenders.append(
                f"{relative} imports {sorted(arrays)} and {sorted(money)}. "
                "Cross the boundary through engine.statistics.boundary instead."
            )
    assert offenders == []


def test_the_boundary_module_is_the_only_exemption_and_it_still_exists() -> None:
    """A guard against the exemption being renamed and the rule quietly emptied."""
    boundary = SRC / BOUNDARY
    assert boundary.is_file(), f"{BOUNDARY} is the stated exemption and it is missing"
    packages, symbols = imported_names(boundary)
    assert packages & ARRAY_LIBRARIES
    assert symbols & MONETARY_NAMES


def test_the_statistics_package_never_names_money() -> None:
    """Stronger than the general rule, for the package most tempted to break it."""
    offenders: list[str] = []
    for path in (SRC / "engine" / "statistics").rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative == BOUNDARY:
            continue
        _, symbols = imported_names(path)
        if symbols & MONETARY_NAMES:
            offenders.append(f"{relative}: {sorted(symbols & MONETARY_NAMES)}")
    assert offenders == []


def test_the_dataframe_loader_never_names_money() -> None:
    """polars would happily parse an exact decimal string into a float."""
    _, symbols = imported_names(SRC / "adapters" / "storage" / "panel.py")
    assert not symbols & MONETARY_NAMES


# -- the crossing itself ------------------------------------------------------


def curve(*amounts: str) -> list[Notional]:
    """An equity curve from exact decimal text."""
    return [Notional(Decimal(amount)) for amount in amounts]


def test_the_equity_curve_crossing_produces_dimensionless_returns() -> None:
    """Money goes in, ratios come out, and the division happened in Decimal."""
    series = equity_curve_as_returns(curve("1000", "1100", "990"))
    assert isinstance(series, ReturnSeries)
    assert series.count == 2
    assert series.values[0] == pytest.approx(0.1)
    assert series.values[1] == pytest.approx(-0.1)
    assert series.periods_per_year == 12


def test_a_curve_with_one_point_has_no_returns_and_says_so() -> None:
    """Not an empty array, which would be silently summarised as zero."""
    with pytest.raises(DegenerateCurve):
        equity_curve_as_returns(curve("1000"))


def test_a_curve_that_touches_zero_is_refused_rather_than_divided_through() -> None:
    """A wiped-out account is a real outcome and must be reported as one."""
    with pytest.raises(DegenerateCurve):
        equity_curve_as_returns(curve("1000", "0", "500"))


def test_the_reverse_crossing_is_separate_and_quantised() -> None:
    """A statistic comes back as a Decimal of stated precision, never as money."""
    value = statistic_as_decimal(0.123456789)
    assert value == Decimal("0.123457")
    assert value.as_tuple().exponent == STATISTIC_PLACES.as_tuple().exponent
    assert type(value) is Decimal


def test_a_non_finite_statistic_has_no_decimal_form() -> None:
    """Better a raise than a Decimal('NaN') travelling into a report."""
    with pytest.raises(DegenerateCurve):
        statistic_as_decimal(float("inf"))


def test_a_decimal_return_sequence_crosses_through_the_same_module() -> None:
    """The second crossing is beside the first, so the list stays short."""
    series = returns_as_series([Decimal("0.01"), Decimal("-0.02")], periods_per_year=4)
    assert series.count == 2
    assert series.periods_per_year == 4


def test_a_return_series_refuses_a_nonsense_frequency() -> None:
    """Annualising by zero periods a year is not a thing."""
    with pytest.raises(ValueError, match="periods_per_year"):
        returns_as_series([Decimal("0.01")], periods_per_year=0)

"""Rules about the source that no type checker can express.

Three of these encode architectural commitments that would otherwise decay
quietly: no venue name inside the exchange-agnostic layers, no primary/fallback
vocabulary anywhere, and no ``Any``/``cast``/``type: ignore`` escape hatches in
our own code.
"""

from __future__ import annotations

import re
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "sextant"
CONFIG = REPO_ROOT / "config"

#: The layers that must never know which venues exist.
VENUE_AGNOSTIC_PACKAGES = ("domain", "ports", "engine")

#: Venue names that must not appear inside those layers.
VENUE_NAMES = ("binance", "kraken")


def python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


@pytest.mark.parametrize("package", VENUE_AGNOSTIC_PACKAGES)
@pytest.mark.parametrize("venue_name", VENUE_NAMES)
def test_no_venue_name_appears_in_an_exchange_agnostic_layer(package: str, venue_name: str) -> None:
    """The grep that proves strategies cannot depend on a specific exchange."""
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in python_files(SRC / package)
        if venue_name in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_no_venue_equality_branch_exists_anywhere_in_the_source() -> None:
    """``if venue == "binance"`` and its relatives, in any spelling."""
    pattern = re.compile(
        r"""(venue|exchange)\w*\s*(==|!=)\s*['"]|['"](binance|kraken)['"]\s*(==|!=)""",
        re.IGNORECASE,
    )
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in python_files(SRC)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_no_primary_or_fallback_exchange_selection_concept_exists() -> None:
    """A fallback venue is a silent assumption that two venues are interchangeable."""
    pattern = re.compile(r"\b(primary|fallback)\b", re.IGNORECASE)
    searched = python_files(SRC) + sorted(CONFIG.glob("*.yaml"))
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in searched
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == []


def escape_hatches(path: Path) -> list[str]:
    """Occurrences of ``Any``, ``cast()`` or a type-ignore comment in real code.

    Tokenised rather than grepped, so that prose explaining why these are
    banned does not itself trip the check.
    """
    found: list[str] = []
    with path.open("rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    for index, token in enumerate(tokens):
        location = f"{path.relative_to(REPO_ROOT).as_posix()}:{token.start[0]}"
        if token.type == tokenize.COMMENT and "type:" in token.string.replace(" ", ""):
            if "ignore" in token.string:
                found.append(f"{location}: {token.string.strip()}")
        elif token.type == tokenize.NAME and token.string == "Any":
            found.append(f"{location}: Any")
        elif token.type == tokenize.NAME and token.string == "cast":
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if following is not None and following.string == "(":
                found.append(f"{location}: cast(")
    return found


def test_the_source_uses_no_typing_escape_hatches() -> None:
    """``Any``, ``cast()`` and type-ignore comments are banned in ``src``.

    Tests may use a targeted ignore to pass a deliberately wrong type into a
    constructor; production code may not.
    """
    offenders = [entry for path in python_files(SRC) for entry in escape_hatches(path)]
    assert offenders == []


def test_datetime_now_appears_only_in_the_adapters_layer() -> None:
    """Everything above the adapters reads time through the Clock port."""
    pattern = re.compile(r"datetime\.now\(|time\.time\(|utcnow\(")
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in python_files(SRC)
        if pattern.search(path.read_text(encoding="utf-8"))
        and "adapters" not in path.relative_to(SRC).parts
    ]
    assert offenders == []


def test_no_exchange_sdk_or_http_client_is_imported_anywhere_yet() -> None:
    """SEXTANT-001 contains no network call to any exchange."""
    pattern = re.compile(
        r"^\s*(import|from)\s+(ccxt|krakenex|binance|requests|httpx|aiohttp|urllib3)\b",
        re.MULTILINE,
    )
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in python_files(SRC)
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_environment_example_carries_no_real_looking_secret() -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() in {"", "0", "1", "backtest", "replace-me"}, line

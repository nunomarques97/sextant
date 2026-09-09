"""The layering contract, executed.

This is the most important test in the repository. It runs import-linter
against the real contract file, so a strategy that reaches for an exchange
adapter, or a venue adapter that leans on its peer, fails here as well as in CI.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = REPO_ROOT / ".importlinter"

EXPECTED_CONTRACTS = {
    "domain-is-pure",
    "engine-is-abstract",
    "ports-are-abstract",
    "exchange-sdks-are-quarantined",
    "http-client-unreachable-from-the-core",
    "http-client-not-spoken-by-the-wiring-layer",
    # SEXTANT-003 added the storage engines. Same two-contract shape as httpx and
    # for the same reason: the core cannot reach them by any path, and the wiring
    # layer may hold a store without speaking parquet itself.
    "storage-engines-unreachable-from-the-core",
    "storage-engines-not-spoken-by-the-wiring-layer",
    "venues-are-independent-peers",
}


def read_contracts() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(CONTRACT_FILE, encoding="utf-8")
    return parser


#: Run the checker in a subprocess rather than in-process, so that the import
#: graph is rebuilt from disk each time. A probe module written and deleted
#: within one test process would otherwise be seen through a stale cache.
_RUNNER = (
    "import sys; from importlinter.cli import lint_imports; "
    "sys.exit(lint_imports(no_cache=True, no_logo=True))"
)


def run_lint_imports() -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", _RUNNER],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )


def test_every_expected_contract_is_configured() -> None:
    parser = read_contracts()
    configured = {
        section.removeprefix("importlinter:contract:")
        for section in parser.sections()
        if section.startswith("importlinter:contract:")
    }
    assert configured == EXPECTED_CONTRACTS


def test_the_two_venue_adapters_are_declared_mutually_independent() -> None:
    contract = read_contracts()["importlinter:contract:venues-are-independent-peers"]
    assert contract["type"] == "independence"
    modules = contract["modules"].split()
    assert "sextant.adapters.exchanges.binance" in modules
    assert "sextant.adapters.exchanges.kraken" in modules


def test_all_contracts_hold() -> None:
    result = run_lint_imports()
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Contracts: {len(EXPECTED_CONTRACTS)} kept, 0 broken." in result.stdout


@pytest.mark.parametrize(
    ("module_path", "forbidden_import"),
    [
        (
            "src/sextant/domain/_contract_probe.py",
            "from sextant.adapters.clocks import SystemClock",
        ),
        (
            "src/sextant/engine/_contract_probe.py",
            "from sextant.adapters.exchanges.kraken.client import KrakenClient",
        ),
        (
            "src/sextant/adapters/exchanges/kraken/_contract_probe.py",
            "from sextant.adapters.exchanges.binance.client import BinanceClient",
        ),
    ],
)
def test_a_forbidden_import_actually_breaks_the_build(
    module_path: str, forbidden_import: str
) -> None:
    """The deliberately failing experiment, automated.

    A contract that has never been seen to fail is a contract nobody has
    verified. This writes a real violating module, proves lint-imports rejects
    it, and removes it again.
    """
    probe = REPO_ROOT / module_path
    probe.write_text(
        f'"""Temporary contract probe. Removed by the test that wrote it."""\n\n'
        f"{forbidden_import}\n\n"
        f"__all__ = []\n",
        encoding="utf-8",
    )
    try:
        result = run_lint_imports()
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode != 0, "a forbidden import did not break the contract"
    assert "broken" in result.stdout


def test_contracts_hold_again_once_the_probe_is_removed() -> None:
    assert run_lint_imports().returncode == 0

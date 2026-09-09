"""Credential resolution and the git-ignored ``.env`` loader.

Two properties matter. A missing ``.env`` is not an error, because BACKTEST must
run on a machine that has never held a key. And an already-set environment
variable wins over the file unless the caller explicitly asks otherwise, so a
stale file cannot quietly override what an operator just exported.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sextant.app.credentials import (
    Credentials,
    credentials_for,
    key_variable,
    load_dotenv,
    secret_variable,
)


def test_the_variable_names_follow_one_generic_pattern_per_venue() -> None:
    """Adding a venue must never mean editing this module."""
    assert key_variable("kraken") == "SEXTANT_KRAKEN_API_KEY"
    assert secret_variable("binance") == "SEXTANT_BINANCE_API_SECRET"


def test_credentials_are_read_from_an_explicit_mapping() -> None:
    resolved = credentials_for(
        "kraken",
        {"SEXTANT_KRAKEN_API_KEY": "key-value", "SEXTANT_KRAKEN_API_SECRET": "secret-value"},
    )

    assert resolved.is_complete
    assert resolved.secret_values == ("key-value", "secret-value")


def test_a_half_present_credential_is_incomplete_rather_than_usable() -> None:
    """One half of a key pair is not a degraded credential, it is no credential."""
    resolved = credentials_for("kraken", {"SEXTANT_KRAKEN_API_KEY": "key-value"})

    assert not resolved.is_complete
    assert resolved.secret_values == ("key-value",)


def test_an_empty_string_is_treated_as_absent() -> None:
    resolved = credentials_for("kraken", {"SEXTANT_KRAKEN_API_KEY": ""})

    assert resolved.api_key is None
    assert not resolved.is_complete


def test_credentials_fall_back_to_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEXTANT_KRAKEN_API_KEY", "from-environ")
    monkeypatch.setenv("SEXTANT_KRAKEN_API_SECRET", "also-from-environ")

    resolved = credentials_for("kraken")

    assert resolved.is_complete


def test_the_repr_never_renders_the_secret() -> None:
    """A credential reaches a log or a traceback eventually. It must be inert there."""
    resolved = Credentials(venue="kraken", api_key="super-secret", api_secret="also-secret")

    assert "super-secret" not in repr(resolved)
    assert "super-secret" not in str(resolved)
    assert "complete" in repr(resolved)
    assert "incomplete" in repr(Credentials(venue="kraken", api_key=None, api_secret=None))


def test_a_missing_dotenv_file_is_not_an_error(tmp_path: Path) -> None:
    """BACKTEST must run on a machine that has never held a credential."""
    assert load_dotenv(tmp_path / "nothing-here") == 0


def test_a_dotenv_file_populates_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                'SEXTANT_KRAKEN_API_KEY="quoted-key"',
                "SEXTANT_KRAKEN_API_SECRET='single-quoted'",
                "not-an-assignment",
                "  SEXTANT_ALLOW_LIVE = 0  ",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("SEXTANT_KRAKEN_API_KEY", raising=False)

    loaded = load_dotenv(target)

    assert loaded == 3
    assert os.environ["SEXTANT_KRAKEN_API_KEY"] == "quoted-key"
    assert os.environ["SEXTANT_KRAKEN_API_SECRET"] == "single-quoted"
    assert os.environ["SEXTANT_ALLOW_LIVE"] == "0"


def test_an_exported_variable_beats_the_file_unless_override_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale file must not silently replace what the operator just exported."""
    target = tmp_path / ".env"
    target.write_text("SEXTANT_KRAKEN_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SEXTANT_KRAKEN_API_KEY", "from-shell")

    assert load_dotenv(target) == 0
    assert os.environ["SEXTANT_KRAKEN_API_KEY"] == "from-shell"

    assert load_dotenv(target, override=True) == 1
    assert os.environ["SEXTANT_KRAKEN_API_KEY"] == "from-file"


def test_the_default_path_is_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("SEXTANT_KRAKEN_API_KEY=cwd-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEXTANT_KRAKEN_API_KEY", raising=False)

    assert load_dotenv() == 1
    assert os.environ["SEXTANT_KRAKEN_API_KEY"] == "cwd-key"

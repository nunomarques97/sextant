"""Credential resolution.

Keys come from the process environment or from a git-ignored ``.env`` file, and
from nowhere else. They are never read from a YAML file, never written to a log
and never carried in a report.

The variable names follow one generic pattern, ``SEXTANT_<VENUE>_API_KEY`` and
``SEXTANT_<VENUE>_API_SECRET``, so adding a venue needs no code change here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sextant.app.settings import ENV_PREFIX

DOTENV_FILENAME = ".env"


@dataclass(frozen=True, slots=True)
class Credentials:
    """An API key pair for one venue. Never rendered in full."""

    venue: str
    api_key: str | None
    api_secret: str | None

    @property
    def is_complete(self) -> bool:
        """Whether both halves of the credential are present."""
        return bool(self.api_key) and bool(self.api_secret)

    @property
    def secret_values(self) -> tuple[str, ...]:
        """The raw values, for registration with the log redactor only."""
        return tuple(value for value in (self.api_key, self.api_secret) if value)

    def __repr__(self) -> str:
        state = "complete" if self.is_complete else "incomplete"
        return f"Credentials(venue={self.venue!r}, {state})"

    __str__ = __repr__


def key_variable(venue_name: str) -> str:
    """The environment variable holding the API key for ``venue_name``."""
    return f"{ENV_PREFIX}{venue_name.upper()}_API_KEY"


def secret_variable(venue_name: str) -> str:
    """The environment variable holding the API secret for ``venue_name``."""
    return f"{ENV_PREFIX}{venue_name.upper()}_API_SECRET"


def load_dotenv(path: Path | None = None, *, override: bool = False) -> int:
    """Load ``KEY=value`` lines from a git-ignored ``.env`` into the environment.

    Absent file is not an error: BACKTEST must run with no ``.env`` at all.
    Returns how many variables were set.
    """
    target = path or (Path.cwd() / DOTENV_FILENAME)
    if not target.is_file():
        return 0
    loaded = 0
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and (override or name not in os.environ):
            os.environ[name] = value
            loaded += 1
    return loaded


def credentials_for(venue_name: str, env: Mapping[str, str] | None = None) -> Credentials:
    """Read the credential pair for one venue out of the environment."""
    source = env if env is not None else os.environ
    return Credentials(
        venue=venue_name,
        api_key=source.get(key_variable(venue_name)) or None,
        api_secret=source.get(secret_variable(venue_name)) or None,
    )

"""Loading and merging the configuration layers.

``config/base.yaml`` is the shared ground. A profile file overlays it. The
environment overlays that. Nothing else is consulted, and nothing is silently
defaulted: a key that is not recognised stops startup with the key's name in the
message.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path

import yaml

from sextant.app.settings import (
    ALLOW_LIVE_ENV_VAR,
    ALLOW_LIVE_EXPECTED_VALUE,
    PROFILE_ENV_VAR,
    Settings,
)
from sextant.domain.errors import SextantError
from sextant.domain.mode import RunMode

BASE_CONFIG_FILENAME = "base.yaml"
CONFIG_DIR_ENV_VAR = "SEXTANT_CONFIG_DIR"

#: The profile files that may be layered on top of base.yaml. A profile is not
#: the same thing as a mode: the profile chooses the file, the file states the
#: mode. An unset profile resolves to "backtest", never to anything else.
KNOWN_PROFILES: frozenset[str] = frozenset({"backtest", "paper", "live"})
DEFAULT_PROFILE = "backtest"


class ConfigError(SextantError):
    """Configuration could not be loaded or is not valid."""


class UnknownConfigKey(ConfigError):
    """A configuration file declared a key the settings model does not define."""

    def __init__(self, source: Path, keys: frozenset[str], known: frozenset[str]) -> None:
        self.source = source
        self.keys = keys
        super().__init__(
            f"{source} declares unknown top-level key(s): {', '.join(sorted(keys))}. "
            f"Known keys: {', '.join(sorted(known))}."
        )


class LiveModeNotAuthorised(SextantError):
    """LIVE was requested without the separate environment switch."""

    def __init__(self) -> None:
        super().__init__(
            f"Refusing to start in LIVE mode: {ALLOW_LIVE_ENV_VAR} is not set to "
            f"{ALLOW_LIVE_EXPECTED_VALUE!r}. LIVE requires all three of: mode: live in "
            f"configuration, {ALLOW_LIVE_ENV_VAR}={ALLOW_LIVE_EXPECTED_VALUE} in the "
            "process environment, and a passing preflight check. Set the environment "
            "variable deliberately, in the shell that starts the process."
        )


def default_config_dir() -> Path:
    """Locate the ``config`` directory.

    Order: the ``SEXTANT_CONFIG_DIR`` environment variable, then the nearest
    ancestor of this package that contains ``config/base.yaml``, then
    ``./config`` relative to the working directory.
    """
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config"
        if (candidate / BASE_CONFIG_FILENAME).is_file():
            return candidate
    return Path.cwd() / "config"


def resolve_profile(profile: str | None = None) -> str:
    """Decide which profile file to layer on.

    An explicit argument wins, then ``SEXTANT_PROFILE``, then ``backtest``.
    There is no branch here in which an unset profile resolves to ``live``.
    """
    chosen = profile or os.environ.get(PROFILE_ENV_VAR) or DEFAULT_PROFILE
    chosen = chosen.strip().lower()
    if chosen not in KNOWN_PROFILES:
        raise ConfigError(
            f"Unknown profile {chosen!r}. Known profiles: {', '.join(sorted(KNOWN_PROFILES))}."
        )
    return chosen


def _read_yaml(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
    return {str(key): value for key, value in loaded.items()}


def _deep_merge(
    base: Mapping[str, object],
    overlay: Mapping[str, object],
) -> dict[str, object]:
    """Overlay wins, except that two mappings are merged rather than replaced."""
    merged: dict[str, object] = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(
                {str(k): v for k, v in existing.items()},
                {str(k): v for k, v in value.items()},
            )
        else:
            merged[key] = value
    return merged


def _reject_unknown_keys(source: Path, data: Mapping[str, object]) -> None:
    known = frozenset(Settings.model_fields)
    unknown = frozenset(data) - known
    if unknown:
        raise UnknownConfigKey(source, unknown, known)


def load_settings(
    *,
    profile: str | None = None,
    config_dir: Path | None = None,
) -> Settings:
    """Assemble the settings for one run from all three layers."""
    directory = config_dir or default_config_dir()
    chosen_profile = resolve_profile(profile)

    base_path = directory / BASE_CONFIG_FILENAME
    profile_path = directory / f"{chosen_profile}.yaml"

    base_data = _read_yaml(base_path)
    _reject_unknown_keys(base_path, base_data)
    profile_data = _read_yaml(profile_path)
    _reject_unknown_keys(profile_path, profile_data)

    merged: MutableMapping[str, object] = _deep_merge(base_data, profile_data)
    return _construct(merged)


def _construct(values: Mapping[str, object]) -> Settings:
    """Build ``Settings`` from the merged YAML mapping.

    The indirection through a ``Callable`` is deliberate and is not a way of
    silencing a real error. ``BaseSettings.__init__`` genuinely accepts
    ``**values`` at runtime - that is how pydantic-settings layers init values
    underneath the environment - but mypy synthesises a strictly typed
    ``__init__`` from the field annotations, which no mapping can satisfy.
    Widening to the real signature keeps the call honest without an ``Any``, a
    ``cast()`` or a ``# type: ignore``. Validation is unaffected: an unknown or
    mistyped key still fails here, loudly.
    """
    factory: Callable[..., Settings] = Settings
    return factory(**values)


def live_mode_authorised() -> bool:
    """Whether the separate live-trading switch is set in the process environment."""
    return os.environ.get(ALLOW_LIVE_ENV_VAR, "") == ALLOW_LIVE_EXPECTED_VALUE


def require_live_authorisation(mode: RunMode) -> None:
    """Hard startup gate. Raises unless LIVE has been authorised out-of-band.

    Any mode other than LIVE passes untouched: this gate only ever blocks.
    """
    if mode is RunMode.LIVE and not live_mode_authorised():
        raise LiveModeNotAuthorised

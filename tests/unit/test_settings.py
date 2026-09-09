"""Configuration layering, mode resolution and the live gate.

The property that matters most here is negative: there is no path through this
code in which an unset or partial configuration ends up in LIVE.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sextant.app.config import (
    ConfigError,
    LiveModeNotAuthorised,
    UnknownConfigKey,
    load_settings,
    require_live_authorisation,
    resolve_profile,
)
from sextant.app.settings import ALLOW_LIVE_ENV_VAR, PROFILE_ENV_VAR
from sextant.domain.capability import Capability
from sextant.domain.mode import RunMode
from sextant.domain.time import Timeframe
from tests.conftest import MINIMAL_BASE_CONFIG, write_config

# --------------------------------------------------------------------------
# Precedence: base -> profile -> environment
# --------------------------------------------------------------------------


def test_base_values_are_used_when_nothing_overrides_them(config_dir: Path) -> None:
    settings = load_settings(profile="backtest", config_dir=config_dir)
    assert settings.run.timeframe is Timeframe.H1
    assert settings.run.seed == 7


def test_the_profile_overrides_the_base(tmp_path: Path) -> None:
    directory = write_config(
        tmp_path,
        profiles={"paper": {"run": {"mode": "paper", "venue": "binance", "timeframe": "15m"}}},
    )
    settings = load_settings(profile="paper", config_dir=directory)
    assert settings.run.mode is RunMode.PAPER
    assert settings.run.venue == "binance"
    assert settings.run.timeframe is Timeframe.M15


def test_the_environment_overrides_the_profile(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEXTANT_RUN__TIMEFRAME", "4h")
    monkeypatch.setenv("SEXTANT_RUN__SEED", "999")
    settings = load_settings(profile="backtest", config_dir=config_dir)
    assert settings.run.timeframe is Timeframe.H4
    assert settings.run.seed == 999


def test_full_precedence_chain_in_one_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """base supplies seed, profile overrides timeframe, environment overrides venue."""
    directory = write_config(
        tmp_path,
        profiles={"backtest": {"run": {"mode": "backtest", "venue": "kraken", "timeframe": "1d"}}},
    )
    monkeypatch.setenv("SEXTANT_RUN__VENUE", "binance")
    settings = load_settings(profile="backtest", config_dir=directory)

    assert settings.run.seed == 7  # from base.yaml
    assert settings.run.timeframe is Timeframe.D1  # from the profile
    assert settings.run.venue == "binance"  # from the environment


# --------------------------------------------------------------------------
# Failing loudly
# --------------------------------------------------------------------------


def test_an_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    polluted = dict(MINIMAL_BASE_CONFIG)
    polluted["risk_limits"] = {"max_drawdown": 0.1}
    directory = write_config(tmp_path, base=polluted)

    with pytest.raises(UnknownConfigKey, match="risk_limits"):
        load_settings(profile="backtest", config_dir=directory)


def test_an_unknown_key_inside_a_section_is_rejected(tmp_path: Path) -> None:
    directory = write_config(
        tmp_path,
        profiles={"backtest": {"run": {"mode": "backtest", "venue": "kraken", "levrage": 3}}},
    )
    with pytest.raises(ValueError, match="levrage"):
        load_settings(profile="backtest", config_dir=directory)


def test_a_missing_required_key_is_rejected(tmp_path: Path) -> None:
    directory = write_config(tmp_path, profiles={"backtest": {"run": {"mode": "backtest"}}})
    with pytest.raises(ValueError, match="venue"):
        load_settings(profile="backtest", config_dir=directory)


def test_a_missing_config_file_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(dict(MINIMAL_BASE_CONFIG)), encoding="utf-8")
    with pytest.raises(ConfigError, match="not found"):
        load_settings(profile="paper", config_dir=tmp_path)


def test_an_unknown_profile_is_rejected() -> None:
    with pytest.raises(ConfigError, match="Unknown profile"):
        resolve_profile("production")


# --------------------------------------------------------------------------
# The default is BACKTEST, always
# --------------------------------------------------------------------------


def test_an_unset_profile_resolves_to_backtest() -> None:
    assert resolve_profile(None) == "backtest"


def test_the_profile_environment_variable_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROFILE_ENV_VAR, "paper")
    assert resolve_profile(None) == "paper"


def test_a_configuration_that_states_no_mode_resolves_to_backtest(tmp_path: Path) -> None:
    """The model default, exercised through a real config that omits the key."""
    directory = write_config(tmp_path, profiles={"backtest": {"run": {"venue": "kraken"}}})
    settings = load_settings(profile="backtest", config_dir=directory)
    assert settings.run.mode is RunMode.BACKTEST


def test_the_run_mode_enum_default_is_backtest() -> None:
    assert RunMode.default() is RunMode.BACKTEST
    assert RunMode.default().touches_real_money is False


def test_an_unrecognised_mode_string_is_an_error_rather_than_a_guess(tmp_path: Path) -> None:
    directory = write_config(
        tmp_path, profiles={"backtest": {"run": {"mode": "prod", "venue": "kraken"}}}
    )
    with pytest.raises(ValueError, match="mode"):
        load_settings(profile="backtest", config_dir=directory)


# --------------------------------------------------------------------------
# The live gate
# --------------------------------------------------------------------------


def test_live_without_the_environment_switch_raises_at_startup() -> None:
    with pytest.raises(LiveModeNotAuthorised) as raised:
        require_live_authorisation(RunMode.LIVE)

    message = str(raised.value)
    assert ALLOW_LIVE_ENV_VAR in message
    assert "preflight" in message


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "TRUE", " 1"])
def test_only_the_exact_value_one_authorises_live(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ALLOW_LIVE_ENV_VAR, value)
    with pytest.raises(LiveModeNotAuthorised):
        require_live_authorisation(RunMode.LIVE)


def test_the_switch_authorises_live_when_set_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_LIVE_ENV_VAR, "1")
    require_live_authorisation(RunMode.LIVE)


@pytest.mark.parametrize("mode", [RunMode.BACKTEST, RunMode.PAPER])
def test_the_gate_never_blocks_a_non_live_mode(mode: RunMode) -> None:
    require_live_authorisation(mode)


def test_the_live_switch_cannot_be_set_from_a_configuration_file(tmp_path: Path) -> None:
    """It is not a settings field, so a YAML file has no way to turn live on."""
    polluted = dict(MINIMAL_BASE_CONFIG)
    polluted["allow_live"] = True
    directory = write_config(tmp_path, base=polluted)

    with pytest.raises(UnknownConfigKey, match="allow_live"):
        load_settings(profile="backtest", config_dir=directory)


# --------------------------------------------------------------------------
# Exchanges as peers
# --------------------------------------------------------------------------


def test_exchanges_are_configured_as_independent_peers(config_dir: Path) -> None:
    settings = load_settings(profile="backtest", config_dir=config_dir)
    assert set(settings.exchanges) == {"kraken", "binance"}
    for name in settings.exchanges:
        assert settings.exchange(name).enabled is True


def test_jurisdiction_is_resolved_from_data_not_from_the_venue_name(config_dir: Path) -> None:
    settings = load_settings(profile="backtest", config_dir=config_dir)
    for name in settings.exchanges:
        allowed = settings.jurisdiction_for(name).allowed_capabilities
        assert Capability.FUTURES_TRADING not in allowed
        assert Capability.PUBLIC_MARKET_DATA in allowed

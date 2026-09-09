"""End-to-end startup, through the real configuration in ``config/``.

The acceptance property being demonstrated: a clean machine with no ``.env``
and no credentials in the environment runs a backtest to completion, and cannot
be talked into LIVE.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sextant.app.cli import EXIT_OK, EXIT_STARTUP_REFUSED, main
from sextant.app.config import LiveModeNotAuthorised
from sextant.app.preflight import PreflightFailed
from sextant.app.settings import ALLOW_LIVE_ENV_VAR
from sextant.app.startup import prepare
from sextant.domain.mode import RunMode

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_CONFIG = REPO_ROOT / "config"


@pytest.fixture(autouse=True)
def no_dotenv_in_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a directory with no .env, so nothing is loaded implicitly."""
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / ".env").exists()


def test_backtest_runs_to_completion_with_no_credentials_at_all(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert not any(name.startswith("SEXTANT_") for name in os.environ)

    exit_code = main(["--config-dir", str(PRODUCTION_CONFIG), "run"])

    assert exit_code == EXIT_OK
    assert "backtest" in capsys.readouterr().out


def test_status_reports_the_resolved_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--config-dir", str(PRODUCTION_CONFIG), "status"])

    output = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "mode:      backtest" in output
    assert "venue:     kraken" in output
    assert "preflight: ok" in output


def test_an_unset_profile_starts_in_backtest_not_live() -> None:
    result = prepare(config_dir=PRODUCTION_CONFIG)
    assert result.settings.run.mode is RunMode.BACKTEST
    assert result.metadata.profile == "backtest"


def test_paper_starts_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--config-dir", str(PRODUCTION_CONFIG), "--profile", "paper", "run"])
    assert exit_code == EXIT_OK
    assert "paper" in capsys.readouterr().out


def test_live_without_the_switch_refuses_to_start() -> None:
    with pytest.raises(LiveModeNotAuthorised):
        prepare(profile="live", config_dir=PRODUCTION_CONFIG)


def test_live_without_the_switch_exits_with_a_clear_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--config-dir", str(PRODUCTION_CONFIG), "--profile", "live", "run"])

    assert exit_code == EXIT_STARTUP_REFUSED
    assert ALLOW_LIVE_ENV_VAR in capsys.readouterr().err


def test_live_with_the_switch_still_fails_preflight_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch is necessary, not sufficient. No withdrawal probe exists yet."""
    monkeypatch.setenv(ALLOW_LIVE_ENV_VAR, "1")

    with pytest.raises(PreflightFailed) as raised:
        prepare(profile="live", config_dir=PRODUCTION_CONFIG)

    failed = {check.name for check in raised.value.report.failures}
    assert "credentials" in failed


def test_the_run_seed_is_recorded_in_metadata_and_drives_the_generator() -> None:
    first = prepare(config_dir=PRODUCTION_CONFIG)
    second = prepare(config_dir=PRODUCTION_CONFIG)

    assert first.metadata.seed == second.metadata.seed
    assert first.rng.random() == second.rng.random()
    assert first.metadata.run_id != second.metadata.run_id


def test_the_backtest_clock_is_simulated_and_the_engine_cannot_tell() -> None:
    result = prepare(config_dir=PRODUCTION_CONFIG)
    first = result.clock.now()
    second = result.clock.now()
    assert first == second, "a simulated clock must not advance on its own"


def test_an_absent_dotenv_file_is_not_an_error() -> None:
    result = prepare(config_dir=PRODUCTION_CONFIG, dotenv_path=Path("does-not-exist.env"))
    assert result.settings.run.mode is RunMode.BACKTEST

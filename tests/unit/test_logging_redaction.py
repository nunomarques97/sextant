"""Secret redaction in the logging stack.

A key that reaches a log file is a key that has to be rotated. The filter is
tested through the real logging stack rather than by calling ``redact``
directly, because the failure mode being prevented is a filter that is
correctly written and incorrectly installed.
"""

from __future__ import annotations

import io
import json
import logging

from sextant.app.logging_setup import (
    REDACTION_PLACEHOLDER,
    RunContext,
    SecretRegistry,
    configure_logging,
    secret_values_from_env,
)

SECRET = "kR4k3n-s3cr3t-value-9f2b"


def emit(stream: io.StringIO) -> list[dict[str, object]]:
    """Parse the JSON records written to ``stream``."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_a_secret_passed_to_the_logger_does_not_appear_in_the_record() -> None:
    stream = io.StringIO()
    registry = SecretRegistry()
    configure_logging(
        context=RunContext(run_id="r1", mode="paper", venue="kraken"),
        stream=stream,
        secrets=registry,
        secret_values=[SECRET],
    )

    logging.getLogger("sextant.app.test").info("authenticating with %s", SECRET)

    raw = stream.getvalue()
    assert SECRET not in raw
    assert REDACTION_PLACEHOLDER in raw


def test_redaction_also_applies_to_records_from_child_loggers() -> None:
    """The filter lives on the handler, so a child logger cannot bypass it."""
    stream = io.StringIO()
    configure_logging(
        context=RunContext(run_id="r2", mode="paper"),
        stream=stream,
        secrets=SecretRegistry(),
        secret_values=[SECRET],
    )

    logging.getLogger("sextant.adapters.exchanges.kraken.client").warning(
        "retrying with key %s", SECRET
    )

    assert SECRET not in stream.getvalue()


def test_a_secret_in_a_structured_extra_is_redacted_too() -> None:
    stream = io.StringIO()
    configure_logging(
        context=RunContext(run_id="r3", mode="live", venue="kraken"),
        stream=stream,
        secrets=SecretRegistry(),
        secret_values=[SECRET],
    )

    logging.getLogger("sextant.app.test").info("request", extra={"payload": f"sig={SECRET}"})

    records = emit(stream)
    assert SECRET not in stream.getvalue()
    assert records[0]["payload"] == f"sig={REDACTION_PLACEHOLDER}"


def test_a_field_whose_name_looks_like_a_credential_is_redacted_by_name() -> None:
    stream = io.StringIO()
    configure_logging(
        context=RunContext(run_id="r4", mode="paper"),
        stream=stream,
        secrets=SecretRegistry(),
    )

    logging.getLogger("sextant.app.test").info(
        "call", extra={"api_secret": "never-registered-anywhere"}
    )

    records = emit(stream)
    assert records[0]["api_secret"] == REDACTION_PLACEHOLDER
    assert "never-registered-anywhere" not in stream.getvalue()


def test_every_record_carries_run_id_mode_and_venue() -> None:
    stream = io.StringIO()
    configure_logging(
        context=RunContext(run_id="run-123", mode="backtest", venue="kraken"),
        stream=stream,
        secrets=SecretRegistry(),
    )

    logging.getLogger("sextant.engine.test").info("tick")

    record = emit(stream)[0]
    assert record["run_id"] == "run-123"
    assert record["mode"] == "backtest"
    assert record["venue"] == "kraken"


def test_configuring_twice_does_not_duplicate_output() -> None:
    stream = io.StringIO()
    for _ in range(3):
        configure_logging(
            context=RunContext(run_id="r5", mode="backtest"),
            stream=stream,
            secrets=SecretRegistry(),
        )

    logging.getLogger("sextant.app.test").info("once")

    assert len(emit(stream)) == 1


def test_very_short_values_are_not_registered_as_secrets() -> None:
    registry = SecretRegistry()
    registry.register("ab", None, "")
    assert registry.redact("ab is fine") == "ab is fine"


def test_credential_like_environment_values_are_discovered_generically() -> None:
    found = secret_values_from_env(
        {
            "SEXTANT_KRAKEN_API_KEY": "key-value-long-enough",
            "SEXTANT_KRAKEN_API_SECRET": "secret-value-long-enough",
            "PATH": "/usr/bin",
        }
    )
    assert set(found) == {"key-value-long-enough", "secret-value-long-enough"}

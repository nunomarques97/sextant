"""Structured logging with secret redaction.

Every record carries ``run_id`` and ``mode``, and ``venue`` where one applies,
so a log line can be traced back to the exact run that produced it.

Redaction is a filter rather than a convention. Anything registered as a secret
is replaced in the rendered message and in the record's extras before a handler
ever sees it, so a stray ``logger.info("using key %s", key)`` cannot leak.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import IO

REDACTION_PLACEHOLDER = "***REDACTED***"

#: Keys whose values are redacted regardless of content, because a field called
#: "api_secret" is a secret even when its value has not been registered.
SENSITIVE_KEY_TOKENS: frozenset[str] = frozenset(
    {"secret", "token", "password", "passphrase", "api_key", "apikey", "signature", "private"}
)

_RESERVED_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


@dataclass(slots=True)
class SecretRegistry:
    """The values that must never appear in a log record.

    Short values are ignored: redacting a two-character string would blank out
    unrelated text and make logs useless without making anything safer.
    """

    minimum_length: int = 6
    _secrets: set[str] = field(default_factory=set)

    def register(self, *values: str | None) -> None:
        """Register secret values. Empty and very short values are ignored."""
        for value in values:
            if value and len(value) >= self.minimum_length:
                self._secrets.add(value)

    def clear(self) -> None:
        """Forget every registered secret."""
        self._secrets.clear()

    def redact(self, text: str) -> str:
        """Replace every registered secret occurring in ``text``."""
        redacted = text
        for secret in sorted(self._secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, REDACTION_PLACEHOLDER)
        return redacted


#: Process-wide registry. Populated at startup from the resolved credentials.
SECRETS = SecretRegistry()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_KEY_TOKENS)


class RedactionFilter(logging.Filter):
    """Render the message early and strip every known secret from it."""

    def __init__(self, secrets: SecretRegistry) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place. Always returns True; nothing is dropped."""
        record.msg = self._secrets.redact(record.getMessage())
        record.args = ()
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_RECORD_ATTRS:
                continue
            if _is_sensitive_key(key):
                record.__dict__[key] = REDACTION_PLACEHOLDER
            elif isinstance(value, str):
                record.__dict__[key] = self._secrets.redact(value)
        return True


@dataclass(frozen=True, slots=True)
class RunContext:
    """The identity of the run, attached to every record."""

    run_id: str
    mode: str
    venue: str | None = None


class RunContextFilter(logging.Filter):
    """Attach run identity to every record."""

    def __init__(self, context: RunContext) -> None:
        super().__init__()
        self._context = context

    def filter(self, record: logging.LogRecord) -> bool:
        """Add run_id, mode and venue. Always returns True."""
        record.run_id = self._context.run_id
        record.mode = self._context.mode
        if self._context.venue is not None:
            record.venue = self._context.venue
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Render the record as compact JSON."""
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(
    *,
    context: RunContext,
    level: str = "INFO",
    as_json: bool = True,
    stream: IO[str] | None = None,
    secrets: SecretRegistry | None = None,
    secret_values: Iterable[str | None] = (),
) -> logging.Logger:
    """Install the sextant logging stack and return the root sextant logger.

    Calling this twice replaces the previous configuration rather than stacking
    handlers, so repeated calls in tests do not multiply output.
    """
    registry = secrets if secrets is not None else SECRETS
    registry.register(*secret_values)

    logger = logging.getLogger("sextant")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    for existing_filter in list(logger.filters):
        logger.removeFilter(existing_filter)

    handler = logging.StreamHandler(stream) if stream is not None else logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if as_json
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(run_id)s] %(message)s")
    )

    # The filters go on the handler, not on the logger. A logger's own filters
    # are skipped for records emitted by its children, so redaction attached to
    # the logger would silently not apply to ``sextant.app``, ``sextant.engine``
    # and every other module that actually logs.
    handler.addFilter(RunContextFilter(context))
    handler.addFilter(RedactionFilter(registry))
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def secret_values_from_env(env: Mapping[str, str]) -> tuple[str, ...]:
    """Every environment value whose key looks like a credential."""
    return tuple(value for key, value in env.items() if _is_sensitive_key(key) and value)

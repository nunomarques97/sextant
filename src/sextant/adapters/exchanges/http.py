"""Venue-neutral HTTP plumbing for exchange adapters.

Three outcomes, and they are never confused with one another:

* **success** - the venue answered. The answer may legitimately be empty, and an
  empty answer is returned as an empty payload, not as an error;
* **rejected** - the venue answered and refused. An unknown symbol lands here.
  Not retryable, and emphatically not an empty result;
* **unavailable** - the venue did not usefully answer: a timeout, a 429, a 5xx.
  Retryable, raised as ``VenueUnavailable``.

The distinction is the whole point. A pipeline that cannot tell "this instrument
has no bars" from "this request failed" builds a survivorship-biased dataset and
reports it as complete, which is the single failure mode this project is least
able to detect after the fact.

Nothing here knows the name of any venue, and nothing here signs a request or
carries a credential.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import httpx

from sextant.domain.availability import VenueStatus, VenueUnavailable
from sextant.domain.errors import SextantError
from sextant.domain.time import Timestamp
from sextant.domain.venue import Venue

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

#: Statuses that mean "ask again later" rather than "you asked wrongly".
RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


class VenueRequestRejected(SextantError):
    """The venue answered and refused the request. Not retryable, not empty.

    An unknown symbol, a malformed parameter or a forbidden endpoint arrives
    here. The caller must not treat it as "no data": those are different facts
    with different consequences for a historical dataset.
    """

    retryable = False

    def __init__(self, venue: Venue, url: str, status: int, detail: str) -> None:
        self.venue = venue
        self.url = url
        self.status = status
        self.detail = detail
        super().__init__(f"{venue.name} rejected {url} with status {status}: {detail}")


class MalformedVenuePayload(SextantError):
    """The venue answered with a shape we do not understand.

    Deliberately distinct from an outage. Silently coercing an unexpected shape
    into an empty list is how a parser bug becomes a data-quality problem nobody
    can see six months later.
    """

    retryable = False


def utc_now() -> Timestamp:
    """Wall-clock read. Legal here, and only here: this is the adapter layer."""
    return Timestamp(datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One attempted call, recorded so a claim in a report can be traced to it."""

    venue: str
    method: str
    url: str
    params: tuple[tuple[str, str], ...]
    requested_at: Timestamp
    responded_at: Timestamp | None
    status: int | None
    payload_bytes: int
    attempt: int
    outcome: str
    detail: str = ""

    def as_json(self) -> dict[str, JsonValue]:
        """A flat, serialisable view for the journal file."""
        return {
            "venue": self.venue,
            "method": self.method,
            "url": self.url,
            "params": dict(self.params),
            "requested_at": self.requested_at.isoformat(),
            "responded_at": None if self.responded_at is None else self.responded_at.isoformat(),
            "status": self.status,
            "payload_bytes": self.payload_bytes,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass(slots=True)
class RequestJournal:
    """Append-only record of every call made, successful or not."""

    records: list[RequestRecord] = field(default_factory=list)

    def append(self, record: RequestRecord) -> None:
        """Record one attempt."""
        self.records.append(record)

    def write_jsonl(self, path: Path) -> int:
        """Persist the journal as one JSON object per line. Returns lines written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in self.records:
                handle.write(json.dumps(record.as_json(), sort_keys=True) + "\n")
        return len(self.records)

    @property
    def successes(self) -> int:
        """How many attempts returned a payload."""
        return sum(1 for record in self.records if record.outcome == "ok")


@dataclass(slots=True)
class RateLimiter:
    """A token bucket, configured from the venue's published limits.

    Monotonic rather than wall-clock: a clock step backwards during a long fetch
    would otherwise hand out a burst of free tokens at exactly the moment the
    venue is least willing to receive them.
    """

    rate_per_second: float
    burst: float = 1.0
    _tokens: float = field(default=0.0, init=False)
    _last: float = field(default_factory=time.monotonic, init=False)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._tokens = self.burst

    def acquire(self, cost: float = 1.0) -> float:
        """Block until ``cost`` tokens are available. Returns seconds waited."""
        waited = 0.0
        while True:
            now = time.monotonic()
            refill = (now - self._last) * self.rate_per_second
            self._tokens = min(self.burst, self._tokens + refill)
            self._last = now
            if self._tokens >= cost:
                self._tokens -= cost
                return waited
            sleep_for = (cost - self._tokens) / self.rate_per_second
            time.sleep(sleep_for)
            waited += sleep_for


class HttpTransport:
    """A rate-limited, journalling, retrying GET client for one venue's host."""

    def __init__(
        self,
        venue: Venue,
        base_url: str,
        limiter: RateLimiter,
        journal: RequestJournal,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 5,
        timeout_seconds: float = 30.0,
        backoff_base_seconds: float = 0.5,
        backoff_cap_seconds: float = 20.0,
        user_agent: str = "sextant-research/0.1 (public market data only)",
    ) -> None:
        self.venue = venue
        self.base_url = base_url.rstrip("/")
        self._limiter = limiter
        self._journal = journal
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_cap = backoff_cap_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
        )

    @property
    def journal(self) -> RequestJournal:
        """The journal this transport writes to."""
        return self._journal

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying connection pool, if we created it."""
        if self._owns_client:
            self._client.close()

    def get_text(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        cost: float = 1.0,
    ) -> str:
        """Fetch a resource as text, retrying transient failures."""
        return self._get(path, params or {}, cost=cost).text

    def get_bytes(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        cost: float = 1.0,
    ) -> bytes:
        """Fetch a resource as raw bytes, retrying transient failures.

        Exists because a venue may publish history as an archive rather than as
        JSON. Decoding it is the caller's problem; what belongs here is that
        the retry, rate-limit and journal behaviour is identical whatever the
        body turns out to be, so an archive fetch cannot quietly acquire its own
        weaker rules.
        """
        return self._get(path, params or {}, cost=cost).content

    def get_json(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        cost: float = 1.0,
    ) -> JsonValue:
        """Fetch a resource and parse it as JSON, retrying transient failures."""
        response = self._get(path, params or {}, cost=cost)
        try:
            payload: JsonValue = response.json()
        except ValueError as exc:
            raise MalformedVenuePayload(
                f"{self.venue.name} returned non-JSON from {response.url}: {exc}"
            ) from exc
        return payload

    def _get(self, path: str, params: Mapping[str, str], *, cost: float) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        recorded = tuple(sorted((key, str(value)) for key, value in params.items()))
        last_detail = "no attempt was made"

        for attempt in range(1, self._max_attempts + 1):
            self._limiter.acquire(cost)
            requested_at = utc_now()
            try:
                response = self._client.get(url, params=dict(params))
            except httpx.HTTPError as exc:
                last_detail = f"{type(exc).__name__}: {exc}"
                self._journal.append(
                    RequestRecord(
                        venue=self.venue.name,
                        method="GET",
                        url=url,
                        params=recorded,
                        requested_at=requested_at,
                        responded_at=None,
                        status=None,
                        payload_bytes=0,
                        attempt=attempt,
                        outcome="transport_error",
                        detail=last_detail,
                    )
                )
                self._sleep_before_retry(attempt, None)
                continue

            responded_at = utc_now()
            retryable = response.status_code in RETRYABLE_STATUSES
            rejected = not response.is_success and not retryable
            if response.is_success:
                outcome = "ok"
            elif retryable:
                outcome = "retryable"
            else:
                outcome = "rejected"
            detail = "" if response.is_success else response.text[:400]
            self._journal.append(
                RequestRecord(
                    venue=self.venue.name,
                    method="GET",
                    url=url,
                    params=recorded,
                    requested_at=requested_at,
                    responded_at=responded_at,
                    status=response.status_code,
                    payload_bytes=len(response.content),
                    attempt=attempt,
                    outcome=outcome,
                    detail=detail,
                )
            )

            if response.is_success:
                return response
            if rejected:
                raise VenueRequestRejected(self.venue, url, response.status_code, detail)
            last_detail = f"HTTP {response.status_code}: {detail}"
            self._sleep_before_retry(attempt, response)

        raise VenueUnavailable(
            self.venue,
            VenueStatus.UNAVAILABLE,
            f"{url} failed after {self._max_attempts} attempts. Last: {last_detail}",
        )

    def _sleep_before_retry(self, attempt: int, response: httpx.Response | None) -> None:
        """Wait before the next attempt, honouring ``Retry-After`` when offered."""
        if attempt >= self._max_attempts:
            return
        delay = min(self._backoff_cap, self._backoff_base * (2 ** (attempt - 1)))
        if response is not None:
            header = response.headers.get("Retry-After")
            if header is not None:
                try:
                    delay = max(delay, min(self._backoff_cap, float(header)))
                except ValueError:
                    delay = delay
        time.sleep(delay)


# ----------------------------------------------------------------------------
# Narrowing helpers.
#
# httpx hands back an untyped structure. Rather than sprinkling casts - which
# this project bans - every access goes through a helper that either produces
# the expected type or raises with enough context to find the offending field.
# ----------------------------------------------------------------------------


def as_mapping(value: JsonValue, *, context: str) -> Mapping[str, JsonValue]:
    """Narrow to an object, or raise naming the context."""
    if not isinstance(value, dict):
        raise MalformedVenuePayload(f"{context}: expected an object, got {type(value).__name__}")
    return value


def as_sequence(value: JsonValue, *, context: str) -> Sequence[JsonValue]:
    """Narrow to an array, or raise naming the context."""
    if not isinstance(value, list):
        raise MalformedVenuePayload(f"{context}: expected an array, got {type(value).__name__}")
    return value


def as_str(value: JsonValue, *, context: str) -> str:
    """Narrow to a string, or raise naming the context."""
    if not isinstance(value, str):
        raise MalformedVenuePayload(f"{context}: expected a string, got {type(value).__name__}")
    return value


def as_int(value: JsonValue, *, context: str) -> int:
    """Narrow to an integer, or raise naming the context."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedVenuePayload(f"{context}: expected an integer, got {type(value).__name__}")
    return value


def field_of(payload: Mapping[str, JsonValue], key: str, *, context: str) -> JsonValue:
    """Read a required key, or raise naming the context and the key."""
    if key not in payload:
        raise MalformedVenuePayload(f"{context}: missing required key {key!r}")
    return payload[key]

# ADR-0002: The exchange capability model

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** Product Owner / Tech Lead (decision), Developer (implementation)

## Context

Binance and Kraken are first-class, independent venues. Neither substitutes for
the other. Strategies must never depend on a specific exchange.

The naive way to express "Binance can do futures, Kraken's futures are a
separate product, and neither may offer either to a retail account in this
jurisdiction" is a conditional on the venue's name. That conditional then
multiplies: it appears in execution, in the data layer, in the strategy that
needs funding rates, and eventually in a backtest. At that point adding a third
venue means auditing the whole codebase, and "strategies are exchange-agnostic"
has become a slogan.

Three distinct questions get confused if they are not separated:

1. Can this exchange do this at all?
2. Is this particular account allowed to do it?
3. Is it permitted where the account holder lives?
4. Is the exchange responding right now?

The first three have the same shape and different sources. The fourth has a
completely different shape and is routinely conflated with them.

## Decision

**Capability is the intersection of three semi-static layers.**

| Layer | Answers | Source of truth | Populated by | Change frequency |
|---|---|---|---|---|
| Venue | Can the exchange do this at all? | The venue's public API documentation | The adapter, as a declared constant | Rarely; a venue release |
| Account | Does this account's tier, verification level and key permission allow it? | The venue's account settings and API key scopes | Configuration today; a runtime probe later | Occasionally; a tier or key change |
| Jurisdiction | Is it permitted for this country of residence? | Regulation and the venue's terms for that country | Configuration data, keyed by jurisdiction code | Unpredictably; a regulatory change |

`ExchangeClient.capabilities()` returns a `CapabilitySet` carrying all three and
exposing their intersection as `effective`. Callers ask
`Capability.SPOT_TRADING in client.capabilities()`. A single generic guard,
`CapabilitySet.require`, raises
`CapabilityNotAvailable(venue, capability, layer)` naming which of the three
layers denied it, in the fixed precedence venue -> account -> jurisdiction.

**Jurisdictional eligibility is data, never code.** It is configured under a
`jurisdictions:` block keyed by jurisdiction code, not by venue name. Two
reasons. First, it is the layer most likely to change without any code change:
regulations move, venues withdraw products from countries, and the account
holder can move. A rule that lives in code turns each of those into a release.
Second, keying it by venue name would reintroduce exactly the venue-name
branching the model exists to prevent, one level down.

**Runtime availability is deliberately not a capability.** Endpoint outages,
rate limits, maintenance windows and halted markets are modelled as a separate
`VenueHealth` concern that raises `VenueUnavailable`, a distinct retryable
error.

## Options considered

### Option A: Three intersecting layers plus separate availability (chosen)

**Pros:** each denial has a single, nameable cause, which makes diagnostics
honest; adding a venue is a declaration plus configuration; jurisdiction changes
are configuration edits; the retryable/permanent distinction is carried by the
type system rather than by a string.

**Cons:** three sources to keep current, and no mechanism yet for detecting that
one has gone stale. A capability set that is wrong in the permissive direction
produces a confusing venue-side rejection rather than a clean local one.

### Option B: One flat capability set per venue

**Pros:** simplest possible model; one thing to look up.

**Cons:** cannot distinguish "the exchange cannot do this" from "you are not
allowed to". That distinction is the difference between a permanent
architectural fact and a support ticket, and losing it makes every capability
failure look like the same undifferentiated wall. It also forces account and
jurisdiction facts back into the adapter, which is where venue branching starts.

### Option C: Capability includes runtime availability

**Pros:** one question to ask before doing anything; callers need only one
check.

**Cons:** this is the option worth arguing against explicitly, because it is
superficially attractive. Folding availability in means a two-minute outage is
indistinguishable from a permanent lack of support. The system would then either
retire a venue that is merely blinking, or retry forever against a venue that
genuinely cannot do the thing. It also makes `capabilities()` a network call
with a latency and a failure mode, which poisons every caller. The conditions
have different lifetimes, different sources and different correct responses, so
they get different types.

## Trade-off analysis

The model buys diagnosability at the cost of three things to maintain. That
trade is worth making because the alternative failure is silent: a flat model
does not report that it has lost information, it just gives worse answers
forever.

The precedence order for attributing a denial (venue, then account, then
jurisdiction) is a reporting choice, not a semantic one. The effective set is a
plain intersection and is order-independent. Precedence only decides which
single layer is named when more than one denies, and the ordering runs from the
most fundamental fact to the most changeable one.

## Consequences

**Easier:** a third venue is a package and a configuration block; a regulatory
change is a configuration edit; a diagnostic can say precisely why an operation
was refused; an outage and a restriction cannot be confused.

**Harder:** three layers must be kept current, and staleness detection does not
exist yet. Until the account probe is wired, the account layer is only as
accurate as what a human typed into `config/base.yaml`.

**To revisit:** the account layer should become a runtime probe reconciled
against configuration, reporting a disagreement rather than silently preferring
one. The lifecycle table in `docs/PHASE-0-FINDINGS.md` records what is still
missing per layer.

## Action items

1. [x] `Capability`, `CapabilityLayer`, `CapabilitySet`, `CapabilityNotAvailable`
2. [x] `VenueHealth` / `VenueUnavailable` as a separate, retryable concern
3. [x] Jurisdiction as configuration data keyed by jurisdiction code
4. [x] A test proving the guard is agnostic to venue identity
5. [ ] Account-capability runtime probe, reconciled against configuration
6. [ ] Staleness policy: how old a capability declaration may be before it is
       refused rather than trusted

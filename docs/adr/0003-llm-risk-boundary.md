# ADR-0003: The boundary between the LLM and deterministic code

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** Product Owner / Tech Lead (decision), Developer (implementation)

## Context

Claude is used in this system for non-latency-critical analysis: interpreting
context, classifying a market regime, suggesting which strategy fits and how
confident it is. That is a reasonable use of a language model.

Position size, leverage, maximum exposure, risk limits, stop-loss risk budget
and portfolio risk budget are a different kind of quantity. They are the
mechanism by which a wrong opinion becomes a survivable loss instead of a
terminal one. A model that can influence them can, in one confidently-worded
response, remove the protection that every other part of the system depends on.

The risk is not that a model is asked to size a position. Nobody would write
that on purpose. The risk is drift: a `notes` field that starts carrying
"suggest 3x", a `confidence` that gets multiplied into a size somewhere
downstream, a future session that adds `suggested_size` to the schema because it
seemed harmless and the docstring said "advisory".

## Decision

**The model may recommend:** direction or action, which strategy applies, a
confidence score, a regime classification, and free-form contextual
interpretation carried in an explanatory field.

**The model may not determine:** position size, leverage, maximum exposure,
risk limits, stop-loss risk budget, or portfolio risk budget. These belong
exclusively to deterministic code and the Risk Engine.

The flow is one-way and always passes through deterministic code:

```
LLM recommendation  ->  deterministic Risk Engine  ->  APPROVE | REDUCE | REJECT  ->  execution
```

There is no path from a model response to an order. Free-form output is recorded
for audit and is never parsed as an instruction.

**The constraint is enforced by schema, not by documentation.** Three
mechanisms, in `sextant.ports.llm`:

1. Every response schema sets `extra="forbid"`. A response that volunteers
   `position_size` fails validation; it is not read and then ignored, it is
   rejected.
2. `assert_no_risk_fields` runs at import over every shipped response schema and
   raises `RiskFieldInSchema` if any field name contains a forbidden token
   (`size`, `leverage`, `exposure`, `stop`, `risk`, `budget`, `notional`,
   `quantity`, `allocation`, `weight`, `margin`, `capital`, and their
   relatives). Adding such a field breaks the build at import time.
3. Tests assert both behaviours, including that a synthesised schema carrying
   `position_size` is rejected.

`DecisionRecord` keeps `llm_decision` and `risk_verdict` as separate fields, so
that disagreement between the model and deterministic risk can be measured
later rather than assumed away.

## Options considered

### Option A: Enforce by schema (chosen)

**Pros:** a value that cannot be expressed cannot be smuggled through. The rule
survives sessions that never read this document, which is the actual threat
model: guidance decays, a failing build does not. The token-based check catches
plausible variants nobody enumerated in advance.

**Cons:** the token list is a blocklist, and blocklists can be evaded by a
determined author (`amount`, `units`, `k`). It also produces occasional false
positives: a legitimately named field containing "risk" is refused. Both are
accepted; the check is a ratchet against drift, not a defence against sabotage,
and false positives fail loudly at import rather than silently at runtime.

### Option B: Enforce by documentation and code review

**Pros:** zero machinery; no false positives.

**Cons:** relies on every future session reading and honouring a rule. With one
developer and no shared session memory, this is the failure mode the whole
project is built to avoid. It also fails silently: nothing tells you the rule
was broken until a position is the wrong size.

### Option C: Allow the model to propose a size that the Risk Engine caps

**Pros:** superficially safe, since deterministic code still has the last word,
and it lets the model express conviction quantitatively.

**Cons:** an anchor is not a cap. Once a proposed size exists, it becomes the
default that risk logic adjusts rather than the input risk logic ignores, and
the burden quietly inverts from "justify this size" to "justify overriding it".
It also makes the audit trail ambiguous: a size that survived a cap is
indistinguishable from a size the Risk Engine chose. Confidence is already
available for expressing conviction, and unlike a size it is dimensionless and
cannot be mistaken for an order parameter.

## Trade-off analysis

The cost of enforcement is a blocklist with known limits and occasional
awkwardness in naming. The cost of non-enforcement is unbounded and arrives
without warning. The asymmetry is not close.

The remaining exposure is the `rationale` field, which is free text and could in
principle contain "size this at 3x". This is accepted deliberately: the field is
never parsed, its content reaches no numeric path, and removing it would remove
the audit trail that makes model performance measurable at all. The invariant is
that free-form text is recorded, never interpreted.

## Consequences

**Easier:** auditing what the model actually influenced; measuring
model-versus-risk disagreement; reasoning about worst-case model behaviour,
because the worst case is a bad direction at a size the model never saw.

**Harder:** a genuinely useful future signal that happens to be named with a
banned token needs renaming or an explicit, reviewed exception. The blocklist
will need occasional maintenance.

**To revisit:** if the Risk Engine ever gains a mechanism for accepting a
conviction-scaled size, it must take `confidence` and compute the size itself.
It must never accept a size.

## Action items

1. [x] `LLMAnalyst` port with `extra="forbid"` response schemas
2. [x] `assert_no_risk_fields` executed at import over every shipped schema
3. [x] Tests for payload rejection and for schema widening
4. [x] `DecisionRecord` keeps `llm_decision` and `risk_verdict` separate
5. [ ] When the Risk Engine is built, assert in test that no field of any LLM
       response type is reachable from a sizing calculation

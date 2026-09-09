"""LLM adapters. Not implemented in SEXTANT-001. No provider call exists yet.

An implementation of ``LLMAnalyst`` will live here. It parses provider output
into the pydantic schemas in ``sextant.ports.llm`` and nothing else: a response
that does not validate is discarded, never coerced, and never read as free text
for an instruction.

LLM usage is confined to non-latency-critical analysis. Nothing here sits on
the order path.
"""

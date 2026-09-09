"""Root of the exception hierarchy."""

from __future__ import annotations


class SextantError(Exception):
    """Base class for every error raised deliberately by this system."""


class DomainError(SextantError):
    """A domain invariant was violated."""

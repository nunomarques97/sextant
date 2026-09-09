"""Monetary value objects.

Every price, quantity, fee and PnL figure in this system is a ``Decimal``.
Binary floating point is rejected at construction rather than tolerated, because
a float that reaches a fee calculation is a silent, compounding accounting error
that no test downstream will reliably catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sextant.domain.errors import DomainError


class MoneyTypeError(DomainError):
    """A monetary value was constructed from something other than a Decimal."""

    def __init__(self, field: str, value: object) -> None:
        self.field = field
        self.value = value
        super().__init__(
            f"{field} must be a Decimal, got {type(value).__name__!r}. "
            "Floats are forbidden for monetary values; use Decimal('...') or .parse()."
        )


class MoneyValueError(DomainError):
    """A monetary value was constructed with an unusable Decimal."""


def require_decimal(value: object, *, field: str) -> Decimal:
    """Return ``value`` when it is a usable Decimal, otherwise raise.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` and would
    otherwise slip through any numeric-tower check.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise MoneyTypeError(field, value)
    if not value.is_finite():
        raise MoneyValueError(f"{field} must be finite, got {value}")
    return value


@dataclass(frozen=True, slots=True, order=True)
class Price:
    """A price expressed in the instrument's quote currency."""

    amount: Decimal

    def __post_init__(self) -> None:
        require_decimal(self.amount, field="Price.amount")
        if self.amount < 0:
            raise MoneyValueError(f"Price.amount must not be negative, got {self.amount}")

    @classmethod
    def parse(cls, text: str | int) -> Price:
        """Build a Price from an exact textual or integral representation."""
        return cls(Decimal(text))

    def __add__(self, other: Price) -> Price:
        return Price(self.amount + other.amount)

    def __sub__(self, other: Price) -> Price:
        return Price(self.amount - other.amount)

    def __mul__(self, quantity: Quantity) -> Notional:
        return Notional(self.amount * quantity.amount)


@dataclass(frozen=True, slots=True, order=True)
class Quantity:
    """A quantity of the instrument's base asset. Negative means short."""

    amount: Decimal

    def __post_init__(self) -> None:
        require_decimal(self.amount, field="Quantity.amount")

    @classmethod
    def parse(cls, text: str | int) -> Quantity:
        """Build a Quantity from an exact textual or integral representation."""
        return cls(Decimal(text))

    def __add__(self, other: Quantity) -> Quantity:
        return Quantity(self.amount + other.amount)

    def __sub__(self, other: Quantity) -> Quantity:
        return Quantity(self.amount - other.amount)

    def __neg__(self) -> Quantity:
        return Quantity(-self.amount)


@dataclass(frozen=True, slots=True, order=True)
class Notional:
    """A value in quote currency. Negative means a loss or an outflow."""

    amount: Decimal

    def __post_init__(self) -> None:
        require_decimal(self.amount, field="Notional.amount")

    @classmethod
    def parse(cls, text: str | int) -> Notional:
        """Build a Notional from an exact textual or integral representation."""
        return cls(Decimal(text))

    def __add__(self, other: Notional) -> Notional:
        return Notional(self.amount + other.amount)

    def __sub__(self, other: Notional) -> Notional:
        return Notional(self.amount - other.amount)

    def __neg__(self) -> Notional:
        return Notional(-self.amount)

"""Monetary and temporal invariants.

These are the two defects that are cheapest to prevent and most expensive to
find later: a float that reaches a fee calculation, and a naive datetime that
shifts a bar boundary by an hour twice a year.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sextant.domain.money import (
    MoneyTypeError,
    MoneyValueError,
    Notional,
    Price,
    Quantity,
)
from sextant.domain.time import NaiveDatetimeError, Timeframe, Timestamp


@pytest.mark.parametrize("value", [1.5, 0.1, 100.0])
def test_price_rejects_float_construction(value: float) -> None:
    with pytest.raises(MoneyTypeError):
        Price(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["1.5", 2, None, Decimal])
def test_price_rejects_non_decimal_construction(value: object) -> None:
    with pytest.raises(MoneyTypeError):
        Price(value)  # type: ignore[arg-type]


def test_quantity_rejects_bool_which_is_an_int_subclass() -> None:
    with pytest.raises(MoneyTypeError):
        Quantity(True)  # type: ignore[arg-type]


def test_notional_rejects_non_finite_decimal() -> None:
    with pytest.raises(MoneyValueError):
        Notional(Decimal("NaN"))


def test_price_rejects_negative_amount() -> None:
    with pytest.raises(MoneyValueError):
        Price(Decimal("-1"))


def test_decimal_precision_survives_addition() -> None:
    total = Price.parse("0.1") + Price.parse("0.2")
    assert total.amount == Decimal("0.3")
    assert str(total.amount) == "0.3"


def test_decimal_precision_survives_a_float_hostile_sum() -> None:
    running = Notional(Decimal("0"))
    for _ in range(10):
        running = running + Notional.parse("0.1")
    assert running.amount == Decimal("1.0")


def test_price_times_quantity_is_a_notional_with_exact_scale() -> None:
    result = Price.parse("27431.55") * Quantity.parse("0.0031")
    assert isinstance(result, Notional)
    assert result.amount == Decimal("85.0378050")


def test_quantity_may_be_negative_because_short_positions_exist() -> None:
    assert (-Quantity.parse("3")).amount == Decimal("-3")


def test_timestamp_rejects_a_naive_datetime() -> None:
    with pytest.raises(NaiveDatetimeError):
        Timestamp(datetime(2024, 1, 1, 12, 0, 0))  # noqa: DTZ001 - the point of the test


def test_timestamp_rejects_a_non_datetime() -> None:
    with pytest.raises(NaiveDatetimeError):
        Timestamp("2024-01-01T00:00:00+00:00")  # type: ignore[arg-type]


def test_timestamp_normalises_an_offset_datetime_to_utc() -> None:
    lisbon_summer = timezone(timedelta(hours=1))
    stamp = Timestamp(datetime(2024, 7, 1, 13, 0, tzinfo=lisbon_summer))
    assert stamp.value.utcoffset() == timedelta(0)
    assert stamp.value == datetime(2024, 7, 1, 12, 0, tzinfo=UTC)


def test_timestamp_round_trips_through_epoch_millis() -> None:
    stamp = Timestamp.parse("2024-03-15T08:30:00+00:00")
    assert Timestamp.from_epoch_millis(stamp.epoch_millis) == stamp


def test_timeframe_duration_matches_its_label() -> None:
    assert Timeframe.H4.duration == timedelta(hours=4)
    assert Timeframe.D1.duration == timedelta(days=1)

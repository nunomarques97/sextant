"""Percentiles of a simulated distribution, and how uncertain each one is.

A gate built on a percentile estimated from a finite sample is only as good as
that estimate. The 95th percentile of a thousand random runs is itself a random
variable, and quoting it to three decimals without an interval is how a
threshold acquires an authority it has not earned.

So every percentile this module reports comes with a bootstrap confidence
interval: resample the distribution with replacement, recompute the percentile,
and take the empirical interval of the resampled estimates. That interval is a
statement about *sampling* error - how much the threshold would move if the
experiment were re-run with different seeds. It says nothing about whether the
model generating the distribution is right, and it must never be read as if it
did.

**Interpolation.** ``numpy.percentile`` with the default linear method. Stated
because the alternatives - nearest, lower, higher, midpoint - disagree by up to
one order statistic, which at the 99th percentile of a thousand samples is a
visible difference.

**Randomness.** The resampling generator is constructed explicitly and seeded by
the caller. The legacy global ``numpy.random`` state is never touched, because
any library imported anywhere in the process can disturb it and the failure is
silent.

Pure computation over float arrays. No money, no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from sextant.domain.errors import SextantError

#: Resamples drawn for each interval. 10,000 is the usual default and is cheap
#: here: one resample is an integer draw plus a percentile over a few thousand
#: floats.
DEFAULT_RESAMPLES = 10_000

#: The interval's coverage. Two-sided 95%.
DEFAULT_CONFIDENCE = 0.95


class EmptyDistribution(SextantError):
    """A percentile was asked for from a distribution with nothing in it."""


@dataclass(frozen=True, slots=True)
class PercentileEstimate:
    """One percentile of a simulated distribution, with its sampling interval.

    ``low`` and ``high`` bracket where the percentile would fall if the whole
    experiment were repeated. ``width`` is carried because it is the number a
    reader should look at first: a threshold whose interval is wider than the
    gap between it and the strategy being tested is not a threshold.
    """

    percentile: float
    value: float
    low: float
    high: float
    resamples: int
    confidence: float
    sample_size: int

    @property
    def width(self) -> float:
        """How wide the interval is, in the units of the statistic."""
        return self.high - self.low


def percentile_with_interval(
    values: Sequence[float] | npt.NDArray[np.float64],
    percentile: float,
    *,
    generator: np.random.Generator,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
) -> PercentileEstimate:
    """One percentile and its bootstrap confidence interval.

    ``generator`` is required rather than defaulted, so that a caller cannot
    accidentally produce an interval that is not reproducible.
    """
    sample = np.asarray(values, dtype=np.float64)
    if sample.size == 0:
        raise EmptyDistribution(
            f"Cannot take the {percentile}th percentile of an empty distribution."
        )
    if not 0.0 <= percentile <= 100.0:
        raise ValueError(f"percentile must be in [0, 100], got {percentile}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    point = float(np.percentile(sample, percentile))
    indices = generator.integers(0, sample.size, size=(resamples, sample.size))
    resampled = np.percentile(sample[indices], percentile, axis=1)
    tail = (1.0 - confidence) / 2.0 * 100.0
    return PercentileEstimate(
        percentile=percentile,
        value=point,
        low=float(np.percentile(resampled, tail)),
        high=float(np.percentile(resampled, 100.0 - tail)),
        resamples=resamples,
        confidence=confidence,
        sample_size=int(sample.size),
    )


def distribution_summary(
    values: Sequence[float] | npt.NDArray[np.float64],
) -> tuple[float, float, float, float]:
    """Minimum, mean, standard deviation and maximum, in that order.

    Reported alongside the percentiles because a distribution described only by
    its own tail is easy to misread, and because the mean of a null distribution
    is the number a reader instinctively looks for and must be shown not to be
    the gate.
    """
    sample = np.asarray(values, dtype=np.float64)
    if sample.size == 0:
        raise EmptyDistribution("Cannot summarise an empty distribution.")
    return (
        float(np.min(sample)),
        float(np.mean(sample)),
        float(np.std(sample, ddof=1)) if sample.size > 1 else 0.0,
        float(np.max(sample)),
    )


def generator_for(seed: int) -> np.random.Generator:
    """An explicitly constructed generator, seeded.

    Never the legacy global ``numpy.random`` state: any library imported
    anywhere in the process can disturb that, and when reproducibility fails
    that way it fails silently. Constructed here so that callers which are not
    allowed to import numpy - the wiring layer, which holds monetary types - can
    still obtain one.
    """
    return np.random.Generator(np.random.PCG64(seed))


def quantiles(values: Sequence[float] | npt.NDArray[np.float64]) -> dict[str, float]:
    """The shape of a distribution, not only its mean.

    A null distribution described by its mean is a null distribution nobody can
    use: the gate is in the tail, and the tail is what these report.
    """
    sample = np.asarray(values, dtype=np.float64)
    if sample.size == 0:
        raise EmptyDistribution("Cannot describe an empty distribution.")
    low, mean, deviation, high = distribution_summary(sample)
    return {
        "minimum": low,
        "p05": float(np.percentile(sample, 5.0)),
        "p25": float(np.percentile(sample, 25.0)),
        "p50": float(np.percentile(sample, 50.0)),
        "p75": float(np.percentile(sample, 75.0)),
        "p90": float(np.percentile(sample, 90.0)),
        "p95": float(np.percentile(sample, 95.0)),
        "p99": float(np.percentile(sample, 99.0)),
        "maximum": high,
        "mean": mean,
        "standard_deviation": deviation,
    }


def variance_of(values: Sequence[float] | npt.NDArray[np.float64]) -> float:
    """Sample variance, the input the Deflated Sharpe Ratio wants for ``V``."""
    sample = np.asarray(values, dtype=np.float64)
    if sample.size < 2:
        raise EmptyDistribution(
            f"A variance across trials needs at least two trials, got {sample.size}."
        )
    return float(np.var(sample, ddof=1))

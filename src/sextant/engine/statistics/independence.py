"""How many independent observations a result actually rests on.

A month count is not an observation count. In the previous phase's window the
equal-weight cross-section correlated 0.804 with the largest single asset, so
twenty-four monthly returns of an eight-name portfolio were nothing like
twenty-four independent facts, and reporting them as such is how a window
that could demonstrate nothing came to look adequate. Two different
dependencies do that damage and they need two different corrections.

**Along time.** Monthly returns that are autocorrelated carry less information
than their count suggests. The standard first-order correction is

    N_eff = N * (1 - rho1) / (1 + rho1)

with rho1 the lag-1 autocorrelation. Positive autocorrelation shrinks the count;
negative autocorrelation would inflate it, so the result is clipped to ``[1, N]``
rather than allowed to claim more evidence than there are months.

**Across the cross-section.** Holding *k* names that all move together is not
*k* bets. With mean pairwise correlation rho_bar the effective breadth is

    k_eff = k / (1 + (k - 1) * rho_bar)

which is *k* at rho_bar = 0 and 1 at rho_bar = 1. It turns "we held ten names"
into a claim about how many independent things were being bet on.

Both are approximations and neither is a hypothesis test. They exist so that a
report states the evidence it has rather than the row count of its own table,
and so that "the statistics fall short" can be quantified instead of asserted.

Float work, on the array side of the numeric boundary. No monetary type appears
here, deliberately: see ``engine/statistics/boundary.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

#: Below this many effective observations, part 1 section 11 routes a variant
#: that cleared criterion 1 to verdict (C) rather than to (A).
MINIMUM_EFFECTIVE_OBSERVATIONS = 24.0


@dataclass(frozen=True, slots=True)
class Independence:
    """What one return series is worth, in observations rather than in rows."""

    observations: int
    lag_one_autocorrelation: float
    effective_observations: float

    @property
    def shortfall(self) -> float:
        """How many effective observations short of the pre-registered floor."""
        return max(0.0, MINIMUM_EFFECTIVE_OBSERVATIONS - self.effective_observations)

    @property
    def sharpe_standard_error(self) -> float:
        """Standard error of an annualised Sharpe at this effective count.

        The usual ``sqrt((1 + S²/2) / n)`` with the Sharpe term dropped, which
        is ``1/sqrt(n)`` and is the floor of that expression. Reported so that
        "nothing could have been demonstrated here" is a number rather than an
        opinion: it is the smallest standard error the sample size permits, and
        the true one is larger.
        """
        if self.effective_observations <= 0:
            return float("inf")
        return float(1.0 / np.sqrt(self.effective_observations))

    def detectable_at(self, confidence_z: float = 1.96) -> float:
        """The smallest per-period Sharpe distinguishable from zero here.

        Multiplied by ``sqrt(12)`` this is the annualised effect size the sample
        could have detected. A measured effect below it has not been measured;
        it has been observed to be smaller than the noise.
        """
        return confidence_z * self.sharpe_standard_error

    def as_json(self) -> dict[str, float | int]:
        """Serialisable form."""
        return {
            "observations": self.observations,
            "lag_one_autocorrelation": self.lag_one_autocorrelation,
            "effective_observations": self.effective_observations,
            "sharpe_standard_error": self.sharpe_standard_error,
            "detectable_annualised_sharpe": self.detectable_at() * float(np.sqrt(12.0)),
            "shortfall_vs_floor": self.shortfall,
        }


def lag_one_autocorrelation(values: Sequence[float] | npt.NDArray[np.float64]) -> float:
    """Lag-1 autocorrelation of a return series, or 0.0 when it is flat.

    Zero rather than NaN for a constant series: a series that never moves has no
    autocorrelation to measure, and treating that as "uncorrelated" leaves the
    effective count equal to the raw one, which is the honest answer for a
    series that carries no variation to be dependent about.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.size < 3:
        return 0.0
    centred = array - array.mean()
    denominator = float(np.dot(centred, centred))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(centred[:-1], centred[1:]) / denominator)


def independence_of(values: Sequence[float] | npt.NDArray[np.float64]) -> Independence:
    """The effective observation count of one monthly return series."""
    array = np.asarray(values, dtype=np.float64)
    count = int(array.size)
    if count == 0:
        return Independence(observations=0, lag_one_autocorrelation=0.0, effective_observations=0.0)
    rho = lag_one_autocorrelation(array)
    effective = 1.0 if rho >= 1.0 else count * (1.0 - rho) / (1.0 + rho)
    return Independence(
        observations=count,
        lag_one_autocorrelation=rho,
        effective_observations=float(min(max(effective, 1.0), float(count))),
    )


@dataclass(frozen=True, slots=True)
class Breadth:
    """How many independent bets a held book represented."""

    positions: float
    mean_pairwise_correlation: float
    effective_positions: float

    def as_json(self) -> dict[str, float]:
        """Serialisable form."""
        return {
            "median_positions": self.positions,
            "mean_pairwise_correlation": self.mean_pairwise_correlation,
            "effective_positions": self.effective_positions,
        }


def mean_pairwise_correlation(returns: npt.NDArray[np.float64]) -> float:
    """Mean off-diagonal correlation of a ``(periods, names)`` return matrix.

    Names with no variation are dropped rather than counted as uncorrelated: a
    constant column has an undefined correlation with everything, and filling it
    with zero would inflate the measured breadth in exactly the direction that
    flatters a diversification claim.
    """
    if returns.ndim != 2 or returns.shape[1] < 2 or returns.shape[0] < 3:
        return 0.0
    varying = returns[:, returns.std(axis=0) > 0.0]
    if varying.shape[1] < 2:
        return 0.0
    matrix = np.asarray(np.corrcoef(varying, rowvar=False), dtype=np.float64)
    if not np.all(np.isfinite(matrix)):
        matrix = np.nan_to_num(matrix, nan=0.0)
    count = int(varying.shape[1])
    off_diagonal = (matrix.sum() - np.trace(matrix)) / (count * (count - 1))
    return float(off_diagonal)


def breadth_of(positions: float, correlation: float) -> Breadth:
    """Effective breadth of ``positions`` names at mean correlation ``correlation``."""
    if positions <= 1.0:
        return Breadth(positions, correlation, max(positions, 0.0))
    denominator = 1.0 + (positions - 1.0) * correlation
    effective = positions if denominator <= 0.0 else positions / denominator
    return Breadth(
        positions=positions,
        mean_pairwise_correlation=correlation,
        effective_positions=float(min(max(effective, 1.0), positions)),
    )

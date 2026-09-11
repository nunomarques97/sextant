"""Rank correlation, in exact integer and float arithmetic over a short sequence.

Spearman's rho is Pearson's correlation computed on ranks rather than on values. It
lives here rather than beside the Sharpe statistics because it answers a different kind
of question - whether two orderings agree - and because the samples it is used on in
this project are small enough that the closed form is the whole implementation.

**No array library and no monetary type.** Both would be justifiable and neither is
needed: a rank correlation over six pairs is six lines of arithmetic, and importing
numpy to do it would put this module on the wrong side of a boundary for no gain. Rule
E1's ordering clause is computed here, and so is the cross-sectional agreement F2 will
need.

**Ties get average ranks.** Two equal values share the mean of the ranks they would
have occupied. The alternative - ranking by input order - makes the statistic depend on
how the caller happened to sort its arguments, which is how a result becomes
irreproducible without anything looking wrong.

**A degenerate input answers ``None``, never a number.** Fewer than three pairs, or a
sequence in which every value is identical, has no rank correlation: the denominator is
zero and any value returned would be an invention. Three is the floor because two pairs
always correlate perfectly and say nothing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Below this many pairs a rank correlation is not reported. Two pairs are always
#: either +1 or -1 whatever the numbers are.
MINIMUM_PAIRS = 3


def ranks(values: Sequence[float]) -> tuple[float, ...]:
    """The ranks of ``values``, smallest first, with ties sharing their average rank.

    Returned as floats because an average rank is not an integer: two values tied for
    second and third place both rank 2.5.
    """
    order = sorted(range(len(values)), key=lambda index: values[index])
    out = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in order[position : end + 1]:
            out[index] = average
        position = end + 1
    return tuple(out)


def spearman_rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho between two equally long sequences, or ``None`` if undefined.

    Computed as Pearson's correlation of the average ranks, which is the definition
    that stays correct when there are ties. The rank-difference shortcut,
    ``1 - 6*sum(d^2)/(n^3-n)``, is only equal to it when every value is distinct, and a
    formula that is right most of the time is the wrong one to leave in a file that
    decides whether a cost model gets adopted.
    """
    if len(xs) != len(ys):
        raise ValueError(f"A rank correlation needs paired inputs, got {len(xs)} and {len(ys)}.")
    if len(xs) < MINIMUM_PAIRS:
        return None
    left, right = ranks(xs), ranks(ys)
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    spread_left = math.sqrt(sum((a - mean_left) ** 2 for a in left))
    spread_right = math.sqrt(sum((b - mean_right) ** 2 for b in right))
    if spread_left == 0.0 or spread_right == 0.0:
        return None
    # Not clamped to [-1, 1]. The float slack here is a few parts in 10^16 and it falls
    # *inside* the range - a perfect ordering comes back as 0.9999999999999998 - so a
    # clamp would be a line of code that never fires. A caller comparing against a
    # threshold is unaffected at this scale; a caller testing for exactly 1.0 should not
    # be, and the tests compare approximately.
    return covariance / (spread_left * spread_right)


__all__ = ["MINIMUM_PAIRS", "ranks", "spearman_rank_correlation"]

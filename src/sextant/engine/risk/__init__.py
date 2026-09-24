"""The Risk Engine. Not implemented yet.

It has veto authority over every decision in the system, including anything a
language model recommends, and returns ``RiskVerdict.APPROVE``, ``REDUCE`` or
``REJECT`` (see ``sextant.domain.risk``).

Position size, leverage, maximum exposure, risk limits, stop-loss risk budget
and portfolio risk budget are computed here, deterministically, and nowhere
else. No value originating from a model response may reach any of them.
"""

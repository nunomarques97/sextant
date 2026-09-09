"""The walk-forward, cross-sectional backtesting engine.

Walk-forward is the only mode this package offers. Nothing in it can produce a
performance figure for the window a strategy was fitted on, because no such
object exists: fitting returns a
:class:`~sextant.engine.backtest.allocation.FitRecord`, which carries parameters
and never an equity curve.
"""

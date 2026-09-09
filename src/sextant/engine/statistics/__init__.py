"""Statistics for evaluating a backtest, on the float side of the boundary.

The one crossing between ``Decimal`` money and float arrays lives in
:mod:`sextant.engine.statistics.boundary`. Every other module in this package
takes floats and returns floats, and none of them may import a monetary type -
``tests/unit/test_numeric_boundary.py`` fails the build if one does.
"""

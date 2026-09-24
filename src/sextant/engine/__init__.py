"""The trading engine.

Skeletons only. No strategy, indicator, regime classifier,
backtester or risk rule exists yet, and none may be added without its own design.

The engine depends on ports and domain types exclusively. It never imports an
adapter and never learns which venue it is running against; the layering
contract in ``.importlinter`` fails CI if that changes.

Pipeline:

    market data -> features -> regime -> strategies -> risk -> execution
"""

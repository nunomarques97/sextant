"""Ports: the interfaces the engine talks to.

Every port is a ``typing.Protocol``. Adapters implement them structurally, so a
port never imports an adapter and the engine never learns which adapter it got.
The layering contract enforces the first half of that; dependency wiring in
``sextant.app`` is the only place the second half is decided.
"""

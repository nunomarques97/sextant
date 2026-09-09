"""Adapters: the concrete implementations of the ports.

Everything that touches the outside world lives here - network clients, storage,
clocks, LLM providers. This is the only layer permitted to import an exchange
SDK, and the only layer permitted to call ``datetime.now()``.

Nothing here is imported by ``domain``, ``ports`` or ``engine``. Only
``sextant.app`` wires an adapter to a port.
"""

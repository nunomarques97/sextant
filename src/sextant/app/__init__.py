"""The application layer.

This is the only place where a concrete adapter is bound to a port. It reads
configuration, decides what is allowed to run, wires the dependencies and hands
the engine an object it cannot identify.

Everything above this layer is testable without a network, a key or a venue.
"""

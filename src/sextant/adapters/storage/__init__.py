"""Storage adapters. Not implemented yet.

This is where a ``BarRepository`` implementation will live. The store is keyed
by ``(venue, symbol, timeframe)`` and must retain delisted instruments, because
deleting them is how a historical universe becomes survivorship-biased.

Choosing the storage engine belongs to the data phase, along with the
dependency it needs.
"""

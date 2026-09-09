"""Exchange adapters.

Each venue is an independent peer. Neither substitutes for the other, neither
is preferred, and neither may import the other - the
``venues-are-independent-peers`` contract in ``.importlinter`` fails CI if one
ever does.

Adding a third venue means adding a package here and a line of configuration.
It must never mean editing the domain, the engine or a strategy.
"""

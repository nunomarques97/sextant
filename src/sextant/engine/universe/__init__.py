"""Point-in-time universe construction.

Pure computation over already-fetched history: rules, statistics and the
research/executable split. Imports only the domain, never an adapter, so a
universe can be recomputed from stored bars with no network at all.
"""

"""Strategies. Not implemented yet.

A strategy consumes domain types and ports. It never names a venue, never reads
configuration to discover which symbol it is about, and is never evaluated on
gross PnL: its output is scored net of the CostModel for the venue it is being
priced through.
"""

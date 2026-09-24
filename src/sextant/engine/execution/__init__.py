"""Execution. Not implemented yet.

Execution receives an approved decision and turns it into orders through the
``ExchangeClient`` port. It asks ``capabilities()`` what the venue and account
permit; it never asks which venue it is.

Cross-venue selection is explicitly out of scope until a MarketDataRouter and
an ExecutionRouter are specified with stated rules. Until then a run names its
venue, once, in configuration.
"""

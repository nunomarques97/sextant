"""Feature computation. Not implemented yet.

Contract for whoever fills this in:

* features are computed from closed bars only; an open bar reaching a feature is
  a look-ahead defect, not a rounding issue;
* every feature is a function of ``(instruments, timeframe, as_of)`` with no
  ambient symbol and no hidden state;
* cross-sectional features take the whole universe slice at once, because a
  rank or a z-score across instruments cannot be computed one symbol at a time.
"""

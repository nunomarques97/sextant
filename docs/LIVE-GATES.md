# Live gate criteria

These are the contract. They are not changed as part of implementation work.

LIVE mode may not be enabled until all of the following are demonstrated and
reviewed:

1. Walk-forward out-of-sample evaluation across at least 3 non-overlapping
   windows.
2. Net-of-all-costs return positive in at least 2 of 3 out-of-sample windows.
3. Out-of-sample max drawdown within the configured limit.
4. Deflated Sharpe Ratio, computed with an honest count of every strategy and
   parameter set tried, still positive.
5. At least 60 days of paper trading with tracking error against the backtest
   below a configured threshold.
6. Explicit written sign-off by the project owner.

Backtest return alone is never sufficient. A strategy showing an extraordinary
backtest return is treated as a suspected defect until proven otherwise.

---

## How this document relates to the code

The criteria above are the decision. The mechanical gates in the code are
separate and weaker: they exist so that live trading cannot start by accident,
not so that it can start correctly.

Starting in LIVE requires all three of:

- `mode: live` in the resolved configuration;
- `SEXTANT_ALLOW_LIVE=1` in the process environment (it cannot be set from any
  YAML file);
- a passing preflight, which includes complete credentials and an API key proven
  not to carry withdrawal permission.

Satisfying those three is necessary and not remotely sufficient. Nothing in the
code can check criteria 1 to 6, and no future automation should be trusted to
sign off criterion 6.

Current state: LIVE cannot start at all. The withdrawal-permission probe is not
wired, preflight treats "cannot be determined" as unsafe, and there is no engine
to run.

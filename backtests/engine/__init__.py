"""The machinery every suite shares.

    schedule.py  rebalance dates (yearly / quarterly, up to decades back)
    returns.py   forward returns from total-return closes
    metrics.py   Observation + rank IC, bands, terciles, group comparisons
    runner.py    point-in-time scoring of every ticker on every date

Only the deterministic pillars (fundamentals, valuation) can be replayed:
Claude's qualitative judgments have no point-in-time history.
"""

"""Backtests — does each claim the scoring makes hold up on history?

`tests/` checks that the code does what it says; `backtests/` checks that
the scores mean what they say. Each module in `suites/` tests one claim
("slow growers underperform", "dip signals beat the rest", …) against
point-in-time history and reports PASS / WEAK / FAIL / NO DATA.

    python3 -m backtests                          # every suite, every cached ticker
    python3 -m backtests growth_veto dip_signals  # just these claims
    python3 -m backtests --tickers MSFT GOOGL 005930.KS --step quarter
    python3 -m backtests --list                   # what each suite claims

The app never imports this package; like `tests/`, it only consumes the app.
"""

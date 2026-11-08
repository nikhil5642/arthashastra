---
name: backtest
description: Test each claim the deterministic scoring makes (fundamentals and valuation rank returns, the growth veto and value-trap guard are justified, dip signals pay, the bank rubric ranks banks) on point-in-time history — up to 30 years, yearly or quarterly, for any tickers or the whole cache — then report honestly which claims held.
argument-hint: [suites…] [--tickers <symbols>|all] [--years N] [--step year|quarter]
---

Backtest the scoring for **$ARGUMENTS** and tell the user, honestly,
which of its claims held up on history.

## Step 1 — Run the suites

Backtests live in the root `backtests/` folder, like `tests/`: each module
in `backtests/suites/` tests one claim.

```bash
python3 -m backtests --list                                  # every suite and its claim
python3 -m backtests --tickers all --years 30 --step year     # all suites, whole cache
python3 -m backtests --tickers all --years 30 --step quarter
python3 -m backtests growth_veto dip_signals --tickers MSFT GOOGL 005930.KS RELIANCE.NS
```

Resolve company names to tickers first (`/resolve-ticker`). Options:
`--horizon N` (forward-return window in months, default one step) and
`--lag N` (days after a fiscal year ends before its numbers count as
public, default 90). Each listing's full history is fetched live, one
ticker at a time, so a large universe takes a few minutes. The report is
saved to `data_cache/reports/backtest_<date>.md`. Exit code 1 means a
suite FAILed (the data significantly contradicted it) — that is a
finding, not a crash.

## Step 2 — Read the coverage table first

Depth comes from the data, not the request. Per ticker you'll see the
fundamentals source and how many dates could be scored:

- `sec` — US filers, ~18 fiscal years of first-filed 10-K values
- `yfinance+screener` — Indian listings, ~12 fiscal years
- `yfinance` — everything else, 4 fiscal years (so only the last few dates)

Prices go back much further (Microsoft to 1986). Say plainly which names
had shallow history — a "30-year backtest" of a yfinance-only listing is
really a 4-year one.

## Step 3 — Report each suite's outcome

Outcomes are judged by fixed rules, set before looking at the data:

| Outcome | Meaning |
|---|---|
| PASS | the effect points the way the claim says, and \|t\| ≥ 2 |
| WEAK PASS | it points the claimed way, but \|t\| < 2 — could be noise |
| WEAK FAIL | it points the other way, but \|t\| < 2 — could be noise |
| FAIL | it points the other way and \|t\| ≥ 2 — the data contradicts the claim |
| NO DATA | too few dates or observations (ranking claims need 10+ tickers on a date) |

The suites:

- `fundamentals_score`, `valuation_score` — does a higher score come
  before a higher return? (mean rank IC + its t-stat; terciles table)
- `combined_score` — does adding valuation rank returns better than
  fundamentals alone? (paired per-date IC difference)
- `verdict_bands` — does BUY-or-better beat AVOID-or-worse on the
  deterministic composite?
- `growth_veto` — do industrials in growth-veto territory underperform?
- `value_trap_guard` — among the cheapest third, do slow growers lag?
- `dip_signals` — do quality-dip signals beat everything else?
- `bank_rubric` — among banks only, does the bank rubric rank returns?

Group comparisons use **excess returns** (each return minus its date's
average), so a group isn't flattered by happening to sit in a boom year.

Lead with the FAILs, then the WEAK FAILs. Never present a WEAK result as
evidence either way: |t| < 2 is noise-level. A FAIL on an anti-optimism
anchor (growth veto, value-trap guard) is not a reason to weaken it on
one run: say what the data showed, how many observations it rests on,
and suggest re-running quarterly or on a wider universe before changing
any rubric.

Read the era too: the deep history (2008–2014) comes from the few US
filers in the cache, so a result driven by those years rests on a
handful of today's survivors. The power to test anything comes from more
names per date — more US filers (SEC gives each ~18 years) or Indian
listings (screener, ~12).

## Step 4 — State the caveats

Always name them: survivorship (today's universe only), restated
screener / Yahoo history (SEC is first-filed), only the deterministic
pillars are replayed (Claude's judgments have no point-in-time history),
annual EPS in backtest P/E (up to ~15 months old), returns in each
listing's own currency, and correlated observations on one date make
t-stats optimistic. Never present a backtest as proof the composite
verdict works.

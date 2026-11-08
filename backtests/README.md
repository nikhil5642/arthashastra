# backtests/

`tests/` checks that the code does what it says. `backtests/` checks that
the **scores mean what they say**: each suite takes one claim the scoring
makes and tests it on point-in-time history, up to 30 years back, yearly
or quarterly.

```bash
python3 -m backtests --list                       # every suite and its claim
python3 -m backtests                              # all suites, every cached ticker, 30y yearly
python3 -m backtests --step quarter               # same, rebalanced quarterly
python3 -m backtests growth_veto dip_signals      # just these claims
python3 -m backtests --tickers MSFT GOOGL 005930.KS RELIANCE.NS --years 15
```

Options: `--tickers` (Yahoo symbols, or `all` for the cached universe —
the default), `--years` (default 30), `--step year|quarter`, `--horizon`
(forward-return months, default one step), `--lag` (days after a fiscal
year ends before its numbers count as public, default 90). The report
is printed and saved to `data_cache/reports/backtest_<date>.md`. Exit code
1 means a suite FAILed, like a failing test run.

## Outcomes

Every suite is judged by the same rules, fixed before looking at data:

| Outcome | Meaning |
|---|---|
| **PASS** | the effect points the way the claim says, and \|t\| ≥ 2 |
| **WEAK** | it points the claimed way, but \|t\| < 2 — could be noise |
| **FAIL** | it points the other way |
| **NO DATA** | too few dates (< 5) or observations (< 10 per group) to say anything |

## Suites

| Suite | Claim | Statistic |
|---|---|---|
| `fundamentals_score` | A higher fundamentals score is followed by a higher return | mean rank IC across dates |
| `valuation_score` | A higher valuation score (a cheaper price) is followed by a higher return | mean rank IC (scored names only) |
| `combined_score` | Fundamentals + valuation ranks returns better than fundamentals alone | paired per-date IC difference |
| `verdict_bands` | BUY-or-better beats AVOID-or-worse on the deterministic composite | Welch t on excess returns |
| `growth_veto` | Industrials in growth-veto territory return less than the rest | Welch t on excess returns |
| `value_trap_guard` | Among the cheapest third by earnings yield, slow growers lag growers | Welch t on excess returns |
| `dip_signals` | A quality-dip signal beats everything else | Welch t on excess returns |
| `bank_rubric` | Among banks only, the bank rubric ranks returns | mean rank IC |

**Excess return** = a stock's forward return minus the average forward
return of every scored stock on the same date, so a group isn't
flattered by happening to sit in a boom year.

## Layout

```
backtests/
├── __main__.py / cli.py     # argument parsing, one fetch, run the suites, write the report
├── suite.py                 # Suite, SuiteResult, Outcome, judge + the two claim shapes:
│                            #   ranks_returns (rank IC) and beats (group comparison)
├── report.py                # terminal view + Markdown mirror + caveats
├── engine/
│   ├── schedule.py          # Step (YEAR / QUARTER) + month-end rebalance dates
│   ├── returns.py           # forward_return over total-return closes
│   ├── metrics.py           # Observation, ScoreKind, rank IC, terciles, bands, Welch t
│   └── runner.py            # point-in-time scoring of every ticker on every date
└── suites/
    ├── __init__.py          # ALL — every suite, in report order
    └── <one module per claim>.py
```

How a run works: `cli.py` fetches each ticker's `StatementHistory` once
(`data_sources/fundamentals_source/history.py`: SEC for US filers,
screener for Indian listings, Yahoo for prices and everything else).
`engine/runner.py` rebuilds the snapshot on every rebalance date from
data public by then, prices it at that day's close, scores fundamentals
and valuation with the live scorers, checks the dip gate on the closes
known that evening, and records the next period's total return. Every
selected suite then reads the same observations.

## Adding a backtest

1. Write `suites/<name>.py` with an `evaluate(observations) -> SuiteResult`
   and `SUITE = Suite(name="<name>", claim="…", evaluate=evaluate)`. Most
   claims are one call to `ranks_returns(...)` or `beats(...)`.
2. List it in `suites/__init__.py:ALL` (a test fails if a module is left out).
3. Add a case to `tests/backtests/test_suites.py`: synthetic history where
   the claim holds must PASS, the mirror image must FAIL.

## What a backtest can't tell you

- **Survivorship** — the universe is today's cache; delisted and failed
  companies are absent, which flatters every result.
- **Restatements** — screener and Yahoo show today's restated history;
  only SEC values are first-filed.
- **Only the deterministic pillars** (fundamentals, valuation) can be
  replayed. Claude's leadership, prospects and news judgments have no
  point-in-time history, so the full composite is never backtested.
- **Depth follows the sources** — SEC ~18 fiscal years, screener ~12,
  Yahoo 4 — and the coverage table says which names were shallow.
- **Backtest P/E** divides by the latest annual EPS (up to ~15 months
  old); live P/E is trailing twelve months.
- **Currencies** — returns are in each listing's own currency.
- **Correlated observations** on one date make t-stats optimistic: |t| ≥ 2
  is the minimum bar, not proof.

Layering: like `tests/`, this package imports the app; nothing in the app
imports it.

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

The universe is **one listing per company**: a second share class or a
depositary receipt of a company already in it is the same business
scored twice (the listing in the company's reporting currency is kept,
then the longest price history), and with `all`, a cached quote whose
price and statements don't reconcile is left out. The run names what it
left out.

Options: `--tickers` (Yahoo symbols, or `all` for the companies you
follow — the default), `--years` (default 30), `--step year|quarter`, `--horizon`
(forward-return months, default one step), `--lag` (days after a fiscal
year ends before its numbers count as public, default 90). The report
is printed and saved to `data_cache/reports/backtest_<date>.md`. Exit code
1 means a suite FAILed — the data significantly contradicted its claim —
like a failing test run.

## Outcomes

Every suite is judged by the same rules, fixed before looking at data:

| Outcome | Meaning |
|---|---|
| **PASS** | the effect points the way the claim says, and \|t\| ≥ 2 |
| **WEAK PASS** | it points the claimed way, but \|t\| < 2 — could be noise |
| **WEAK FAIL** | it points the other way, but \|t\| < 2 — could be noise |
| **FAIL** | it points the other way and \|t\| ≥ 2 — the data contradicts the claim |
| **NO DATA** | too few dates or observations to say anything (see below) |

**One number per date.** Returns on one date move together, so a date is
one piece of evidence however many tickers it holds. A ranking claim
takes one rank IC per date; a group comparison takes one difference per
date (the group's mean return minus the other group's, on the dates both
appear). The estimate is the average across dates and `t` is its t-stat
across dates. Pooling every observation instead would let one unusual
year pass for dozens of confirmations — a run where 33 of 40 observations
sat on two dates once read as a significant FAIL.

A ranking claim needs **10+ scored tickers on a date** (a rank correlation
across five names has a standard error near 0.5) and 5+ such dates; a
group comparison needs 10+ observations in each group and 5+ dates with
both groups present. The finding says what was missing — e.g. "the most
on any date was 8" means analyse more names of that kind.

Every table shows the **median** return beside the mean. A mean can be
one name: a basket whose mean is +65% and whose median is +17% made its
money on a lottery ticket, and the two columns say so.

## Suites

| Suite | Claim | Statistic |
|---|---|---|
| `fundamentals_score` | A higher fundamentals score is followed by a higher return | mean rank IC across dates |
| `valuation_score` | A higher valuation score (a cheaper price) is followed by a higher return | mean rank IC (scored names only) |
| `combined_score` | Fundamentals + valuation ranks returns better than fundamentals alone | paired per-date IC difference |
| `verdict_bands` | BUY-or-better beats AVOID-or-worse on the deterministic composite | mean per-date difference |
| `growth_veto` | Industrials in growth-veto territory return less than the rest | mean per-date difference |
| `value_trap_guard` | Among the cheapest third by earnings yield, slow growers lag growers | mean per-date difference |
| `peak_margin_guard` | Among the cheapest third by earnings yield, companies earning above 1.5× their own median margin return less | mean per-date difference |
| `priced_for_doubt` | Profitable companies under 5× repeatable earnings return no more than those at 5–10× | mean per-date difference |
| `early_concerns` | A company carrying a material early concern returns less than one carrying none | mean per-date difference |
| `early_strengths` | A company showing a material early strength returns more than one showing none | mean per-date difference |
| `dip_signals` | A quality-dip signal beats everything else | mean per-date difference |
| `bank_rubric` | Among banks only, the bank rubric ranks returns | mean rank IC |

**Excess return** ("vs same-date average" in the tables) = a stock's
forward return minus the average forward return of every scored stock on
the same date, so a group isn't flattered by happening to sit in a boom
year. The judged statistic compares groups within each date, which takes
the market's move out the same way.

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
│   ├── metrics.py           # Observation, ScoreKind, rank IC, terciles, bands, per-date group test
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

- **Who is in it** — `all` is the companies you follow, which lean large
  and well known; what a screen scored in passing is not included. A
  result on them says little about small companies. To test those, pass
  a screen's scored tickers to `--tickers`.
- **Survivorship and selection** — the universe is today's cache: failed
  and delisted companies are absent, and the names in it were chosen with
  hindsight. Every group's return is lifted, the weakest-scored groups'
  most (their failures are the ones missing). That works *against* any
  claim that weak scores come before weak returns, and flatters
  dip-buying. No free source has prices for delisted listings, so this
  can be named but not fixed here.
- **Restatements** — screener and Yahoo show today's restated history;
  only SEC values are first-filed.
- **Only the deterministic pillars** (fundamentals, valuation) can be
  replayed. Claude's leadership, prospects and news judgments have no
  point-in-time history, so the full composite is never backtested.
- **Depth follows the sources** — SEC ~18 fiscal years, screener ~12,
  Yahoo 4 — and the coverage table says which names were shallow.
- **Backtest P/E** divides by the latest annual EPS (up to ~15 months
  old); live P/E is trailing twelve months.
- **Early signals are only partly replayable** — the ones built on
  fiscal-year statements everywhere, plus screener's quarterly and
  shareholding tables for Indian listings as far back as it publishes
  them (about three years). News signals and Yahoo / SEC quarterly
  signals have no point-in-time history, and unusual items are only
  stripped where the source publishes normalized income (not SEC).
- **Currencies** — returns are in each listing's own currency.
- **Few dates** — every claim is judged across dates, and a yearly run
  has at most a couple of dozen, overlapping in what drove them. |t| ≥ 2
  is the minimum bar, not proof.
- **No cyclical trough** — the scoring caps earnings at the top of a
  cycle and has no counterpart at the bottom, by design: raising a score
  for a loss-making cyclical raises it for the companies most likely to
  fail, and a survivors-only universe can't count the failures. Among
  survivors, loss-makers at the trough rebound hard, and that shows up
  against `verdict_bands` and `growth_veto`.

Layering: like `tests/`, this package imports the app; nothing in the app
imports it.

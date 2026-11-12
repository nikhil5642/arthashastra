# Arthashastra

A Claude-Code-native fundamental analyst for **global equities** — US,
India, China / Hong Kong, Japan, Europe, and any other market Yahoo
Finance lists. Ask "analyse Microsoft", "is Tencent a buy at this
price?", or "find quality dips for ₹5 lakh"; Python fetches live data and
computes deterministic scores, Claude (inside a slash command) writes the
qualitative leadership / prospects / news judgment, and the report tells
you STRONG_BUY / BUY / HOLD / AVOID with the numbers to back it — and,
straight under the verdict, what has changed since those numbers were
audited.

No Anthropic API key, no cost budget, no hardcoded ticker list. The
universe is every listed stock Yahoo's screener covers — about 80,000
listings in 59 markets — plus whatever you've analysed.

## How it covers every market

- **Screens the whole world, from a vague ask.** "Find me a chip maker
  with good fundamentals" reads every chip maker Yahoo lists — 509
  companies in 59 markets, in four requests — skips depositary receipts
  and foreign copies, scores the most promising in full and ranks them.
- **Every size of company, on the same footing.** Nothing is cut off by
  market cap unless you ask. Which companies get scored is decided by a
  preview of the fundamentals rubric that never looks at size; see
  "Fair to every size" below.
- **One pipeline for every listing.** Tickers are Yahoo symbols: `MSFT`,
  `GOOGL`, `RELIANCE.NS`, `0700.HK`, `7203.T`, `SAP.DE`, `SHEL.L`.
  Search by company name and the resolver finds the listing.
- **Live data everywhere.** yfinance supplies every listing; for Indian
  listings screener.in adds a 10-year history, promoter / FII / DII
  flows, and bank asset quality, and wins on the ratios it publishes.
  Where the two sources disagree, the report says so.
- **Currency-aware.** Prices print in the trading currency, statements
  in the reporting currency (Tencent trades in HKD, reports in CNY;
  London quotes in pence). Budgets are in your `BASE_CURRENCY` and every
  foreign price is converted with live FX.
- **Same rubric, no brand priors.** Microsoft and HDFC Bank are scored
  on the same curves; only business models (bank vs non-bank) get
  different rubrics.

## What it catches early

A score built on the latest annual report can be most of a year old.
Every `analyse` and `refresh` also reads what is fresher — the four
newest quarters, the latest balance sheet against the one a year back,
the ownership tape, a week of headlines — and names what it finds, as
**early signals**: concerns and strengths, each with the numbers behind
it and a severity (URGENT / ALERT / INFO).

| It reads | Concerns it names | Strengths it names |
|---|---|---|
| Earnings quality | profit padded by one-off gains; a margin far above the company's own norm (a cyclical peak) | — |
| Cash | free cash flow falling or turning negative; capex taking nearly all operating cash flow; heavy stock-based pay | free cash flow rising or turning positive |
| Momentum | revenue shrinking or slowing against its three-year pace; operating margin compressing; profit falling while sales grow | revenue accelerating; margin expanding; profit scaling faster than sales |
| Balance sheet | debt jumping; negative equity; receivables or inventory outpacing sales; a jump in goodwill | debt falling |
| Ownership | dilution; insiders or foreign institutions selling; retail absorbing institutional selling | buybacks; insiders or foreign institutions buying |
| Lenders | bad loans rising | bad loans falling |
| News | a burst of governance stories; a credit-rating action | — |
| Tape and data | a deep drawdown; stale statements; a quote whose price and statements don't reconcile | — |

Signals state facts and never move a score on their own. Missing data
produces no signal — never a strength. The one place they reach the
numbers is the valuation pillar, which prices on earnings the business
can repeat: one-off gains are taken out, and a margin above 1.5× the
company's own median counts only up to that.

Each run is also compared with the last look at the same ticker: a
verdict that moved, and any concern or strength that is new (or worse),
is shown in a "Changed since …" panel and logged, so `brief` can tell
you what changed across your whole watchlist in the last two weeks.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set BASE_CURRENCY (default INR); optionally BRAVE_API_KEY for web research.
```

## Run

```bash
./launch.sh                  # open Claude Code with the analyst primer
```

That opens an interactive Claude session. Ask in plain language —
"analyse Alphabet", "compare Toyota and Tesla's fundamentals", "why did
TCS score HOLD?" — and Claude drives the CLI and slash commands.

The CLI directly:

```bash
python -m fundamental_engine resolve "Microsoft"            # name → ticker, any exchange
python -m fundamental_engine analyse MSFT                   # full report (live)
python -m fundamental_engine analyse 0700.HK                # Tencent, Hong Kong
python -m fundamental_engine analyse RELIANCE.NS            # Reliance, NSE
python -m fundamental_engine inspect MSFT --section roe     # read the cached snapshot
python -m fundamental_engine screen --industry Semiconductors \
    --score 25 --rank fundamentals                          # every chip maker, best business first
python -m fundamental_engine screen --market us jp --sector tech \
    --min-roe 15 --min-drawdown 20 --score 10               # two markets, with filters
python -m fundamental_engine compare NVDA AMD 2330.TW      # named companies side by side
python -m fundamental_engine ipo anthropic                  # a company preparing to list: SEC stage,
                                                            # and its prospectus scored once transcribed
python -m fundamental_engine ipo anthropic --price 40       # …valued at a price of your choosing
python -m fundamental_engine pick --amount 5L               # dips, budget in BASE_CURRENCY
python -m fundamental_engine pick --amount 5L --tickers 8002.T 8053.T  # dips among chosen names
python -m fundamental_engine brief                          # overview of the cache
python -m backtests --step quarter                          # do the scores mean what they claim?
```

`screen` filters: `--market` (Yahoo codes: us in jp gb hk cn kr de …;
default all), `--sector`, `--industry` (Yahoo's name or part of it:
`tech`, `bank`, `"software infrastructure"`), `--min-mcap` / `--max-mcap`
(in `BASE_CURRENCY`), `--max-pe`, `--min-roe`, `--max-de`,
`--min-revenue-growth`, `--min-dividend-yield`, `--min-drawdown`.
`--sort quality|mcap|drawdown|earnings-yield` orders the list: `quality`,
the default, is the size-blind rubric preview; `mcap` is largest first,
for when size is what you want. `--score N` scores the first N in full
and `--rank risk-adjusted|fundamentals|valuation|both` orders them
(`risk-adjusted`, the default, is fundamentals + valuation less a 0–30
point discount for measured risk); scores fetched in
the last 12 hours are reused (`--reuse-hours 0` forces everything live).
`--json` is for skills.

### Fair to every size

Four listed companies in five are small, and the best business in an
industry is as likely to be one of them. When every chip maker with a
15%+ return on equity was scored in full (113 companies, November 2026),
five of the six best on fundamentals were not large caps — one had a
market cap of $90 million — and the twelve largest held three of the ten
best. A screen that scores "the top N by market cap" never sees most of
its own field. So:

- **Every match is read**, not each market's largest: one query spans
  all the markets asked for.
- **A preview decides who gets scored.** For each input of the
  fundamentals rubric that a screener can filter on — returns on equity,
  capital and assets; debt / equity; current and quick ratios; revenue
  and profit growth — Yahoo is asked which companies clear the rubric's
  "good" and "excellent" marks. A company's preview is the weighted share
  it clears. It never looks at size; companies level on it take turns
  across large, mid and small. On two industries scored in full, the 25
  best previews held 9 of the ten best companies, from every size band.
- **Unknown is not a miss, and not a pass.** A ratio Yahoo has no value
  for is left out, and a preview resting on too few known ratios ranks
  after the well-covered ones.
- **The result says how it covered the field**: how many large, mid and
  small companies matched and were scored, and — when a screen is too
  broad to read or preview whole — that it was.

Blind to size is not blind to risk. Smaller companies are riskier on
average — across 293 cached companies the share price's yearly swing ran
34% for those above $200B and 70% for those below $300M, and loss-making
years rose from 6% of the record to 32%. But a fifth of the large ones
were wilder than the typical small one, so risk is measured for each
company rather than assumed from its size: every scorecard and report
shows the yearly price swing, the worst fall in three years, what trades
on an average day, and the loss years on record. None of it moves a
pillar or a verdict; it does discount the default ranking of a shortlist
(`--rank risk-adjusted`). What the numbers can't measure — whether a small
company depends on one foundry, or can be out-spent by its industry's
leader — Claude weighs in the prospects pillar as "position risk". `pick` holds every position to a tenth of a day's trading, so a
thinly traded stock gets a smaller position or none.

## Slash commands (from inside Claude Code)

| Command | What it does |
|---|---|
| `/analyse-stock <name or ticker>` | Full five-pillar deep dive; Claude writes the leadership / prospects / news judgment |
| `/research-founder <name>` | Leadership research + mode-aware rubric |
| `/screen-stocks <what you want>` | Find companies from a plain-language ask ("a chip maker with good fundamentals", "Japanese industrials, P/E under 15"): every match of every size, the best scored and ranked |
| `/compare-stocks <names>` | Named companies side by side ("AMD or Nvidia?"), or a shortlist for a theme |
| `/analyse-ipo <name>` | A company preparing to list: SEC registration stage, the prospectus transcribed and scored on the same rubric, judged with IPO anchors (who is selling, lock-ups, concentration, runway) |
| `/pick-stocks <amount> [filters]` | Buy-the-dip picks for a budget — screened globally when you name filters, FX-converted |
| `/market-brief` | Overview of the companies you follow + what changed lately |
| `/refresh-now <ticker\|all>` | Force a live data refresh (`all`: every followed company) |
| `/explain-score <ticker>` | Audit trail for a cached score (no re-fetch) |
| `/resolve-ticker <name>` | Free-text name → ticker on any exchange |
| `/backtest [suites] [--tickers …]` | Test each scoring claim on point-in-time history (yearly / quarterly, up to 30 years) |

## Scoring

```
composite = 0.35 × fundamentals + 0.15 × valuation + 0.25 × leadership + 0.15 × prospects + 0.10 × news
```

Fundamentals (is it a good business?) and valuation (is the price
reasonable for what it earns?) are deterministic. The other three are
scored by Claude and written to `data_cache/judgments/<TICKER>.json`.
Valuation uses earnings yield, FCF yield (over the trailing twelve months)
and P/E against the company's own history, on one set of curves for every
market — and on earnings the business can repeat, not on one-off gains or
a cyclical peak. Banks and lenders get a bank rubric (asset quality, NIM,
ROA, CD ratio); everything else — including payment networks, exchanges,
and asset managers — gets the industrial rubric.

Anti-optimism defaults: a growth-deceleration veto caps slow growers, a
value-trap guard stops a shrinking business from looking cheap, quiet
news windows score neutral (55) not bullish, missing data is never
treated as good, known-bad facts are scored rather than dropped
(negative equity, a profit that became a loss), and an integrity red flag
caps the whole composite at AVOID.

Judgments age: Claude's news judgment stops counting after 30 days (the
window it covered has passed), leadership and prospects are flagged STALE
after 90, and an integrity veto never expires.

## What you follow

A company you ask about by name (`analyse`, `refresh`) is *followed*:
`brief`, `refresh all` and the backtests work on those. A vague question
can score dozens of companies along the way; they are cached so the next
question is fast, but they don't join your watchlist until you analyse
one.

## Backtesting

Like `tests/` checks the code, the root [`backtests/`](./backtests/README.md)
folder checks the scores: each suite tests one claim the scoring makes,
and the run reports PASS / WEAK PASS / WEAK FAIL / FAIL / NO DATA per
claim — PASS and FAIL both need |t| ≥ 2.

```bash
python -m backtests --list                       # the claims
python -m backtests                              # every suite, every cached ticker, 30y yearly
python -m backtests growth_veto dip_signals --tickers MSFT GOOGL 005930.KS --step quarter
```

Every company is rebuilt point-in-time — a fiscal year counts only once
it was public (90 days after year-end by default) and is priced at that
day's close — then its next-period total return is measured, yearly or
quarterly, up to 30 years back. Depth follows the data: SEC filings give
US companies ~18 years (first-filed, never restated), screener gives
Indian listings ~12, Yahoo gives everything else 4; prices usually go
back decades.

## Tests

```bash
pytest                       # ~650 tests, a few seconds
pytest --cov=data_sources --cov=scorers --cov=screener --cov=reports \
       --cov=signals --cov=fundamental_engine --cov=backtests
```

All hermetic — no network, no fixtures. Live adapters are monkeypatched;
screener pages are built inline as HTML; HTTP is mocked at the transport.

---

**Internals:** see [`CLAUDE.md`](./CLAUDE.md) for the architecture,
layer discipline, typed-interfaces rules, and scoring rubric details.
Each source folder has its own focused `README.md`.

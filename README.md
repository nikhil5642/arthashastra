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

- **Screens the whole world.** "US and Japanese tech over $10B with ROE
  above 15%, down 20%" reads every listing in those markets through
  Yahoo's live screener, converts sizes into your currency, skips
  depositary receipts and foreign copies, and scores the best matches.
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
python -m fundamental_engine screen --market us jp --sector Technology \
    --min-mcap 1000B --min-roe 15 --min-drawdown 20 --score 10  # screen two whole markets
python -m fundamental_engine screen --max-pe 15 --min-roe 20   # all 59 markets (~1½ min)
python -m fundamental_engine pick --amount 5L               # dips, budget in BASE_CURRENCY
python -m fundamental_engine pick --amount 5L --tickers 8002.T 8053.T  # dips among chosen names
python -m fundamental_engine brief                          # overview of the cache
python -m backtests --step quarter                          # do the scores mean what they claim?
```

`screen` filters: `--market` (Yahoo codes: us in jp gb hk cn kr de …;
default all), `--sector`, `--industry`, `--min-mcap` / `--max-mcap` (in
`BASE_CURRENCY`), `--max-pe`, `--min-roe`, `--max-de`,
`--min-revenue-growth`, `--min-dividend-yield`, `--min-drawdown`, and
`--sort mcap|drawdown|earnings-yield`. `--score N` fully fetches and
scores the top N and adds them to the cache; `--json` is for skills.

## Slash commands (from inside Claude Code)

| Command | What it does |
|---|---|
| `/analyse-stock <name or ticker>` | Full five-pillar deep dive; Claude writes the leadership / prospects / news judgment |
| `/research-founder <name>` | Leadership research + mode-aware rubric |
| `/screen-stocks <filters>` | Screen every listed stock in the chosen markets ("Japanese industrials, P/E under 15") and score the best |
| `/pick-stocks <amount> [filters]` | Buy-the-dip picks for a budget — screened globally when you name filters, FX-converted |
| `/market-brief` | Overview of the cached universe + recent signals |
| `/refresh-now <ticker\|all>` | Force a live data refresh |
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
pytest                       # ~596 tests, a few seconds
pytest --cov=data_sources --cov=scorers --cov=screener --cov=reports \
       --cov=signals --cov=fundamental_engine --cov=backtests
```

All hermetic — no network, no fixtures. Live adapters are monkeypatched;
screener pages are built inline as HTML; HTTP is mocked at the transport.

---

**Internals:** see [`CLAUDE.md`](./CLAUDE.md) for the architecture,
layer discipline, typed-interfaces rules, and scoring rubric details.
Each source folder has its own focused `README.md`.

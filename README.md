# Arthashastra

A Claude-Code-native fundamental analyst for **global equities** — US,
India, China / Hong Kong, Japan, Europe, and any other market Yahoo
Finance lists. Ask "analyse Microsoft", "is Tencent a buy at this
price?", or "find quality dips for ₹5 lakh"; Python fetches live data and
computes deterministic scores, Claude (inside a slash command) writes the
qualitative leadership / prospects / news judgment, and the report tells
you STRONG_BUY / BUY / HOLD / AVOID with the numbers to back it.

No Anthropic API key, no cost budget, no hardcoded ticker list. The
universe is emergent — whatever you've analysed.

## How it covers every market

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
python -m fundamental_engine pick --amount 5L               # dips, budget in BASE_CURRENCY
python -m fundamental_engine brief                          # overview of the cache
python -m backtests --step quarter                          # do the scores mean what they claim?
```

## Slash commands (from inside Claude Code)

| Command | What it does |
|---|---|
| `/analyse-stock <name or ticker>` | Full five-pillar deep dive; Claude writes the leadership / prospects / news judgment |
| `/research-founder <name>` | Leadership research + mode-aware rubric |
| `/pick-stocks <amount>` | Buy-the-dip candidates across the cache, FX-converted |
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
Valuation uses earnings yield, FCF yield and P/E against the company's own
history, on one set of curves for every market. Banks and lenders get a
bank rubric (asset quality, NIM, ROA, CD ratio); everything else — including
payment networks, exchanges, and asset managers — gets the industrial
rubric. Anti-optimism defaults: a growth-deceleration veto caps slow
growers, a value-trap guard stops a shrinking business from looking
cheap, quiet news windows score neutral (55) not bullish, missing data is
never treated as good, and an integrity red flag caps the whole composite
at AVOID.

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
pytest                       # ~450 tests, a few seconds
pytest --cov=data_sources --cov=scorers --cov=screener --cov=reports \
       --cov=signals --cov=fundamental_engine --cov=backtests
```

All hermetic — no network, no fixtures. Live adapters are monkeypatched;
screener pages are built inline as HTML; HTTP is mocked at the transport.

---

**Internals:** see [`CLAUDE.md`](./CLAUDE.md) for the architecture,
layer discipline, typed-interfaces rules, and scoring rubric details.
Each source folder has its own focused `README.md`.

# Fundamental Analyst

Claude-Code-native fundamental analyst for Indian equities (NSE/BSE).
Finds great companies trading at a dip so you can buy quality during
drawdowns. Works episodically: load a handful of stocks, come back weeks
later, the cache is ready.

## Architecture

**Python owns the objective stuff:**

- Live data fetching — yfinance (statements + history) + screener.in
  (ROE / ROCE / promoter holding / pledge) + Wikipedia + Google News RSS
- Deterministic fundamentals scoring (debt health, profitability,
  growth, cash quality, promoter behaviour)
- Dip detection (multi-gate: quality + drawdown + technical confirmation)
- Cache management (scores, judgments, signals, dip candidates)
- Background warmer (asyncio loop that keeps the cache fresh)
- Report rendering (Rich terminal + Markdown)

**Claude (via slash commands) owns the subjective stuff:**

- Leadership judgement (mode-aware rubric, integrity veto)
- Prospects judgement (moat, tailwinds, capex narrative)
- News sentiment (materiality filter, event interpretation)
- Final recommendation in plain language

Claude writes her judgements to `data_cache/judgments/<TICKER>.json`.
When `analyse` runs, the Python pipeline reads the judgment and blends
it with the deterministic fundamentals into a `StockScore`. Pillars
without a judgment are clearly marked **pending Claude judgment** so
nothing silently uses a neutral prior.

There is **no hardcoded ticker list, no hardcoded company metadata, no
hardcoded holidays, no fixtures, no mock LLM, no Anthropic API key.**
The universe is emergent — whatever the user has loaded into the cache
via `refresh` or `analyse`. Everything else comes from live sources.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env.sandbox
# optionally set BRAVE_API_KEY in .env.sandbox for leadership web research
```

## Daily use (from the terminal)

```bash
# 1. Resolve a free-text name to a ticker
python -m fundamental_engine resolve "Reliance Industries"

# 2. Pull a stock into the cache (deterministic fundamentals only)
python -m fundamental_engine refresh RELIANCE

# 3. See the full report (pillars that need Claude judgment will be flagged)
python -m fundamental_engine analyse RELIANCE

# 4. Fetch research for a leader
python -m fundamental_engine research "Mukesh Ambani" --ticker RELIANCE

# 5. Find buy-the-dip picks across the cached universe
python -m fundamental_engine pick --amount 1L

# 6. Market overview
python -m fundamental_engine brief

# 7. Refresh everything in the cache
python -m fundamental_engine refresh all

# 8. Run the background warmer (long-running; Ctrl+C to stop)
bash launch.sh
```

## Daily use (from Claude Code)

Slash commands orchestrate the Python CLI and handle the qualitative
scoring in-session:

| Skill | Purpose |
|---|---|
| `/resolve-ticker <name>` | Free-text name → NSE/BSE ticker |
| `/analyse-stock <TICKER>` | Full four-pillar deep dive; Claude writes leadership/prospects/news judgments |
| `/research-founder <name> [ticker]` | Leadership research + mode-aware rubric |
| `/pick-stocks <amount>` | Buy-the-dip candidates from cached scores |
| `/market-brief` | Market overview with Claude-written narrative |
| `/refresh-now <TICKER\|all>` | Force live data refresh |
| `/explain-score <TICKER>` | Audit trail for a cached score, no re-fetch |

The skill markdown files in `.claude/skills/` contain the full
instructions. Each skill is documented enough that Claude can follow
it step-by-step without additional context.

## Scoring

```
composite = 0.40 × leadership + 0.30 × fundamentals + 0.15 × prospects + 0.15 × news
```

Leadership is mode-adaptive so multi-generation Tata executives aren't
judged against a "first-time founder" framework:

- **FIRST_GEN** — listed < 10 years, original founder active
- **MULTI_GEN_FAMILY** — family promoter ≥ 25%, 2nd+ gen at the helm
- **PROFESSIONAL** — institutional / low-promoter / professional CEO

Fundamentals is fully deterministic with graceful degradation on missing
inputs (Indian mid/small cap data is sparse). Pledge > 10% caps the
score at 40 (promoter-behaviour veto). Low data completeness → score
tagged `LOW_CONFIDENCE` and excluded from `pick`.

## Data flow

```
refresh <TICKER>
   │
   ├── yfinance Ticker(.NS/.BO)
   │     └── history / balance_sheet / income_stmt / cashflow
   │
   └── screener.in HTTP scrape
         └── ROE, ROCE, promoter holding, pledge %, market cap

  → deterministic fundamentals_scorer
  → read judgment from data_cache/judgments/<TICKER>.json (or pending)
  → blend into StockScore
  → write to data_cache/scores.json
  → emit SCORE_UPDATED signal
```

The cache layout:

```
data_cache/
├── scores.json              # all cached tickers (ticker → extended StockScore)
├── judgments/
│   └── <TICKER>.json        # Claude's leadership/prospects/news scores
├── research/
│   └── <name_slug>.json     # ResearchBundle for a leader
├── dip_candidates.json      # active buy-the-dip list
├── signals.json             # event log
├── refresh_queue.jsonl      # CLI → engine refresh requests
└── reports/
    ├── <TICKER>_<date>.md   # saved analyse reports
    └── brief_<date>.md      # saved market briefs
```

## Tests

```bash
pytest
```

All tests are hermetic — no network, no fixture JSONs, no real API
keys. Live adapter functions (`_fetch_yfinance_sync`,
`_fetch_screener_in`, `_fetch_wikipedia_sync`, `_fetch_brave_search`,
`_fetch_feed_sync`, `_resolve_sync`) are monkeypatched at the module
level.

## Relationship to the trading project

Sibling of `/Users/nikhilagrawal/stockMarket/trading/` (intraday /
options trader). They share Python conventions (async, dataclasses,
file cache, signal bus shape) but **no runtime coupling** — the
trading repo holds live broker secrets and is kept air-gapped.

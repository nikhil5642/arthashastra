# Fundamental Analyst — project instructions for Claude

**Claude-Code-native fundamental analyst for Indian equities.** Python
handles all real data fetching and deterministic scoring; Claude
(invoked via slash commands) handles the qualitative leadership,
prospects, and news judgements and writes them to a judgment cache.

Read [`README.md`](./README.md) for the user-facing guide. This file
covers what you need to know as the LLM driving the project.

## The rules (critical)

1. **Nothing is hardcoded.** There is no default ticker list, no
   default alias list, no default holiday calendar, no mock LLM, no
   fixture JSONs. Every piece of company data comes from a live API at
   fetch time. The universe is emergent — it's whatever the user has
   loaded via `refresh` / `analyse`.

2. **No LLM calls from Python.** `scorers/leadership_scorer.py` and
   `scorers/qualitative_scorer.py` do NOT exist. Don't add them. The
   qualitative pillars are scored by you (Claude) inside slash commands
   and written to `data_cache/judgments/<TICKER>.json`. Python reads
   the judgments when rendering reports.

3. **No Anthropic SDK dependency.** `anthropic` is not in
   `requirements.txt`. If you ever find yourself importing it, stop
   and think — the architecture is wrong.

4. **Live data only.** If you need to test something without hitting
   the network, monkeypatch the adapter functions
   (`data_sources/*.py:_fetch_*`) at the module level. Do NOT create
   JSON fixture files. Do NOT ship pre-computed "example" data.

## Directory map

```
fundamental_engine/          # orchestrator, CLI, config, scheduler
├── __main__.py              # CLI router (analyse/refresh/pick/brief/research/resolve/engine)
├── config.py                # AnalystConfig.from_env()
├── analyse_pipeline.py      # fetch + deterministic score + judgment read
├── engine.py                # background cache warmer
├── resolver.py              # name → ticker via yfinance search
├── judgments.py             # Judgment + JudgmentStore (Claude writes, Python reads)
├── universe.py              # tickers currently in scores.json
├── market_calendar.py       # weekday + market-hours gate (no holidays)
├── leader_mode_classifier.py
├── budget_parser.py         # "1L" / "2.5L" → INR float
├── ticker.py                # canonical / yf_symbol / slug
├── cache.py                 # JsonStore

data_sources/                # live data adapters
├── fundamentals_source.py   # yfinance + screener.in
├── research_source.py       # Wikipedia + Brave Search
└── news_source.py           # Google News per-company + market RSS

scorers/                     # deterministic scoring
├── base.py                  # ScorerResult, StockScore, verdict_for
├── fundamentals_scorer.py
└── composite_scorer.py      # blend + pending_judgment placeholder

screener/                    # pure logic
├── dip_detector.py
└── price_history.py         # SQLite + RSI14

reports/                     # Rich + Markdown rendering
├── stock_report.py
├── picks_report.py
├── market_brief.py
└── formatting.py

signals/
└── signal_bus.py            # file-based pub/sub

.claude/skills/              # Claude-native orchestration
├── resolve-ticker.md
├── analyse-stock.md
├── research-founder.md
├── pick-stocks.md
├── market-brief.md
├── refresh-now.md
└── explain-score.md

data_cache/                  # runtime state (gitignored)
├── scores.json
├── judgments/<TICKER>.json
├── research/<slug>.json
├── dip_candidates.json
├── signals.json
├── refresh_queue.jsonl
└── reports/
```

## Scoring rules

```
composite = 0.40*leadership + 0.30*fundamentals + 0.15*prospects + 0.15*news
```

### Fundamentals (30%, deterministic — you never touch this)

Rubric in `scorers/fundamentals_scorer.py`: debt health 25%,
profitability 25%, growth 20%, cash quality 15%, promoter behaviour
15%. Graceful degradation on missing inputs. Pledge > 10% clamps
score ≤ 40.

### Leadership (40%, YOU score this)

Three modes, you classify which applies:

- **FIRST_GEN** — listed < 10 years + founder active. Rubric:
  integrity 35% + track_record 30% + reputation 20% + first-time bonus 15%
- **MULTI_GEN_FAMILY** — promoter ≥ 25% + 2nd+ gen. Rubric:
  governance 30% + pledge clean 25% + succession 20% +
  related-party 15% + family reputation 10%
- **PROFESSIONAL** — institutional or low-promoter. Rubric:
  ceo_track_record 35% + board independence 20% + capital allocation 20%
  + governance 15% + tenure stability 10%

**Integrity veto**: fraud, SEBI action, auditor resignation,
bankruptcy of prior venture → leadership score ≤ 30 regardless.

### Prospects (15%, YOU score this)

Sub-axes: moat 40% + tailwind 30% + capex/guidance clarity 30%.
Based on the research bundle + fundamentals context.

### News (15%, YOU score this)

Rolling 30-day weighted sentiment × relevance over **material** items
only. Material negatives (governance, rating) clamp ≤ 35.

## Judgment file format

Claude writes `data_cache/judgments/<TICKER>.json`:

```json
{
  "ticker": "RELIANCE",
  "leader_name": "Mukesh Ambani",
  "leadership": {
    "score": 82,
    "mode": "MULTI_GEN_FAMILY",
    "integrity_score": 85,
    "evidence": ["Led $30bn Jio buildout", "..."],
    "red_flags": [],
    "summary": "..."
  },
  "prospects": {
    "score": 75,
    "evidence": ["...", "..."]
  },
  "news": {
    "score": 68,
    "evidence": ["..."],
    "material_negatives": []
  },
  "updated_at": "2026-04-11T10:00:00",
  "judged_by": "claude"
}
```

The pipeline reads each pillar individually — you can write a
judgment with only leadership filled in and the other two still
pending. Re-running `/analyse-stock <TICKER>` will refresh them.

## Conventions

- Python 3.10+, async where it earns its keep
- `@dataclass(slots=True)` + `from __future__ import annotations`
- `logging.getLogger(__name__)`, never bare `print` outside CLI output
- Config lives in `AnalystConfig.from_env()`; never read env vars
  from elsewhere
- Single-writer discipline: only the engine writes to aggregate
  files (`scores.json`, `signals.json`). CLI readers are read-only
  except for the specific ticker they operate on.
- Tests are hermetic — monkeypatch `_fetch_*` adapters, don't
  create fixture JSONs

## Don'ts

- Don't import anything from `/Users/nikhilagrawal/stockMarket/trading/`
- Don't add a continuous dashboard or TUI — Rich is only used for
  one-shot terminal reports
- Don't ship a company registry, alias list, ticker list, or holiday
  list. Everything dynamic.
- Don't re-introduce an LLM client in Python
- Don't commit `.env.sandbox` or `.env.live` or anything from
  `data_cache/`

## When running tests

```bash
pytest
```

Tests are fast because there is no network. If a test needs a
`BalanceSheetSnapshot` or `ResearchBundle`, construct it inline in
the test function. Don't create a conftest fixture that loads JSON.

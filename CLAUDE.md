# Fundamental Analyst — project instructions for Claude

**Claude-Code-native fundamental analyst for global equities** — US,
India, China / Hong Kong, Japan, Europe, and any market Yahoo Finance
lists. Python handles all real data fetching and deterministic scoring;
Claude (invoked via slash commands) handles the qualitative leadership,
prospects, and news judgements and writes them to a judgment cache.
One pipeline serves every market — no per-market code paths.

Read [`README.md`](./README.md) for the user-facing guide. This file
covers what you need to know as the LLM driving the project.

## Your role (read this first)

This project is a **two-halves machine**. Understand which half you're
in before every action:

- **Python half** — data fetching, deterministic scoring, screening,
  signal bus, reports. Runs with no LLM involvement. Takes live yfinance
  (every listing) + screener.in (Indian listings) + Google News inputs
  and produces a `BalanceSheetSnapshot` and a five-pillar `StockScore`.
  The fundamentals and valuation scores are fully determined by the
  numbers; you must never hand-edit them.
- **Claude half (you)** — the qualitative judgement pillars.
  Leadership, prospects, and news get scored by you inside slash
  commands (`.claude/skills/<name>/SKILL.md`), and you write the
  result to `data_cache/judgments/<TICKER>.json`. The Python side
  picks up your judgment at report time and blends it into the
  composite.

Every session Claude works in this repo, the default workflow is:

1. If the user asks to **analyse / research a ticker** → invoke the
   matching slash command (`/analyse-stock`, `/research-founder`,
   `/pick-stocks`, etc.). Each skill is a self-contained recipe that
   handles data refresh + qualitative scoring + judgment writing.
2. If the user asks an **ad-hoc question** about a ticker → run
   `python3 -m fundamental_engine analyse <TICKER>` (which reads the
   cached judgment + re-fetches live data) and answer from the typed
   report output. Every `analyse` / `refresh` also caches the full
   snapshot, including raw dumps (`raw_profit_loss`, `raw_balance_sheet`,
   `raw_cash_flow`, `raw_ratios`, `raw_quarters`,
   `raw_shareholding_quarterly`) keyed by the screener row label. Read
   them with `python3 -m fundamental_engine inspect <TICKER> --section
   raw_profit_loss` — no re-scrape, no ad-hoc Python.
3. If the user asks whether the scoring **works** → `/backtest` (or
   `python3 -m backtests [SUITE …] --tickers <SYMBOLS|all> --years 30
   --step year|quarter`). Each suite in `backtests/suites/` tests one
   claim (fundamentals ranks returns, the growth veto is justified, dip
   signals pay, …) on point-in-time history and reports PASS / WEAK PASS
   / WEAK FAIL / FAIL / NO DATA (FAIL needs |t| ≥ 2 against the claim).
   Report the coverage, the t-stats, and every FAIL honestly.
4. If the user asks you to **change the code** → obey the layer
   discipline (see "Architecture" below), add a typed interface for
   any new field / shape / value (see "Typed interfaces"), and
   write/update tests that monkeypatch adapters instead of loading
   fixtures.

## The rules (critical — read every session)

1. **Nothing is hardcoded, and nothing is per-market.** There is no
   default ticker list, no alias list, no holiday calendar, no market
   routing table, no mock LLM, no fixture JSONs. Every piece of company
   data — including its exchange and currencies — comes from a live API
   at fetch time. Data sources select themselves by coverage (screener
   answers only for `.NS` / `.BO` listings), so no code branches on a
   market. The universe is emergent — whatever the user has analysed.

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

5. **Anti-optimism defaults** (load-bearing). Apply downward anchors
   by default:
   - A business with decelerating growth should be downgraded, not
     excused. The growth-deceleration veto in the industrial rubric
     caps the score at 70/60/50 when both 3y CAGRs fall below 7/5/3%.
     Never weaken or bypass it.
   - "Nothing bad happened in the news window" is **neutral (55)**,
     not bullish (70). A quiet window is not a buy signal.
   - Structural disruption (AI automating a legacy service,
     commodity cycle turning, regulated margins tightening) →
     prospects ≤ 55 regardless of moat narrative.
   - Integrity red flags (fraud, SEBI action, auditor resignation,
     prior venture bankruptcy) → leadership ≤ 30 regardless, AND set
     `"integrity_veto": true` in the judgment so the composite is
     gated at 54 (AVOID). Fraud comes with excellent *reported*
     numbers; a pillar clamp alone still let such cases land HOLD.
   - **Unknown is not good.** A missing value stays `None` and drops
     out of the score; it is never defaulted to a clean value. (Pledge
     used to default to 0% because screener has no pledge row — every
     company scored "clean pledge" and the pledge veto could never fire.)
   When your verdict feels more optimistic than the tape, check
   whether you've actually applied these anchors or whether you're
   importing brand priors.

6. **Verify from data, not brand.** When a score clashes with a
   famous company's reputation (e.g. "HDFC Bank got AVOID but it's
   a top-tier bank"), **walk through the actual numbers before
   defending or overriding**: ROE trend, ROA trend, P/B vs historical,
   FII/DII flows, PAT CAGR. Reputation is not evidence. The rubric
   has caught real underperformance that contradicted brand priors
   multiple times — trust the math.

7. **yfinance is the global baseline; screener.in wins for Indian
   listings; SEC EDGAR is the deep history for US filers.** Every listing is fetched from yfinance. For `.NS` / `.BO`
   listings screener.in adds depth, and when the two disagree on D/E,
   ROE, margins, OCF/PAT, working-capital days, or any ratio screener's
   standard tables expose, screener wins. For backtests, US filers use
   SEC's first-filed 10-K values (~18 fiscal years) so restatements
   never leak into the past. See `_SCREENER_WINS` in
   `data_sources/fundamentals_source/source.py` for the full list
   and the rationale — yfinance's "Total Debt" includes leases that
   Indian analysts exclude, and similar US-centric definitional
   mismatches pervade its other fields. Screener winning never hides a
   disagreement: material gaps are recorded as `DataConflict`s and shown
   in the report's Data quality section.

8. **One period per ratio, one definition per metric.** Numerator and
   denominator always come from the same column (`_latest_aligned`) or
   the same fiscal-year label across tables (`_value_at_label`) — never
   "latest of each". Interest coverage is core operating EBIT
   (operating profit − depreciation, or yfinance's Operating Income) /
   interest on both sources, and a negative value is kept, not dropped.
   NaN / inf never pass the merge.

9. **Tickers are Yahoo symbols; every amount knows its currency.**
   `MSFT`, `GOOGL`, `RELIANCE.NS`, `0700.HK`, `7203.T` — the exchange
   suffix is part of the identity and is never stripped or assumed (a
   bare symbol is a US listing). Prices and market cap are in the
   trading currency (`snap.currency`, which can be a minor unit like
   London's `GBp`), statement amounts in the reporting currency
   (`snap.financial_currency`); they differ for ADRs and many Hong Kong
   listings. Convert with `data_sources.fx_source`, display with
   `reports.formatting.format_price` / `format_money`, and never compute
   a ratio across two currencies. Every per-share number is on today's
   share basis: closes are split-adjusted but not dividend-adjusted
   (that would shrink every past P/E), screener and Yahoo already restate
   past EPS for later splits, and SEC's first-filed EPS is rebased by
   Yahoo's split history. Backtest returns use total-return closes.

## Architecture

The project is a strict **layered architecture** — each layer depends
only on layers below it, never on layers above. Run-time direction:

```
   ┌─────────────────────────────────────────────┐
   │  fundamental_engine (orchestrator + CLI)    │   high level
   │  ├── __main__.py  — parser + dispatch       │
   │  ├── commands/    — pick, brief, …          │
   │  └── analyse_pipeline, engine, resolver     │
   └──────────────┬──────────────────────────────┘
                  ↓
   ┌──────────────┴──────────────┐
   │  reports/      │  screener/  │                 presentation + screening
   └──────┬─────────┴──────┬──────┘
          ↓                ↓
   ┌──────┴────────────────┴──────┐
   │        scorers/              │                 deterministic scoring
   └──────────────┬───────────────┘
                  ↓
   ┌──────────────┴───────────────┐
   │       data_sources/          │                 live API adapters
   └──────────────┬───────────────┘
                  ↓
   ┌──────────────┴───────────────┐
   │         signals/             │   low level    (leaf — depends on nothing)
   └──────────────────────────────┘

   tests/  and  backtests/   consume the app from outside; nothing imports them
```

A grep at commit time confirms: `data_sources/`, `scorers/`,
`screener/`, `reports/`, and `signals/` have **zero upward imports**,
and nothing in the app imports `backtests/`. Breaking that discipline
will land you a layering violation at the next `git log` sanity check.

## Directory map

Each source folder has its own `README.md` explaining the contracts.
Read those when you're touching a layer for the first time.

```
fundamental_engine/          # orchestrator + CLI commands — see README.md
├── __main__.py              # argparse + dispatch (the thin CLI shim)
├── commands/                # one file per CLI subcommand that needs orchestration
│   ├── pick.py              #   pick --amount <BUDGET>  (FX-converted, sector + country caps)
│   ├── brief.py             #   brief
│   └── migrate.py           #   migrate-ids — one-time bare NSE keys → Yahoo symbols
├── analyse_pipeline.py      # analyse_ticker / score_snapshot / AnalysisResult
├── analysis_context.py      # "Analysis context" banner + judgment-age helpers
├── extended_score.py        # ExtendedScore — typed scores.json row + from_dict / to_dict
├── dip_candidate.py         # DipCandidateRecord — typed dip_candidates.json row
├── engine.py                # background cache warmer; writes scores.json + snapshots + prices.db + signals.json
├── snapshot_store.py        # SnapshotStore — full per-ticker snapshot cache (read by `inspect`)
├── resolver.py              # free-text → ticker on any exchange via Yahoo search
├── judgments.py             # Judgment + JudgmentStore (Claude writes, Python reads)
├── universe.py              # load_scores / load_dip_candidates — typed loaders
├── leader_mode_classifier.py  # LeaderMode StrEnum (4 global modes) + classify_mode(...)
├── budget_parser.py         # Magnitude enum (K / M / B / LAKH / CRORE) + parse_budget
├── ticker.py                # canonical / slug (path-safe name)
├── config.py                # AnalystConfig.from_env()
└── cache.py                 # JsonStore — dict[str, object] boundary

data_sources/                # live data adapters — see README.md
├── fundamentals_source/     # yfinance + screener.in + SEC, split into single-responsibility submodules
│   ├── __init__.py          #   public API only (tests import private helpers from submodules)
│   ├── snapshot.py          #   BalanceSheetSnapshot + SnapshotFields TypedDict + SCORED_FIELDS
│   │                        #   + DataConflict + is_bank_snapshot (rubric router)
│   │                        #   + PeerRecord / DocumentRecord aliases
│   ├── source.py            #   FundamentalsSource async facade + merge rules + _SCREENER_WINS
│   ├── yfinance_adapter.py  #   yfinance fetcher + section fillers (pandas types via TYPE_CHECKING)
│   ├── screener_adapter.py  #   screener.in fetcher + section parsers + appliers
│   │                        #   (HTMLParser type via TYPE_CHECKING)
│   ├── sec_adapter.py       #   SEC EDGAR companyfacts → yfinance-shaped annual frames (first-filed)
│   ├── valuation.py         #   apply_valuation: P/E, earnings / FCF yield, P/B, P/E history
│   ├── history.py           #   StatementHistory + snapshot_as_of + rebase_sec_eps (point-in-time)
│   └── primitives.py        #   parse helpers + period alignment + date-matched YoY
├── price_series.py          # PriceSeries — split-adjusted closes + date lookups
├── names.py                 # company-name cleaning shared by resolver + news queries
├── fx_source.py             # live FX via Yahoo pairs + minor units (GBp, ZAc, ILA)
├── research_source.py       # Wikipedia API + Brave Search (WebHit TypedDict)
└── news_source.py           # Google News RSS (FeedEntry TypedDict) + NewsCategory enum

scorers/                     # deterministic scoring — see README.md
├── base.py                  # Verdict + Pillar + Gate StrEnums + ScorerResult + StockScore + verdict_for
├── composite_scorer.py      # blend(...) 35 / 15 / 25 / 15 / 10 + integrity gate
├── valuation_scorer.py      # valuation pillar: earnings yield + FCF yield (or P/B vs ROE) + P/E vs history
└── fundamentals_scorer/     # dispatches to industrial or bank rubric based on is_bank_snapshot
    ├── __init__.py          #   score_fundamentals entry
    ├── industrial.py        #   industrial rubric + 5 sub-scorers + pledge / growth vetoes
    │                        #   (growth_deceleration_cap is shared with valuation + backtests)
    ├── bank.py              #   bank / NBFC rubric + 5 sub-scorers (incl. asset quality)
    └── ownership.py         #   shared ownership sub-score (pledge, insider change, holding)

screener/                    # pure logic — see README.md
├── dip_detector.py          # multi-gate check_dip + DipSignal (Severity enum, VERDICT_ORDER from enum)
└── price_history.py         # PriceHistoryStore (SQLite) + Wilder RSI14 + SMA

reports/                     # pure Rich + Markdown presentation — see README.md
├── formatting.py            # make_console, verdict_text, VERDICT_STYLES, GATE_LABELS
└── stock_report/            # /analyse output split into single-responsibility renderers
    ├── __init__.py          #   re-exports render_stock_report
    ├── render.py            #   entry + markdown writer
    ├── header.py            #   header panel + valuation + analyst bundle
    ├── financial_profile.py #   profile dispatcher + industrial / bank variants
    ├── data_quality.py      #   statement freshness + source conflicts
    └── trends.py            #   multi-year + shareholding + quarterly rows

signals/                     # append-only file pub/sub + shared file I/O — see README.md
├── signal_bus.py            # Signal + SignalBus + SignalType + Severity enums
└── fileio.py                # atomic_write_text + file_lock (crash-safe JSON writes)

.claude/skills/<name>/SKILL.md   # Claude-native slash commands
├── resolve-ticker/SKILL.md
├── analyse-stock/SKILL.md
├── research-founder/SKILL.md
├── pick-stocks/SKILL.md
├── market-brief/SKILL.md
├── refresh-now/SKILL.md
├── explain-score/SKILL.md
└── backtest/SKILL.md

tests/                       # hermetic, adapter-mocked — see README.md
└── (mirrors the source layout one-for-one, backtests/ included)

backtests/                   # does each scoring claim hold on history? — see README.md
├── __main__.py / cli.py     # python3 -m backtests [SUITE …] --tickers … --years 30 --step year|quarter
├── suite.py                 # Suite + SuiteResult + Outcome + judge (|t| ≥ 2 to PASS or FAIL)
├── report.py                # terminal view + Markdown mirror (data_cache/reports/backtest_*.md)
├── engine/                  # schedule · returns · metrics (Observation, rank IC, Welch t) · runner
└── suites/                  # one claim per module, registered in suites/__init__.py:ALL

data_cache/                  # runtime state (gitignored)
├── scores.json              # per-ticker extended StockScore (engine + analyse write)
├── prices.db                # SQLite — real daily closes from yfinance.history()
├── judgments/<TICKER>.json  # Claude-written qualitative scores
├── snapshots/<TICKER>.json  # full scored snapshot (raw dumps, provenance, conflicts)
├── research/<slug>.json     # cached Wikipedia + Brave bundles
├── news/<TICKER>.json       # per-company Google News RSS snapshots
├── dip_candidates.json      # last pick run output
├── signals.json             # append-only event log (bounded at 500)
└── reports/                 # saved Markdown reports (analyse / brief / backtest)
```

## Typed interfaces (zero `Any` in the source tree)

The codebase enforces a hard "no `typing.Any` anywhere in source" rule.
A grep confirms it at commit time:

```bash
grep -rnE '\bAny\b' data_sources/ scorers/ screener/ reports/ \
    signals/ fundamental_engine/ backtests/ --include='*.py'
# (docstring / comment mentions only — never a type hint)
```

Every domain value has a proper home:

### Enums — domain-valued strings

Every discriminated string lives in a `StrEnum` so typos surface at
call time rather than in a production verdict. Members are `str`
subclasses so JSON serialization and dict-key usage keep working, but
callers get IDE autocomplete and exhaustive iteration.

| Enum                  | Location                                           | Values |
|---                    |---                                                 |---     |
| `Verdict`             | `scorers/base.py`                                  | STRONG_BUY · BUY · HOLD · AVOID · RED_FLAG |
| `Pillar`              | `scorers/base.py`                                  | FUNDAMENTALS · VALUATION · LEADERSHIP · PROSPECTS · NEWS |
| `Gate`                | `scorers/base.py`                                  | INTEGRITY_VETO |
| `ValuationComponent`  | `scorers/valuation_scorer.py`                      | EARNINGS_YIELD · FCF_YIELD · PRICE_TO_BOOK · VS_HISTORY |
| `IndustrialComponent` | `scorers/fundamentals_scorer/industrial.py`        | BALANCE_SHEET_HEALTH · PROFITABILITY · GROWTH · EARNINGS_QUALITY · OWNERSHIP |
| `BankComponent`       | `scorers/fundamentals_scorer/bank.py`              | PROFITABILITY · ASSET_QUALITY · GROWTH · FRANCHISE · GOVERNANCE |
| `RefreshStatus`       | `fundamental_engine/engine.py`                     | OK · NO_DATA · ERROR |
| `LeaderMode`          | `fundamental_engine/leader_mode_classifier.py`     | FOUNDER_LED · FAMILY_CONTROLLED · STATE_CONTROLLED · PROFESSIONAL (legacy FIRST_GEN / MULTI_GEN_FAMILY still parse) |
| `Magnitude`           | `fundamental_engine/budget_parser.py`              | K · LAKH · MILLION · CRORE · BILLION (float-valued enum) |
| `NewsCategory`        | `data_sources/news_source.py`                      | GOVERNANCE · RATING · INSIDER · LEADERSHIP · CORPORATE_ACTION · EARNINGS (severity order) |
| `Severity`            | `signals/signal_bus.py`                            | INFO · ALERT · URGENT |
| `SignalType`          | `signals/signal_bus.py`                            | DIP_DETECTED · QUALITY_DIP · LEADERSHIP_NEWS · … |
| `Step`                | `backtests/engine/schedule.py`                     | YEAR · QUARTER |
| `ScoreKind`           | `backtests/engine/metrics.py`                      | FUNDAMENTALS · VALUATION · COMBINED |
| `Outcome`             | `backtests/suite.py`                               | PASS · WEAK_PASS · WEAK_FAIL · FAIL · NO_DATA |
| `Direction`           | `backtests/suite.py`                               | POSITIVE · NEGATIVE (the sign a claim predicts) |

### TypedDicts — partial / structured JSON shapes

| TypedDict        | Location                                                    | Purpose |
|---               |---                                                          |---     |
| `SnapshotFields` | `data_sources/fundamentals_source/snapshot.py`              | The **builder shape** used by yfinance + screener adapters. Every `_fill_*`, `_apply_*`, `_parse_*` helper takes this instead of `dict[str, Any]`. Mirrors `BalanceSheetSnapshot` fields with `total=False`. |
| `FeedEntry`      | `data_sources/news_source.py`                               | One normalised RSS row (`headline`, `body`, `url`, `published_at`, `source`). |
| `WebHit`         | `data_sources/research_source.py`                           | One Brave Search result (`title`, `url`, `snippet`). |

### Typed records — JSON persistence boundary

Raw JSON from `data_cache/*.json` is parsed into typed records *once*
at the load site, then the rest of the codebase works with typed
objects. There's a single `from_dict` / `to_dict` pair per record; no
consumer anywhere else does `.get("key", default)` on a raw dict.

| Record                | Location                                       | Cache file |
|---                    |---                                             |---     |
| `ExtendedScore`       | `fundamental_engine/extended_score.py`         | `scores.json` |
| `DipCandidateRecord`  | `fundamental_engine/dip_candidate.py`          | `dip_candidates.json` |
| `BalanceSheetSnapshot` + `DataConflict` | `data_sources/fundamentals_source/snapshot.py` | `snapshots/<TICKER>.json` |
| `Judgment`, `PillarJudgment` | `fundamental_engine/judgments.py`       | `judgments/<TICKER>.json` |

Each `from_dict(data: dict[str, object])` narrows untyped input with
dedicated `_as_float`, `_as_str`, `_as_int`, `_parse_evidence`,
`_parse_severity` helpers — every coercion is explicit and isinstance-
checked. Corrupt rows return `None` instead of crashing the caller.

### Pandas / selectolax / yfinance types

External-library objects are forward-referenced via `TYPE_CHECKING`
imports so there's zero runtime cost. See `yfinance_adapter.py` for
the pattern — `is_: "DataFrame | None"`, `tree: "HTMLParser"`,
`t: "Ticker"`.

### `dict[str, object]` at serialization boundaries

Every `to_dict()` method returns `dict[str, object]` (not `dict[str, Any]`).
`object` has no methods, so callers cannot use the value without
explicit narrowing — which forces the untyped-to-typed transition to
happen in one place per record, not scattered across consumers.

`JsonStore.read/write/upsert/get` in `fundamental_engine/cache.py`
uses `dict[str, object]` + an `isinstance(parsed, dict)` guard on
the top-level value (a corrupt file with an array or scalar at the
root returns an empty dict instead of crashing `.items()`).

### The rule

When you add a new field / persisted shape / domain value:

1. Domain-valued strings → add to the relevant `StrEnum`, never a fresh string literal.
2. A new field on `BalanceSheetSnapshot` → also add it to `SnapshotFields`.
3. A new persisted JSON shape → define a typed record with `from_dict` / `to_dict` and coerce at the boundary; never let raw dict access leak into business logic.
4. If you *must* accept polymorphic input, use `object` and narrow with `isinstance`; **never `Any`**.

## Scoring rules

```
composite = 0.35*fundamentals + 0.15*valuation + 0.25*leadership + 0.15*prospects + 0.10*news
```

Half the composite is deterministic (fundamentals + valuation, Python),
half is your judgment (leadership + prospects + news). Valuation joined
in composite v2 after the first 30-year backtest showed the
fundamentals score alone ignored price: a great business at 80× earnings
scored the same as at 12×.

### Fundamentals (35%, deterministic — you never touch this)

Two rubrics, chosen by `is_bank_snapshot(snap)` — structure first:
screener's bank P&L shape (`is_bank`) wins; otherwise a financial
sector string routes to the bank rubric only when the balance sheet
has no current-assets split. Payment networks, exchanges, AMCs and
brokers publish ordinary balance sheets and get the industrial rubric.

**Industrial** (default): balance-sheet health 35% (D/E + interest
coverage + current ratio + quick ratio + net debt / EBITDA),
profitability 20% (ROE + ROCE + ROA), growth 15% (3y revenue + PAT
CAGR), earnings quality 15% (OCF/PAT + FCF/PAT), ownership 15%
(pledge when known + insider stake change + neutral-centred holding —
a widely-held company is not penalised). Graceful degradation on
missing inputs. Pledge > 10% clamps the score to 40. Growth-
deceleration veto caps the score at 70/60/50 when both 3y CAGRs fall
below 7/5/3%.

**Bank / NBFC / levered financials**: profitability 30% (ROE + ROA +
NIM on bank-calibrated curves — ROA 1.5% is good, not 12%), asset
quality 25% (gross NPA + net NPA + 1y gross-NPA trend, from screener's
bank quarterly table), growth 20% (PAT 3y CAGR only — merger
step-changes distort revenue CAGR), franchise 15% (non-interest
income ratio + Credit-Deposit ratio), governance 10% (the shared
ownership sub-score). CASA / CRAR are not exposed by screener and
must be verified in the Claude judgment.

**Composite gate:** a judgment with `integrity_veto: true` caps the
composite at 54.0 (top of AVOID) after the weighted blend, and `pick`
blocks the ticker.

Every listing's data comes from yfinance; Indian listings add
screener.in (balance sheet, P&L, cash flow, ratios, quarters,
shareholding tables), which wins on the ratios it publishes. Ratios are
currency-free, so the same curves score Microsoft, Tencent, and
Reliance alike.

### Valuation (15%, deterministic — you never touch this)

Is the price reasonable for what the business earns? Universal curves:
earnings yield 35% (100 / P/E) + FCF yield 35% + today's P/E vs the
median of its own past fiscal-year-end P/Es 30%. Banks and lenders swap
FCF yield for P/B judged against ROE / 11% cost of equity. Anti-optimism:
a loss-maker scores 0 on earnings yield, and the **value-trap guard**
caps valuation at 70 for slow growers (the same both-CAGRs-below-7%
test as the growth veto) — cheap because shrinking is not a bargain.
Price and statements must share a currency (ADRs keep only the source's
P/E); per-share numbers are all on today's share basis (split-adjusted
closes; SEC's first-filed EPS is rebased by Yahoo's split history).
Unscoreable → neutral 50 with zero confidence.

### Leadership (25%, YOU score this)

Four modes, valid in any market; you classify which applies:

- **FOUNDER_LED** — founder still runs or controls it, whatever the
  listing age. Rubric: integrity 35% + execution track record 30% +
  capital allocation 20% + key-person / succession risk 15%
- **FAMILY_CONTROLLED** — family / promoter group controls it (2nd+
  generation, or a family bloc ≥ 25%). Rubric: governance 30% + pledge /
  encumbrance clean 25% + succession 20% + related-party 15% + family
  reputation 10%
- **STATE_CONTROLLED** — a government or state entity controls it.
  Rubric: minority-shareholder treatment 30% + capital-allocation
  discipline 25% + policy / political interference risk 20% + governance
  & disclosure 15% + management stability 10%
- **PROFESSIONAL** — widely held, professional management. Rubric:
  ceo_track_record 35% + board independence 20% + capital allocation 20%
  + governance 15% + tenure stability 10%

**Integrity veto**: fraud, enforcement action by any securities
regulator or prosecutor (SEC / DOJ, SEBI / ED, CSRC, SFC, FCA, BaFin,
JFSA, …), auditor resignation, restatement, bankruptcy of a prior
venture → leadership score ≤ 30 regardless, plus `"integrity_veto": true`
in the leadership block (composite gate).

### Prospects (15%, YOU score this)

Sub-axes: moat 40% + tailwind 30% + capex/guidance clarity 30%.
Based on the research bundle + fundamentals context.

### News (10%, YOU score this)

Rolling 30-day weighted sentiment × relevance over **material** items
only. Material negatives (governance, rating) clamp ≤ 35.

## Judgment file format

Claude writes `data_cache/judgments/<TICKER>.json`:

```json
{
  "ticker": "RELIANCE.NS",
  "leader_name": "Mukesh Ambani",
  "leadership": {
    "score": 82,
    "mode": "FAMILY_CONTROLLED",
    "integrity_score": 85,
    "integrity_veto": false,
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
- Don't commit `.env` or anything from
  `data_cache/`

## When running tests

```bash
pytest
```

Tests are fast because there is no network. If a test needs a
`BalanceSheetSnapshot` or `ResearchBundle`, construct it inline in
the test function. Don't create a conftest fixture that loads JSON.
See `tests/README.md` for the full testing conventions.

## Further reading

Each source folder has a focused `README.md`:

- [`fundamental_engine/README.md`](./fundamental_engine/README.md) — orchestrator + CLI
- [`data_sources/README.md`](./data_sources/README.md) — live adapters
- [`scorers/README.md`](./scorers/README.md) — fundamentals + composite blend
- [`screener/README.md`](./screener/README.md) — dip detection + SQLite price history
- [`reports/README.md`](./reports/README.md) — Rich + Markdown renderers
- [`signals/README.md`](./signals/README.md) — file pub/sub
- [`tests/README.md`](./tests/README.md) — hermetic testing conventions

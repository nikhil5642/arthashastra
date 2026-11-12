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
  (every listing) + screener.in (Indian listings) + SEC EDGAR (US filers'
  long history) + Google News inputs and produces a `BalanceSheetSnapshot`,
  a five-pillar `StockScore`, and a list of **early signals** — concerns
  and strengths the newest quarters, the latest balance sheet, the
  ownership tape and the week's headlines show before any score moves.
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
   raw_profit_loss` — no re-scrape, no ad-hoc Python. The report's
   "Early signals" panel and the "Changed since …" panel are the first
   things to read: they say what is new since the audited numbers and
   since the last look.
3. If the user asks to **find / screen / pick stocks** — and the ask will
   usually be vague: "find me a chip maker with good fundamentals", "any
   cheap Japanese industrials?" → `/screen-stocks`, or `/pick-stocks
   <budget> <filters>` when there is money to place. Work out what was
   meant, say in one line what you assumed, and answer; don't ask a
   clarifying question when a sensible default exists. Both read every
   listed stock that matches through Yahoo's screener (`python3 -m
   fundamental_engine screen …`), order the matches by a size-blind
   preview of the fundamentals rubric, score the best in full and rank
   them. **Every size of company is in play unless the user names one**
   (rule 12).
   If the user names the companies ("AMD or Nvidia?", "compare these
   five") or a theme Yahoo has no industry for → `/compare-stocks`
   (`python3 -m fundamental_engine compare <TICKERS…>`).
   Both rank by reward less measured risk unless the ask says otherwise
   (`--rank risk-adjusted`, rule 12).
4. If the user asks about a company **preparing to list** ("is Anthropic
   filing?", "analyse the X IPO") → `/analyse-ipo <name>` (`python3 -m
   fundamental_engine ipo <NAME> [--price P]`). See rule 14.
5. If the user asks whether the scoring **works** → `/backtest` (or
   `python3 -m backtests [SUITE …] --tickers <SYMBOLS|all> --years 30
   --step year|quarter`). Each suite in `backtests/suites/` tests one
   claim (fundamentals ranks returns, the growth veto is justified, dip
   signals pay, …) on point-in-time history and reports PASS / WEAK PASS
   / WEAK FAIL / FAIL / NO DATA (FAIL needs |t| ≥ 2 against the claim;
   every estimate is one number per date, with t taken across dates).
   Report the coverage, the t-stats, and every FAIL honestly.
6. If the user asks you to **change the code** → obey the layer
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
   market. The universe is either live — every listing Yahoo's screener
   covers (~80,000 in 59 markets, via `screen`) — or whatever the user
   has analysed (the cache). Markets, sectors and industries are
   yfinance's own lists.

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
   - **Known-bad is not unknown.** The mirror of the rule above: a fact
     that has no ratio is still a fact. Negative shareholders' equity
     scores the D/E axis at 0 (it used to drop out, so the most levered
     balance sheets scored like unlevered ones). A profit that turned
     into a loss is a −100% PAT CAGR (`PROFIT_WIPED_OUT_CAGR`), so the
     growth veto can fire on it (it used to be "unknown", which let a
     shrinking loss-maker escape the veto).
   - **Earnings must be repeatable.** The valuation pillar prices on
     profit the business can earn again: unusual gains are taken out
     (never added back), and a margin far above the company's own median
     counts only up to 1.5× that median. A P/E of 15 made of paper gains,
     or 9 at the top of a memory cycle, is not cheap.
   - **A challenger is judged against its leader.** Size stays out of the
     search (rule 12) but position goes into the judgment: a small GPU
     maker without contracted foundry capacity, or a small model maker the
     leader out-spends tenfold, carries business risk the leader does not.
     In the prospects pillar, answer from evidence — scale against the
     leader, single-supplier dependence and contracted capacity, customer
     concentration, the leader's absolute R&D and capex, whether the
     product is a feature the leader can bundle, cash runway. In a
     scale-driven industry, a company a small fraction of the leader's
     size with no documented niche, captive customers or contracted supply
     gets prospects ≤ 60; a documented niche lifts the cap. The mirror:
     being the leader is not a moat by itself (rule 6). Every prospects
     judgment carries a `Position risk:` evidence line.
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
   standard tables expose, screener wins. For US filers SEC adds history
   only — up to ten fiscal years of sales, profit, margins and ROCE in
   the live snapshot (Yahoo has four), never a current ratio. For
   backtests, US filers use SEC's first-filed 10-K values (~18 fiscal
   years) so restatements never leak into the past. See `_SCREENER_WINS` in
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
   (operating profit − depreciation, or yfinance's Operating Income —
   never Yahoo's "EBIT", which carries other income) / interest on both
   sources, and a negative value is kept, not dropped. NaN / inf never
   pass the merge. The same holds across time and sources:
   - **Scores read the latest audited fiscal year; valuation and early
     signals read the quarters since.** Trailing twelve months is the
     sum of four *consecutive* quarters or it is nothing
     (`trailing_twelve_months`); every `ttm_` figure on a snapshot
     describes the same window (`_keep_one_trailing_window`), and every
     year-on-year comparison is matched by date, never by position.
   - **Amounts from two sources share a currency or don't merge.**
     Screener's statements are in rupees; when Yahoo reports the same
     company in another currency (Infosys files in dollars), yfinance's
     absolute amounts are dropped and only its ratios kept
     (`_in_screeners_currency`).

9. **Tickers are Yahoo symbols; every amount knows its currency.**
   `MSFT`, `GOOGL`, `RELIANCE.NS`, `0700.HK`, `7203.T` — the exchange
   suffix is part of the identity and is never stripped or assumed (a
   bare symbol is a US listing). Prices and market cap are in the
   trading currency (`snap.currency`, which can be a minor unit like
   London's `GBp`), statement amounts in the reporting currency
   (`snap.financial_currency`); they differ for ADRs and many Hong Kong
   listings. Convert with `data_sources.fx_source`, display with
   `reports.formatting.format_price` / `format_money`, and never compute
   a ratio across two currencies without converting. For a listing whose
   two currencies differ the live path converts the market cap with spot
   FX — but only after the quote **reconciles**: a cross-currency quote
   below 1.5× earnings, or one whose FX-converted P/E is more than 3× away
   from the source's own, is a depositary receipt (CEDEAR, SDR) or carries
   a wrong currency label. It is marked `quote_unreliable` and gets no
   price-based ratio at all. Every per-share number is on today's
   share basis: closes are split-adjusted but not dividend-adjusted
   (that would shrink every past P/E), screener and Yahoo already restate
   past EPS for later splits, and SEC's first-filed EPS is rebased by
   Yahoo's split history. Backtest returns use total-return closes.

10. **Early signals state facts; they never touch a score.**
    `scorers/early_signals.py` names what the newest data shows — profit
    padded by one-offs, a margin at a cyclical peak, free cash flow
    falling, capex outrunning cash flow, debt or goodwill jumping,
    dilution, receivables outpacing sales, insider and institutional
    flows, a burst of governance headlines — and the mirror-image
    strengths. A signal needs the numbers: missing data produces no
    signal, never a strength. The deterministic pillars are not adjusted
    by them (the valuation pillar already prices on repeatable earnings);
    **you** weigh them in the qualitative pillars, and the
    `/analyse-stock` skill says how. An URGENT concern must be addressed
    in your judgment, by name.

11. **Judgments age.** A cached judgment was right on the day it was
    written. The news pillar judges a rolling 30-day window, so a
    judgment older than 30 days no longer counts for news (the pillar
    goes back to neutral, zero confidence). Leadership and prospects
    older than 90 days still count, at lower confidence, with a STALE
    line. An integrity veto never expires. Pillar scores are held to
    0–100 when read.

12. **No company is preferred for its size.** Four listed companies in
    five are small, and the best business in an industry is as likely to
    be one of them: of the 113 chip makers with a 15%+ return on equity
    (November 2026), five of the six that scored best on fundamentals were
    not large caps, and the largest twelve held three of the ten best.
    - Nothing is truncated by market cap unless the user asks for a size:
      a screen reads every match (one query spans all its markets), not
      each market's largest. "Largest first" is `--sort mcap` and nothing
      else.
    - Who gets scored in full is decided by the **rubric preview**
      (`fundamental_engine/preview.py`): how many of the fundamentals
      rubric's own marks a company clears, asked of Yahoo for every
      listing in the screen. No size term; companies level on it take
      turns across large, mid and small. On two industries scored in full
      it found 9 of the ten best among the first 25, from every size band.
    - Unknown is not a miss, and not a pass: a ratio Yahoo has no value
      for is left out of a preview, and a preview resting on under 60% of
      the marks is "thin" and ranks after the well-covered ones.
    - Anything that scales with fame is measured as a share, not a count
      (the governance radar fires when governance is most of what little
      is written about a company, not only at eight stories).
    - In your own judgments: a thin public record is neither a red flag
      nor a clean bill, and fame is not evidence (`/analyse-stock` step
      7.5). Never add a size filter the user did not ask for.
    - When coverage is incomplete the output says so (`read_whole`,
      `ordered_by`, `unread_markets`, `size_mix`) — and so must you.
    - **Blind to size is not blind to risk.** Smaller companies are
      riskier on average, measurably: across 293 cached companies the
      share price's yearly swing ran 34% above $200B and 70% below $300M,
      and loss-making years rose from 6% of the record to 32%. Size is a
      proxy, so risk is measured per company instead of assumed from it
      (`scorers/risk_profile.py`: yearly price swing, worst fall in three
      years, what trades a day, loss years on record) and shown beside
      every score. It moves no pillar and no verdict, but it does discount
      the default ranking: `RankBy.RISK_ADJUSTED` orders a shortlist by
      fundamentals + valuation less `risk_discount` (0–30 points for price
      swings above 30%, loss years, a record under four fiscal years;
      unknown volatility costs 10). `pick` holds a position to a tenth
      of a day's trading and sizes it down as the price swings more
      (`position_scale`: twice the volatility of a calm stock, half the money;
      unmeasured is sized as 50%, not as calm). State the measured risk for every company you
      recommend; never treat unfamiliarity as risk or fame as safety. A
      user who asks for "safe" or "low-risk" is asking about this, and a
      size floor is then a fair reading of the ask.

13. **Followed, or scored in passing.** A company the user asks about by
    name (`analyse`, `refresh <TICKER>`) is *followed*. One that a screen
    or a comparison scores along the way is cached for the next question
    but not followed: nothing about it goes on the signal bus, and
    `brief`, `refresh all` and `backtests --tickers all` leave it out.
    Asking about it by name follows it; once followed, always followed.
    A vague question can score dozens of companies — they must not flood
    the watchlist.

14. **IPOs: two halves, and transcribed is not fetched.** A company that
    has not listed has no Yahoo symbol and no statements feed.
    `data_sources/ipo_source.py` finds its SEC registration (stage, reserved
    ticker, newest prospectus); **you** read the prospectus in
    `/analyse-ipo` and transcribe its statements into
    `data_cache/ipos/<slug>.json` (`ProspectusStatements`); Python scores
    those figures on the same rubric (`snapshot_from_prospectus`,
    `source == "prospectus"`). The report labels them as transcribed, with
    the source document. No offering terms → no valuation (never a price
    from the news). Nothing found at SEC ≠ not filing (confidential drafts
    are invisible; listings outside the US are not covered). An unlisted
    company never enters `scores.json` or the signal bus. Once it trades,
    it is an ordinary `analyse` with a `RECENTLY_LISTED` early signal for
    its first year. When the company is Anthropic, say that you are made
    by Anthropic and hold yourself to the filed numbers only.

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
│   ├── screen.py            #   screen [--market …] [filters] [--score N] [--rank …] — every listed stock,
│   │                        #   every size: whole read → rubric preview → score the best → rank
│   ├── compare.py           #   compare <TICKER…> [--rank …] — named companies side by side
│   │                        #   + render_shortlist (the scorecard table screen prints too)
│   ├── ipo.py               #   ipo <NAME> [--price P] — SEC registration stage + transcribed
│   │                        #   prospectus scored (never written to scores.json)
│   ├── pick.py              #   pick --amount <BUDGET> [--tickers …]  (FX-converted, sector + country caps)
│   ├── brief.py             #   brief
│   └── migrate.py           #   migrate-ids — one-time bare NSE keys → Yahoo symbols
├── analyse_pipeline.py      # analyse_ticker / score_snapshot / AnalysisResult + judgment ageing
├── tracking.py              # record_analysis — persist a run, what changed since the last look,
│                            # and whether the company is followed or was scored in passing
├── scorecard.py             # Scorecard + Shortlist + RankBy (default RISK_ADJUSTED) + score_tickers
│                            # (reuses fresh cache rows; risk_discount / risk_adjusted / scale_vs_largest_pct)
├── preview.py               # preview(): share of the rubric's marks each listing clears, with coverage
├── analysis_context.py      # "Analysis context" + "Changed since …" banners, judgment-age helpers
├── extended_score.py        # ExtendedScore — typed scores.json row (incl. early signals, `followed`)
├── dip_candidate.py         # DipCandidateRecord — typed dip_candidates.json row
├── engine.py                # refresh_one / refresh_all (fundamentals + news radar → tracking.record_analysis)
├── snapshot_store.py        # SnapshotStore — full per-ticker snapshot cache (read by `inspect`)
├── resolver.py              # free-text → ticker on any exchange via Yahoo search
├── judgments.py             # Judgment + JudgmentStore (Claude writes, Python reads)
├── universe.py              # load_universe (followed tickers) / load_scores / load_dip_candidates
├── leader_mode_classifier.py  # LeaderMode StrEnum (4 global modes) + classify_mode(...)
├── budget_parser.py         # Magnitude enum (K / M / B / LAKH / CRORE) + parse_budget
├── ticker.py                # canonical / slug (path-safe name)
├── config.py                # AnalystConfig.from_env()
└── cache.py                 # JsonStore — dict[str, object] boundary

data_sources/                # live data adapters — see README.md
├── fundamentals_source/     # yfinance + screener.in + SEC, split into single-responsibility submodules
│   ├── __init__.py          #   public API only (tests import private helpers from submodules)
│   ├── snapshot.py          #   BalanceSheetSnapshot + SnapshotFields TypedDict + SCORED_FIELDS
│   │                        #   + StatementWindow + DataConflict + is_bank_snapshot (rubric router)
│   │                        #   + PeerRecord / DocumentRecord aliases
│   ├── source.py            #   FundamentalsSource async facade + merge rules + _SCREENER_WINS
│   │                        #   + SEC deep history + one currency / one trailing window per snapshot
│   ├── yfinance_adapter.py  #   yfinance fetcher: fiscal year, trailing twelve months, year-on-year
│   │                        #   balance-sheet changes, multi-year trends (pandas via TYPE_CHECKING)
│   ├── screener_adapter.py  #   screener.in fetcher + section parsers + appliers
│   │                        #   (HTMLParser type via TYPE_CHECKING)
│   ├── sec_adapter.py       #   SEC EDGAR companyfacts → yfinance-shaped annual frames (first-filed)
│   ├── prospectus.py        #   ProspectusStatements (Claude-transcribed, StatementRow lines, Offering)
│   │                        #   + snapshot_from_prospectus → an ordinary snapshot, source "prospectus"
│   ├── valuation.py         #   apply_valuation: P/E, earnings / FCF yield (freshest twelve months),
│   │                        #   P/B, P/E history, spot FX for cross-currency, quote reconciliation
│   ├── history.py           #   StatementHistory + snapshot_as_of + rebase_sec_eps (point-in-time)
│   └── primitives.py        #   parse helpers + period alignment + date-matched YoY
│                            #   + trailing_twelve_months + cagr_pct (PROFIT_WIPED_OUT_CAGR)
├── price_series.py          # PriceSeries — split-adjusted closes + date lookups
│                            # + annualised_volatility_pct / worst_fall_pct
├── universe_source.py       # Yahoo's global screener: Criteria → EquityQuery (one query can span
│                            # every market), Listing rows, Measure / Bound + fetch_symbols ("which
│                            # of these listings clear X?"), market_of(exchange)
├── names.py                 # company-name cleaning shared by resolver, news, and screen
├── ipo_source.py            # SEC registrations: find_registrants / fetch_registration → IpoRegistration
│                            # (IpoStage, reserved ticker, latest prospectus URL — never the document)
├── fx_source.py             # live FX via Yahoo pairs (bridged through USD) + minor units (GBp, ZAc, ILA, KWF)
├── research_source.py       # Wikipedia API + Brave Search (WebHit TypedDict)
└── news_source.py           # Google News RSS: de-noised, de-duplicated stories (NewsItem) +
                             # NewsCategory enum + digest() — a week's story counts (NewsDigest)

scorers/                     # deterministic scoring — see README.md
├── base.py                  # Verdict + Pillar + Gate StrEnums + ScorerResult + StockScore + verdict_for
├── composite_scorer.py      # blend(...) 35 / 15 / 25 / 15 / 10 + integrity gate + deterministic_composite
├── valuation_scorer.py      # valuation pillar: earnings yield + FCF yield (or P/B vs ROE) + P/E vs history,
│                            # on sustainable_earnings (one-offs out, cyclical peaks capped)
├── early_signals.py         # detect_early_signals: concerns + strengths beyond the scored ratios
├── risk_profile.py          # RiskProfile: measured price swings, liquidity, record — facts, no score
│                            # + risk_discount (ranking aid only)
└── fundamentals_scorer/     # dispatches to industrial or bank rubric based on is_bank_snapshot
    ├── __init__.py          #   score_fundamentals entry
    ├── industrial.py        #   industrial rubric + 5 sub-scorers + pledge / growth vetoes
    │                        #   (growth_deceleration_cap is shared with valuation + backtests)
    ├── marks.py             #   the rubrics' own thresholds as screenable questions (INDUSTRIAL_MARKS,
    │                        #   LENDER_MARKS) — what the screen's size-blind preview asks about
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
    ├── early_signals.py     #   concerns / strengths panel, straight under the header
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
├── screen-stocks/SKILL.md   # the entry point for any vague "find me …" ask
├── compare-stocks/SKILL.md  # named companies, or a hand-built shortlist, side by side
├── analyse-ipo/SKILL.md     # a company preparing to list: registration → prospectus → judgment
├── pick-stocks/SKILL.md
├── market-brief/SKILL.md
├── refresh-now/SKILL.md
├── explain-score/SKILL.md
└── backtest/SKILL.md

tests/                       # hermetic, adapter-mocked — see README.md
└── (mirrors the source layout one-for-one, backtests/ included)

backtests/                   # does each scoring claim hold on history? — see README.md
├── __main__.py / cli.py     # python3 -m backtests [SUITE …] --tickers … --years 30 --step year|quarter
│                            # (one listing per company; quotes that don't reconcile are left out)
├── suite.py                 # Suite + SuiteResult + Outcome + judge (|t| ≥ 2 across dates to PASS or FAIL)
├── report.py                # terminal view + Markdown mirror (data_cache/reports/backtest_*.md)
├── engine/                  # schedule · returns · metrics (Observation, rank IC, per-date group test) · runner
└── suites/                  # one claim per module, registered in suites/__init__.py:ALL

data_cache/                  # runtime state (gitignored)
├── scores.json              # per-ticker extended StockScore (engine + analyse write)
├── prices.db                # SQLite — real daily closes from yfinance.history()
├── judgments/<TICKER>.json  # Claude-written qualitative scores
├── snapshots/<TICKER>.json  # full scored snapshot (raw dumps, provenance, conflicts)
├── ipos/<slug>.json         # Claude-transcribed prospectus statements (ProspectusStatements)
├── research/<slug>.json     # cached Wikipedia + Brave bundles
├── news/<TICKER>.json       # per-company news: {ticker, company, window_days, radar, stories}
├── dip_candidates.json      # last pick run output
├── signals.json             # append-only event log (bounded at 500): score updates, verdict
│                            # moves, new early concerns / strengths, dips
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
| `Tone`                | `scorers/early_signals.py`                         | CONCERN · STRENGTH |
| `EarlySignalKind`     | `scorers/early_signals.py`                         | ONE_OFF_PROFIT · PEAK_MARGIN · FREE_CASH_FLOW_FALLING / _RISING · CAPEX_OUTRUNS_CASH_FLOW · STOCK_PAY_HEAVY · REVENUE_SHRINKING / _DECELERATING / _ACCELERATING · MARGIN_COMPRESSING / _EXPANDING · DEBT_RISING / _FALLING · NEGATIVE_EQUITY · SHARE_DILUTION / _BUYBACK · GOVERNANCE_NEWS · UNRELIABLE_QUOTE · RECENTLY_LISTED · … (one per detector) |
| `StatementWindow`     | `data_sources/fundamentals_source/snapshot.py`     | TTM · FISCAL_YEAR (which twelve months a flow figure covers) |
| `IndustrialComponent` | `scorers/fundamentals_scorer/industrial.py`        | BALANCE_SHEET_HEALTH · PROFITABILITY · GROWTH · EARNINGS_QUALITY · OWNERSHIP |
| `BankComponent`       | `scorers/fundamentals_scorer/bank.py`              | PROFITABILITY · ASSET_QUALITY · GROWTH · FRANCHISE · GOVERNANCE |
| `RefreshStatus`       | `fundamental_engine/engine.py`                     | OK · NO_DATA · ERROR |
| `LeaderMode`          | `fundamental_engine/leader_mode_classifier.py`     | FOUNDER_LED · FAMILY_CONTROLLED · STATE_CONTROLLED · PROFESSIONAL (legacy FIRST_GEN / MULTI_GEN_FAMILY still parse) |
| `Magnitude`           | `fundamental_engine/budget_parser.py`              | K · LAKH · MILLION · CRORE · BILLION (float-valued enum) |
| `NewsCategory`        | `data_sources/news_source.py`                      | GOVERNANCE · RATING · INSIDER · LEADERSHIP · CORPORATE_ACTION · EARNINGS (severity order) |
| `Severity`            | `signals/signal_bus.py`                            | INFO · ALERT · URGENT |
| `SignalType`          | `signals/signal_bus.py`                            | UNIVERSE_REFRESHED · SCORE_UPDATED · VERDICT_CHANGED · EARLY_CONCERN · EARLY_STRENGTH · DIP_DETECTED (each has an emitter) |
| `ScreenSort`          | `fundamental_engine/commands/screen.py`            | QUALITY (the rubric preview, the default) · MARKET_CAP (only when size is asked for) · DRAWDOWN · EARNINGS_YIELD |
| `SizeBand`            | `fundamental_engine/commands/screen.py`            | LARGE · MID · SMALL (thirds of a screen's companies, by market cap) |
| `Order`               | `fundamental_engine/commands/screen.py`            | PREVIEW · SIZE_SPREAD (too many to preview) · AS_SORTED |
| `RankBy`              | `fundamental_engine/scorecard.py`                  | FUNDAMENTALS · VALUATION · BOTH · RISK_ADJUSTED (the default: both less `risk_discount`) |
| `IpoStage`            | `data_sources/ipo_source.py`                       | DRAFT · FILED · PRICED · TRADING · WITHDRAWN (read off the SEC filing list) |
| `StatementRow`        | `data_sources/fundamentals_source/prospectus.py`   | REVENUE · COST_OF_REVENUE · … · LONG_TERM_DEBT · CURRENT_DEBT (the lines a prospectus file may carry) |
| `Measure`             | `data_sources/universe_source.py`                  | RETURN_ON_EQUITY · RETURN_ON_CAPITAL · RETURN_ON_ASSETS · DEBT_TO_EQUITY · CURRENT_RATIO · QUICK_RATIO · REVENUE_GROWTH · PROFIT_GROWTH (ratios the screener can be asked about) |
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
| `EarlySignal`         | `scorers/early_signals.py`                     | `scores.json` (the `early_signals` list of each row) |
| `RiskProfile`         | `scorers/risk_profile.py`                      | `scores.json` (the `risk` object of each row) |
| `Scorecard`, `Shortlist` | `fundamental_engine/scorecard.py`           | the `scored` / `not_scored` lists of `screen --json` and `compare --json` (built from `ExtendedScore`, never stored) |
| `NewsItem`, `NewsDigest` | `data_sources/news_source.py`               | `news/<TICKER>.json` |
| `ProspectusStatements` | `data_sources/fundamentals_source/prospectus.py` | `ipos/<slug>.json` (written by Claude in `/analyse-ipo`) |
| `IpoRegistration`, `Registrant` | `data_sources/ipo_source.py`         | none — the `registration` object of `ipo --json` |

Two fields on the score row say what a number is *not*: `unscored` (on
`StockScore`) lists the pillars sitting at the neutral 50 because nothing
could be scored — a judgment not written or expired, a valuation with no
usable price ratio — so a placeholder is never mistaken for an opinion;
`followed` (on `ExtendedScore`) is false for a company scored in passing
(rule 13).

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
missing inputs — but known-bad is scored, not dropped: negative equity
is 0 on the D/E axis, and a profit that became a loss is a −100% PAT
CAGR. Pledge > 10% clamps the score to 40. Growth-deceleration veto caps
the score at 70/60/50 when both 3y CAGRs fall below 7/5/3%. All inputs
are the latest audited fiscal year. A record of only three fiscal years
(a new listing, an IPO) is scored on two years of growth instead of
three (`growth_years`), labelled "2y (short record)" — so growth and the
veto are not silent for it.

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
FCF yield for P/B judged against ROE / 11% cost of equity.

Today's price is set against today's business:
- **FCF yield uses the trailing twelve months** when four consecutive
  quarters exist, else the fiscal year (`cash_flow_window` says which).
- **Earnings are "sustainable earnings"** (`sustainable_earnings`), which
  only ever lowers the profit counted, for the earnings yield and the
  P/E-vs-history:
  - *one-offs out* — Yahoo's normalized ÷ reported net income over the
    P/E's own window (`core_earnings_ratio`); gains are removed, charges
    are never added back, and under 5% of profit is ignored as noise;
  - *cyclical peaks capped* — a core net margin above 1.5× the company's
    own median of the last (up to) six fiscal years counts only up to
    1.5× that median; a company that typically lost money earns no yield
    credit. Needs four fiscal years; lenders are exempt.
  Free cash flow is scored as measured.

Anti-optimism: a loss-maker (over the freshest twelve months) scores 0
on earnings yield, and the **value-trap guard** caps valuation at 70 for
slow growers (the same both-CAGRs-below-7% test as the growth veto) —
cheap because shrinking is not a bargain. The **priced-for-doubt cap** does the
same for a price under 5× repeatable earnings (`DOUBT_PE`): a market that
pays that little for a real profit doubts the profit, the accounts, or that
the money can reach shareholders, so cheaper stops scoring better until the
reason is known. Cross-currency listings are
valued with spot FX once the quote reconciles (rule 9); P/E history is
same-currency only. Per-share numbers are all on today's share basis
(split-adjusted closes; SEC's first-filed EPS is rebased by Yahoo's split
history). Unscoreable or `quote_unreliable` → neutral 50 with zero
confidence.

### Early signals (no weight — facts for you to weigh)

`detect_early_signals(snap, news=…)` runs on every `analyse` / `refresh`
and returns `EarlySignal(kind, tone, severity, message)` records,
concerns first, most severe first. Severity: **URGENT** changes how the
headline numbers should be read, **ALERT** is material, **INFO** is
context. They are shown in the report's "Early signals" panel, cached on
the `scores.json` row, tallied in `brief` / `pick` / `screen`, and when a
material one is new since the last look it goes on the signal bus and
into the "Changed since …" panel.

What they read: the four newest quarters against the same quarter a year
earlier (revenue, operating margin, profit), trailing twelve months
against the fiscal year (free cash flow, capex, stock pay), the latest
balance sheet against the one a year back (debt, receivables, inventory,
goodwill, share count), the ownership tape (insiders, foreign
institutions, retail), bank NPAs, the price against its 52-week high,
how long the price record is (under a year of trading is
`RECENTLY_LISTED`: every price-based measure covers only that stretch and
the insiders' lock-up has usually not ended), and a week of headlines (`news_source.digest`): a governance burst is
several governance-tagged stories AND ≥ 15% of the week's coverage — or,
for a company few outlets cover, two or more stories that are half of
everything written about it (story counts scale with fame; the share
does not).

A strength paid for out of flagged profit is context, not a material
strength: when the profit is padded by one-offs or sits at a cyclical
peak, falling debt, rising free cash flow and widening margins drop to
INFO with a note. (On history, deleveraging at a margin peak came before
*lower* returns — it is what cyclicals do at the top.)

They do not change the fundamentals or valuation score. The backtest
suites `early_concerns` / `early_strengths` / `peak_margin_guard` test
the part that can be replayed on fiscal-year history; report their
outcomes as honestly as any other suite.

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
only. Material negatives (governance, rating) clamp ≤ 35. "Rating" means
credit ratings — a broker's upgrade or downgrade is sell-side opinion.
The pillar expires with its window: a judgment older than 30 days no
longer counts for news (rule 11).

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
`updated_at` matters: it is what ages the judgment (rule 11), so always
write the real current timestamp.

## Conventions

- Python 3.10+, async where it earns its keep
- `@dataclass(slots=True)` + `from __future__ import annotations`
- `logging.getLogger(__name__)`, never bare `print` outside CLI output
- Config lives in `AnalystConfig.from_env()`; never read env vars
  from elsewhere
- Single-writer discipline: aggregate files (`scores.json`,
  `signals.json`) are written through `tracking.record_analysis` (one
  ticker's row at a time, under a file lock) and by `pick` for its dip
  signals. CLI readers are read-only otherwise.
- Tests are hermetic — monkeypatch `_fetch_*` adapters, don't
  create fixture JSONs. `tests/conftest.py` holds the one shared guard:
  an empty news feed, because every refresh reads the news radar

## Don'ts

- Don't import anything from `/Users/nikhilagrawal/stockMarket/trading/`
- Don't add a continuous dashboard or TUI — Rich is only used for
  one-shot terminal reports
- Don't ship a company registry, alias list, ticker list, or holiday
  list. Everything dynamic.
- Don't rank, truncate or sample by market cap, and don't add a size
  floor "to be safe" — in code, in a skill, or in an answer (rule 12).
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

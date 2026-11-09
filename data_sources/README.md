# data_sources/

Live data adapters — every module here is the **only** place in the
project that talks to an external API. No synthetic data, no fixtures,
no hardcoded ticker, company, or market lists.

This is the **lowest non-leaf layer** in the architecture: it depends
only on `signals/` (shared infra) and nothing else. Run `grep -rhE
'^from (scorers|screener|reports|fundamental_engine)' data_sources/`
to confirm — it must always come back empty.

## Layout

```
data_sources/
├── fundamentals_source/     # split by responsibility
│   ├── __init__.py          #   public API only
│   ├── snapshot.py          #   BalanceSheetSnapshot + SnapshotFields TypedDict
│   │                        #   + DataConflict + SCORED_FIELDS + is_bank_snapshot
│   ├── source.py            #   FundamentalsSource facade + merge rules + _SCREENER_WINS
│   │                        #   + provenance (field_sources) + conflict detection
│   ├── yfinance_adapter.py  #   global baseline: every listing on any exchange
│   ├── screener_adapter.py  #   screener.in — Indian (.NS / .BO) listings only
│   ├── sec_adapter.py       #   SEC EDGAR — US filers, ~18 years of first-filed 10-K values
│   ├── valuation.py         #   P/E, earnings / FCF yield, P/B, P/E history (live + backtest)
│   ├── history.py           #   point-in-time snapshots for backtests + SEC EPS rebasing
│   └── primitives.py        #   parse helpers + period alignment + date-matched YoY
├── price_series.py          # PriceSeries — split-adjusted closes + date lookups
├── universe_source.py       # Yahoo's global screener: every listing in 59 markets, filtered server-side
├── fx_source.py             # live FX via Yahoo pairs, bridged through USD + minor units (GBp, ZAc, ILA, KWF)
├── names.py                 # company-name cleaning for search-style queries
├── research_source.py       # Wikipedia API + Brave Search + WebHit TypedDict
└── news_source.py           # Google News RSS + FeedEntry TypedDict + NewsCategory enum
```

## Market independence

- **Tickers are Yahoo symbols** — `MSFT`, `RELIANCE.NS`, `0700.HK`,
  `7203.T`. Adapters take them as-is; no market is ever assumed.
- **Sources select themselves by coverage.** yfinance answers for
  every listing; `screener_adapter.covers(symbol)` is true only for
  `.NS` / `.BO`, and for anything else the adapter returns nothing
  without a request. No code anywhere branches on a market.
- **Listing identity is live.** Exchange, trading currency
  (`currency`), and reporting currency (`financial_currency`) come from
  Yahoo's metadata per security. They differ for ADRs and many Hong
  Kong listings (Tencent trades in HKD, reports in CNY), and London
  quotes in pence (`GBp`) while its market cap is in pounds — see
  `fx_source.major_unit`.
- **Every monetary field is an absolute amount.** Screener's crore
  figures are multiplied out at parse time; market cap is in the major
  unit of the trading currency; statement amounts (quarterly revenue /
  PAT, bank deposits, NII) are in the reporting currency.
- **Valuation never mixes currencies.** `valuation.apply_valuation`
  divides a price by a statement amount (FCF per share, book per share,
  past EPS) only when the trading currency's major unit equals the
  reporting currency; an ADR keeps only the source's published P/E.
- **Every per-share number is on today's share basis.** Closes are
  split-adjusted but not dividend-adjusted (`auto_adjust=False`, Yahoo's
  "Close"): dividend adjustment would quietly shrink every past price
  and P/E. screener and Yahoo already restate past EPS for later splits
  and bonus issues; SEC's first-filed EPS is rebased in
  `history.rebase_sec_eps` by every split after its filing date (Apple's
  FY2013 $39.75 → $1.42). Shares are net income ÷ EPS of the same fiscal
  year (`latest_annual_period`).
- **News uses one global English edition.** The query is the
  company's everyday name (`names.news_query_name`), so "Reliance
  Industries Limited" searches as "Reliance Industries".

## The global universe (screening)

`universe_source` reads whole markets shallowly where
`fundamentals_source` reads one company deeply. Yahoo's equity screener
covers ~80,000 listings in 59 markets; `fetch_page(Criteria(...))`
returns up to 250 of a market's largest listings that pass the filters,
which Yahoo applies server-side to every listing in that market. Markets,
sectors and industries come from yfinance's own lists (`markets()`,
`sectors()`, `industries()`), never from a table here.

Yahoo's conventions, verified live: ratios are currency-free and in
percent (ROE 15 = 15%, debt / equity 50 = 0.5×); a loss-maker carries a
negative P/E, so "P/E at most 20" is queried as 0–20; and amounts are in
each listing's own currency, so `Criteria` takes market-cap bounds in
the market's currency — `fundamental_engine/commands/screen.py`
converts them. `Listing.daily_turnover` (a day's trading ÷ market cap)
is what tells a primary listing from a depositary receipt or foreign
copy that carries another instrument's market cap.

FX: a cross Yahoo doesn't quote (MXN→INR, TRY→INR) is bridged through
the dollar, and Kuwait's fils (`KWF`, 1/1000 of a dinar) join pence,
cents and agorot as minor units.

## Point-in-time history (backtests)

`history.fetch_statement_history(symbol)` gathers the full daily price
history (yfinance: split-adjusted closes for valuation, total-return
closes for returns, and the split events), annual statement frames (SEC
for US filers, else yfinance), and screener's tables (Indian listings).
`history.snapshot_as_of(history, day)` rebuilds the snapshot from only
the fiscal years that ended at least `reporting_lag_days` (default 90)
before `day`, by running the same appliers / fillers as the live path —
so backtest and live scoring share every formula — and prices it at the
last close known that evening. SEC values are first-filed, so
restatements never leak backwards. The backtest suites that consume
this live in the root `backtests/` folder.

## Reliability rules for this layer

- **No silent defaults.** A value the source didn't publish stays
  absent. (Pledge used to default to 0% because screener has no pledge
  row, which scored every company "clean".)
- **NaN / inf never pass the merge.** `_parse_float` / `_parse_pct`
  reject non-finite text, yfinance's CAGR skips NaN endpoints, and
  `_drop_non_finite` strips both adapters' outputs before merging.
- **One period per ratio.** Inputs to a ratio come from the same column
  (`_latest_aligned`) or the same fiscal-year label across tables
  (`_value_at_label`): OCF/PAT, ROA, NIM, margins, and coverage never
  pair TTM with annual or FY profit with half-year assets.
- **One definition per metric.** Interest coverage is core operating
  EBIT / interest on both sources (screener: operating profit −
  depreciation; yfinance: Operating Income). Negative coverage is kept —
  a core operating loss is known-bad, not missing.
- **Quarterly series stay aligned.** A quarter is kept only when every
  series has it, and YoY growth is matched by date, not position.
- **Disagreements are recorded, not hidden.** When both adapters answer
  a scored field and differ materially, the merge keeps the preferred
  source and logs a `DataConflict`; `field_sources` names the source of
  every populated field.
- **Bank asset quality.** Screener's bank quarterly table carries Gross /
  Net NPA %. Banks report them standalone only, so when the
  consolidated table's NPA cells are empty the adapter reads the
  standalone page.

## Conventions

- **Every public async method is mockable at the submodule level.**
  Tests monkeypatch `yfinance_adapter._fetch_yfinance_sync`,
  `screener_adapter._fetch_screener_in`,
  `universe_source._fetch_screen_sync`, and
  `fx_source._fetch_major_rate_sync`. `source.py` imports the adapter
  *modules* so attribute lookups happen at call time.
- **Screener is authoritative where it has coverage** — D/E (from
  `Borrowings`, not yfinance's lease-inflated "Total Debt"), ROE / ROCE,
  margins, OCF/PAT, FCF/PAT, working-capital days, 3y CAGRs, quarters.
  See `_SCREENER_WINS`.
- **TTM is trimmed before any CAGR** (`_drop_ttm`); latest-value metrics
  still use TTM.
- **Bank / NBFC row labels.** Banks use "Borrowing" (singular) and
  "Financing Profit"; the parser accepts both shapes.
- **This layer never reads environment variables** — keys such as
  `BRAVE_API_KEY` are passed in from `AnalystConfig`.

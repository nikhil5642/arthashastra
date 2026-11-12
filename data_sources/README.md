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
│   ├── snapshot.py          #   BalanceSheetSnapshot + SnapshotFields TypedDict + StatementWindow
│   │                        #   + DataConflict + SCORED_FIELDS + is_bank_snapshot
│   ├── source.py            #   FundamentalsSource facade + merge rules + _SCREENER_WINS
│   │                        #   + provenance (field_sources) + conflict detection
│   │                        #   + one currency and one trailing window per snapshot
│   ├── yfinance_adapter.py  #   global baseline: every listing on any exchange — fiscal year,
│   │                        #   trailing twelve months, year-on-year balance sheet, long trends
│   ├── screener_adapter.py  #   screener.in — Indian (.NS / .BO) listings only
│   ├── sec_adapter.py       #   SEC EDGAR — US filers, ~18 years of first-filed 10-K values
│   ├── prospectus.py        #   ProspectusStatements (Claude-transcribed) → snapshot_from_prospectus
│   ├── valuation.py         #   P/E, earnings / FCF yield, P/B, P/E history (live + backtest),
│   │                        #   spot FX for cross-currency listings, quote reconciliation
│   ├── history.py           #   point-in-time snapshots for backtests + SEC EPS rebasing
│   └── primitives.py        #   parse helpers + period alignment + date-matched YoY
│                            #   + trailing_twelve_months + cagr_pct
├── price_series.py          # PriceSeries — split-adjusted closes + date lookups
├── universe_source.py       # Yahoo's global screener: every listing in 59 markets, filtered server-side;
│                            # one query can span all of them; Measure / Bound + fetch_symbols
├── ipo_source.py            # SEC registrations: who filed to list, IpoStage, newest prospectus URL
├── fx_source.py             # live FX via Yahoo pairs, bridged through USD + minor units (GBp, ZAc, ILA, KWF)
├── names.py                 # company-name cleaning for search-style queries
├── research_source.py       # Wikipedia API + Brave Search + WebHit TypedDict
└── news_source.py           # Google News RSS → de-noised, de-duplicated stories (NewsItem)
                             # + NewsCategory enum + digest() → NewsDigest (a week's counts)
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
  divides a price by a statement amount only when both are in one
  currency. When the trading and reporting currencies differ (an ADR, a
  Hong Kong share of a mainland company, a London share with dollar
  accounts) the live path converts the market cap with spot FX, so FCF
  yield and P/B still exist; backtests have no past FX rate and leave
  them out. P/E history is same-currency only.
- **A cross-currency quote must reconcile before it is used.** Two
  tests, and failing either marks the snapshot `quote_unreliable` and
  discards every price-based ratio: a profitable company cannot trade
  below 1.5× earnings (`MIN_PLAUSIBLE_PE`), and the FX-converted market
  cap ÷ profit must land within 3× of the source's own P/E
  (`RECONCILE_TOLERANCE`). This catches depositary receipts whose
  "market cap" is the receipt programme's (Argentine CEDEARs, Singapore
  and Canadian receipts) and statements carrying the wrong currency
  label. Wrong numbers are worse than none.
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

## Fresher than the fiscal year

The scored ratios describe the latest audited fiscal year, which can be
most of a year old. The snapshot also carries what has been published
since, and a longer look back, for the valuation pillar and the early
signals (`scorers/early_signals.py`):

| What | Fields | Rule |
|---|---|---|
| Trailing twelve months | `ttm_period_end`, `ttm_revenue`, `ttm_net_income`, `ttm_operating_cash_flow`, `ttm_capex`, `ttm_free_cash_flow`, `ttm_stock_based_compensation` | The sum of four *consecutive* quarters (`primitives.trailing_twelve_months`) or nothing — a missing middle quarter, or a half-yearly reporter, gets no TTM. All `ttm_` figures share one window. |
| Which twelve months a ratio used | `earnings_window`, `cash_flow_window` (`StatementWindow`) | Set by `apply_valuation`; `snap.trailing(name)` returns the trailing figure when it is newer than the fiscal year, else the fiscal-year one, with its window. |
| Fiscal-year flows | `revenue`, `net_income`, `operating_cash_flow`, `capex`, `free_cash_flow`, `stock_based_compensation` | Absolute amounts in the reporting currency; capex is positive. |
| Core earnings | `core_earnings_ratio_fy`, `core_earnings_ratio_ttm` | Yahoo's "Normalized Income" ÷ net income for that window. Only where Yahoo publishes it — SEC frames and screener have no such row, so the ratio is absent there, never assumed to be 1. |
| Latest quarters | `quarterly_dates`, `quarterly_revenue`, `quarterly_pat`, `quarterly_operating_margin`, `latest_yoy_revenue_growth`, `latest_yoy_pat_growth` | Newest first, aligned across series; year-on-year is matched by date (`change_by_date`), never by position. |
| Balance sheet, a year on | `balance_sheet_date`, `debt_change_1y_pct`, `latest_debt_to_equity`, `debt_to_equity_1y_ago`, `receivables_change_1y_pct`, `inventory_change_1y_pct`, `goodwill_change_1y_pct`, `latest_goodwill_to_equity`, `share_count_change_1y_pct` | The latest quarterly balance sheet against the one a year earlier (the annual ones when there are no quarters). The share-count change is skipped across a stock split. |
| Long trends | `sales_annual_trend`, `net_profit_annual_trend`, `operating_margin_annual_trend`, `net_margin_annual_trend`, `roce_annual_trend` | Oldest first. Yahoo has four fiscal years; SEC extends US filers to ten; screener gives Indian listings about twelve. |

SEC in the live path: `FundamentalsSource(sec_user_agent=…)` fetches
SEC's annual frames for US filers and uses them **only** for the long
trends — a current ratio never comes from SEC. The source tag reads
`yfinance+sec`.

SEC history follows the registrant, not the ticker. A company that
reorganised under a new registrant starts over there — Alphabet in 2015,
Broadcom in 2018 — and one that has just done so has no annual facts at
all until its first 10-K (ExxonMobil Holdings in 2026: 19 fiscal years
sit under the old registrant, none under the new). SEC publishes no link
between the two, so the gap is logged and Yahoo's four years are used;
it is never bridged with a hardcoded identifier.

## News

`NewsSource.fetch_for_company(name)` runs one exact-phrase Google News
query. The raw feed is mostly noise, so before anything reads it:

- quote pages, chart pages and 13F filing spam are dropped
  (`_NOISE_PATTERN`);
- syndicated copies of one headline are merged into one story whose
  `coverage` counts the outlets that carried it;
- the publisher's name is taken from the feed and stripped from the
  headline, dates are ISO, stories are newest first;
- each story is tagged with a `NewsCategory` by keywords matched at
  word starts, in severity order. `RATING` means credit ratings only —
  a broker's upgrade or downgrade is opinion, not a rating action.

`digest(stories)` counts the last seven days' stories per category
(`NewsDigest`: total stories, and per category the count, the most
widely carried headline and its outlet count). Those counts are the
facts behind the governance and credit-rating early signals; judging
what the stories mean is Claude's job in `/analyse-stock`.

## The global universe (screening)

`universe_source` reads whole markets shallowly where
`fundamentals_source` reads one company deeply. Yahoo's equity screener
covers ~80,000 listings in 59 markets; `fetch_page(Criteria(...))`
returns up to 250 listings that pass the filters, which Yahoo applies
server-side to every listing. Markets, sectors and industries come from
yfinance's own lists (`markets()`, `sectors()`, `industries()`), never
from a table here.

Three things make a screen complete rather than a view of the largest:

- **One query spans many markets.** `Criteria.markets` takes one market
  or all 59; the result is exactly what the same query returns market by
  market (verified live: 250 of 250 for one industry worldwide), paged
  250 at a time as deep as needed. A narrow screen is a request or two,
  and it holds every match of every size. `market_of(exchange_code)`
  says which market a listing came from, from yfinance's own table.
- **A failed request is `None`, never an empty page.** "Yahoo did not
  answer" and "nothing matched" must stay different, or a screen silently
  loses a market and still looks complete.
- **Yahoo filters on more than it returns.** `Measure` names eight
  ratios (returns on equity, capital and assets; debt / equity; current
  and quick ratios; revenue and profit growth); a `Bound` puts one into a
  query — a floor, a ceiling, or neither, which selects every listing
  Yahoo has a value for; and `fetch_symbols(criteria)` answers "which of
  this screen's listings pass?" That set-membership question is what the
  size-blind rubric preview is built from (`fundamental_engine/preview.py`).

`Listing.implied_roe_pct` (earnings per share ÷ book value per share) is
the one quality figure a quote carries by itself; it is None for a
listing that trades in another currency than it reports in.

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

## Companies that have not listed

`ipo_source` answers "has this company filed to list, and how far along
is it?" from SEC EDGAR: `find_registrants(name)` searches S-1 / F-1
filers, `fetch_registration(cik)` reads the filing list into an
`IpoRegistration` — `stage` (`IpoStage`: DRAFT / FILED / PRICED /
TRADING), reserved ticker, `latest_prospectus`, `first_public_filing`,
`amendments`. It never fetches the prospectus document (SEC's archive
refuses generic clients); Claude reads it in `/analyse-ipo`. Nothing
found means nothing *public* at SEC — confidential drafts and listings
outside the US are invisible here.

`fundamentals_source/prospectus.py` takes what Claude transcribed —
`ProspectusStatements`, one value per `StatementRow` per fiscal year,
`amounts_in` the tables' multiplier, the `Offering` terms — and builds
an ordinary `BalanceSheetSnapshot` through the same SEC-shaped frames and
`snapshot_as_of` fillers, so it is scored by the same formulas.
`snap.source` is `"prospectus"`. Valuation exists only when a price (the
range midpoint, or an override) and the shares after the offering are
known; per-share figures use that post-offering count. No price history,
no quarters: those measures stay None.

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
  depreciation; yfinance: Operating Income — never Yahoo's "EBIT" row,
  which carries investment gains and other income). Negative coverage is
  kept — a core operating loss is known-bad, not missing.
- **Known-bad is not unknown.** A profit that turned into a loss has no
  growth rate, so `cagr_pct(..., wiped_out_counts=True)` returns
  `PROFIT_WIPED_OUT_CAGR` (−100%) instead of nothing; a loss that
  narrowed or a profit from a loss stays unknown. Negative equity is
  kept on the snapshot so the scorer can say so.
- **One trailing window.** When screener supplies the trailing twelve
  months, yfinance's sums over a different window are dropped
  (`_keep_one_trailing_window`) — a ratio never divides one source's
  twelve months by another's.
- **One currency per merge.** Screener's statements are in rupees. When
  Yahoo reports the same company in another currency (Infosys files in
  dollars), yfinance's absolute amounts are dropped and only its ratios
  kept (`_in_screeners_currency`).
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
  `universe_source._fetch_screen_sync` (return `None` to play a request
  Yahoo did not answer), and `fx_source._fetch_major_rate_sync`. `source.py` imports the adapter
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

# data_sources/

Live data adapters — every module here is the **only** place in the
project that talks to an external API. No synthetic data, no fixtures,
no hardcoded ticker or company lists.

## Modules

| Module | External source | What it returns |
|---|---|---|
| `fundamentals_source.py` | yfinance (`.history()`, `.balance_sheet`, `.income_stmt`, `.cashflow`, `.quarterly_income_stmt`) + screener.in HTML scrape | `BalanceSheetSnapshot` (14 scored ratios: leverage/liquidity/solvency/profitability/growth/earnings-quality/promoter; plus non-scored **Financial Profile** — margin stack, cost structure, working-capital days, asset composition, 5-quarter revenue/PAT trend + YoY growth) **and** the raw daily close rows, bundled into a `FetchResult` |
| `research_source.py` | Wikipedia (`wikipedia-api`) + Brave Search API (optional, requires `BRAVE_API_KEY`) | `ResearchBundle` — biography text + web hit snippets for a leader |
| `news_source.py` | Google News RSS per-company queries + market-wide RSS (ET, Moneycontrol) | `NewsItem` list with a keyword-based `is_material` gate |

## Conventions for this layer

- **Every public async method is mockable at the sync adapter level.**
  Tests monkeypatch `_fetch_yfinance_sync`, `_fetch_screener_in`,
  `_fetch_wikipedia_sync`, `_fetch_brave_search`, `_fetch_feed_sync`.
  No network in tests.
- **Screener.in is authoritative for every Indian-specific ratio it exposes** —
  D/E (from the `Borrowings` row, not yfinance's lease-inflated "Total Debt"),
  interest coverage, operating/net/tax margins, OCF/PAT, FCF/PAT, working-
  capital days, 3y CAGRs, and the full quarterly trend. yfinance fills gaps
  (current/quick ratio, net debt / EBITDA, price history, sector/industry).
- **TTM column is trimmed before computing any CAGR.** Screener appends a
  `TTM` column to the P&L which mixes a partial fiscal year with a rolling
  12-month window. `_drop_ttm(headers, values)` removes it so the 3y window
  uses the last full annual point. Latest-value metrics (margins, interest
  coverage, tax rate) still use TTM since it's the most recent period.
- **Bank / NBFC row labels.** Banks use the singular "Borrowing" instead of
  "Borrowings+", and their operating-profit row is called "Financing Profit"
  (which is often negative by construction). `_apply_balance_sheet` now
  accepts both labels.
- **Cell-position alignment.** `_parse_section` pads missing cells with
  `None` so row positions map 1-to-1 with headers, which means dropping
  a column by name (e.g. TTM) doesn't shift the CAGR window.
- **Quarterly values are stored in rupees regardless of source.** yfinance
  publishes rupees directly; screener publishes crores and we multiply by
  `1e7` at parse time so the downstream report renderer (which divides by
  `1e7` for Cr display) works identically for both paths.
- **Interest coverage requires EBIT > 0.** A loss-making company has no
  coverage regardless of the numeric ratio; we drop the field entirely.
- **Timestamps are parsed to naive UTC** everywhere in `news_source`
  so the window filter behaves identically regardless of the host
  machine's local timezone.
- **Graceful degradation:** if one adapter fails, the other still
  populates what it can. The `source` field on the snapshot records
  which adapters contributed.

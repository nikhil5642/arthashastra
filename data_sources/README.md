# data_sources/

Live data adapters — every module here is the **only** place in the
project that talks to an external API. No synthetic data, no fixtures,
no hardcoded ticker or company lists.

This is the **lowest non-leaf layer** in the architecture: it depends
only on `signals/` (shared infra) and nothing else. Run `grep -rhE
'^from (scorers|screener|reports|fundamental_engine)' data_sources/`
to confirm — it must always come back empty.

## Layout

```
data_sources/
├── fundamentals_source/     # split by responsibility
│   ├── __init__.py          #   public API re-exports
│   ├── snapshot.py          #   BalanceSheetSnapshot + SCORED_FIELDS + is_bank_snapshot
│   ├── source.py            #   FundamentalsSource facade + merge rules + _SCREENER_WINS
│   ├── yfinance_adapter.py  #   yfinance fetcher + section fillers (with _yf_symbol helper)
│   ├── screener_adapter.py  #   screener.in fetcher + section parsers + appliers
│   └── primitives.py        #   parse helpers + _drop_ttm + _screener_cagr
├── research_source.py       # Wikipedia API + Brave Search (optional)
└── news_source.py           # Google News RSS per-company + market RSS
```

## Modules

| Module | External source | What it returns |
|---|---|---|
| `fundamentals_source/` | yfinance (`.history()`, `.balance_sheet`, `.income_stmt`, `.cashflow`, `.quarterly_income_stmt`, `.info`) + screener.in HTML scrape | `BalanceSheetSnapshot` — 14 scored ratios, full **Financial Profile** (margin stack, cost mix, working-capital days, asset composition, quarterly trend, multi-year ratio trends, shareholding trend), bank-specific fields (NIM, NII, CD ratio) populated when the snapshot is a bank, the yfinance analyst bundle (target + recommendation), and full raw section dumps (`raw_profit_loss`, `raw_balance_sheet`, …) keyed by screener row label for ad-hoc downstream queries |
| `research_source.py` | Wikipedia (`wikipedia-api`) + Brave Search API (optional, requires `BRAVE_API_KEY`) | `ResearchBundle` — biography text + web hit snippets for a leader |
| `news_source.py` | Google News RSS per-company queries + market-wide RSS (ET, Moneycontrol) | `NewsItem` list with a keyword-based `is_material` gate |

## Conventions for this layer

- **Every public async method is mockable at the submodule level.**
  Tests monkeypatch adapter functions on the submodules where they
  live — `yfinance_adapter._fetch_yfinance_sync` and
  `screener_adapter._fetch_screener_in`. `source.py` imports the
  adapter *modules* (not functions) so attribute lookups are deferred
  to call time, making monkeypatching actually take effect.
- **No upward imports.** `data_sources/` must not import from
  `scorers/`, `screener/`, `reports/`, or `fundamental_engine/`.
  yfinance-symbol formatting (`_yf_symbol`) is a private helper inside
  `yfinance_adapter.py` — it used to reach into `fundamental_engine.
  ticker.yf_symbol` which was a layering violation; fixed now.
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

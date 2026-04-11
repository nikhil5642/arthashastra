# data_sources/

Live data adapters — every module here is the **only** place in the
project that talks to an external API. No synthetic data, no fixtures,
no hardcoded ticker or company lists.

## Modules

| Module | External source | What it returns |
|---|---|---|
| `fundamentals_source.py` | yfinance (`.history()`, `.balance_sheet`, `.income_stmt`, `.cashflow`) + screener.in HTML scrape | `BalanceSheetSnapshot` (the 9 scored fields + price/market-cap context) **and** the raw daily close rows, bundled into a `FetchResult` |
| `research_source.py` | Wikipedia (`wikipedia-api`) + Brave Search API (optional, requires `BRAVE_API_KEY`) | `ResearchBundle` — biography text + web hit snippets for a leader |
| `news_source.py` | Google News RSS per-company queries + market-wide RSS (ET, Moneycontrol) | `NewsItem` list with a keyword-based `is_material` gate |

## Conventions for this layer

- **Every public async method is mockable at the sync adapter level.**
  Tests monkeypatch `_fetch_yfinance_sync`, `_fetch_screener_in`,
  `_fetch_wikipedia_sync`, `_fetch_brave_search`, `_fetch_feed_sync`.
  No network in tests.
- **Screener.in fields win over yfinance** for ROE, ROCE, promoter
  holding, pledge %, market cap — screener's pre-computed Indian-
  specific ratios are more reliable than our statement-based arithmetic.
- **Interest coverage requires EBIT > 0.** A loss-making company has no
  coverage regardless of the numeric ratio; we drop the field entirely.
- **Timestamps are parsed to naive UTC** everywhere in `news_source`
  so the window filter behaves identically regardless of the host
  machine's local timezone.
- **Graceful degradation:** if one adapter fails, the other still
  populates what it can. The `source` field on the snapshot records
  which adapters contributed.

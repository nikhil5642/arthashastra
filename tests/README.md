# tests/

Hermetic test suite — **no network, no fixture JSON files, no real API
keys**. Every live adapter is monkeypatched at the module level.

```bash
pytest
```

128 tests, runs in under 2 seconds.

## Layout

Mirrors the source layout one-for-one:

```
tests/
├── conftest.py                          # sets ANALYST_ENV=sandbox; nothing else
├── data_sources/
│   ├── test_fundamentals_source.py      # mocks _fetch_yfinance_sync + _fetch_screener_in
│   ├── test_research_source.py          # mocks _fetch_wikipedia_sync + _fetch_brave_search
│   └── test_news_source.py              # mocks _fetch_feed_sync
├── fundamental_engine/
│   ├── test_analyse_pipeline.py         # mocks FundamentalsSource
│   ├── test_engine.py                   # mocks FundamentalsSource + verifies prices.db
│   ├── test_resolver.py                 # mocks _resolve_sync
│   ├── test_judgments.py                # pure I/O, tmp_path
│   ├── test_universe.py                 # pure I/O, tmp_path
│   ├── test_ticker.py                   # pure logic
│   ├── test_budget_parser.py            # pure logic
│   └── test_leader_mode_classifier.py   # pure logic, date-relative years
├── scorers/
│   ├── test_fundamentals_scorer.py      # inline BalanceSheetSnapshot
│   └── test_composite_scorer.py         # inline ScorerResult
├── screener/
│   ├── test_dip_detector.py             # inline StockScore + PriceSnapshot
│   └── test_price_history.py            # SQLite :memory:, real series
├── reports/
│   ├── test_stock_report.py             # inline snapshot + blended score
│   ├── test_picks_report.py             # seed scores.json + prices.db, run command
│   └── test_market_brief.py             # seed cache, run command
└── signals/
    └── test_signal_bus.py               # tmp_path, round-trip
```

## Rules

- **No JSON fixture files.** Test inputs are constructed inline in the
  test body (explicit `BalanceSheetSnapshot(...)`, `StockScore(...)`,
  etc.). If a test writes to the cache it uses `tmp_path` or `:memory:`.
- **Monkeypatch adapters, not classes.** Each live data source exposes
  a top-level `_fetch_*` function. Tests replace those to simulate API
  responses; the rest of the source code runs unmodified.
- **No LLM mocking infrastructure.** There is no LLM client in Python
  to mock. Qualitative pillars are tested via `pending_judgment()`
  placeholders and a hand-written `Judgment` object in
  `test_analyse_pipeline.py::test_analyse_ticker_with_judgment_merges`.
- **Tests that touch environment variables use `monkeypatch.setenv()`**
  and a `tmp_path` for `DATA_CACHE_DIR`, so parallel runs don't
  interfere.

## Adding a new test

1. Decide if it's a unit test (pure logic, inline inputs) or an
   integration test (runs a command against a primed cache).
2. For integration tests, always set `DATA_CACHE_DIR=tmp_path/cache`
   via monkeypatch so the real project `data_cache/` is untouched.
3. For data source tests, patch the `_fetch_*` function on the module
   object (`monkeypatch.setattr(fundamentals_source, "_fetch_yfinance_sync", fake)`).
4. Construct test inputs explicitly. Never load JSON.

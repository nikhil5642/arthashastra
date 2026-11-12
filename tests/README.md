# tests/

Hermetic test suite — **no network, no fixture JSON files, no real API
keys**. Every live adapter is monkeypatched at the module level.

```bash
pytest
```

~650 tests, ~97% line coverage, runs in a few seconds.

## Layout

Mirrors the source layout one-for-one:

```
tests/
├── conftest.py                          # one guard: an empty news feed (no fixture data)
├── data_sources/
│   ├── test_fundamentals_source.py      # mocks _fetch_yfinance_sync + _fetch_screener_in;
│   │                                    #   merge, provenance, conflicts, alignment, parsers,
│   │                                    #   one currency / one trailing window, SEC deep history
│   ├── test_primitives.py               # trailing twelve months, consecutive quarters,
│   │                                    #   date-matched changes, wiped-out-profit CAGR
│   ├── test_screener_page.py            # full inline screener pages (industrial + bank)
│   ├── test_yfinance_adapter.py         # fake Samsung ticker built from DataFrames; TTM,
│   │                                    #   balance-sheet changes, split guard, core earnings
│   ├── test_sec_adapter.py              # inline companyfacts payloads
│   ├── test_history.py                  # point-in-time snapshots, no look-ahead, SEC EPS rebasing
│   ├── test_valuation.py                # P/E, yields, P/B, P/E history; ADRs with spot FX, pence,
│   │                                    #   as-of pricing, quotes that don't reconcile
│   ├── test_fx_source.py                # mocks _fetch_major_rate_sync; minor units, USD bridge
│   ├── test_universe_source.py          # real EquityQuery building; mocks _fetch_screen_sync;
│   │                                    #   multi-market queries, Bound conditions, a failure ≠ empty
│   ├── test_names.py                    # pure logic
│   ├── test_research_source.py          # mocks _fetch_wikipedia_sync + _fetch_brave_search
│   └── test_news_source.py              # mocks _fetch_feed_sync; noise, syndication, digest
├── backtests/                           # the root backtests/ package
│   ├── engine/                          # schedule, returns, metrics (known answers), runner
│   ├── test_suite.py                    # judging rules + the two claim shapes
│   ├── test_suites.py                   # every suite PASSes when its claim holds, FAILs reversed
│   └── test_cli.py                      # python3 -m backtests end to end, fetch monkeypatched
├── fundamental_engine/
│   ├── test_analyse_pipeline.py         # mocks FundamentalsSource; integrity gate, judgment ageing
│   ├── test_tracking.py                 # what is recorded, and what counts as new since the last look
│   ├── test_analysis_context.py         # the "Analysis context" and "Changed since" banners
│   ├── test_cli.py                      # every subcommand through main(argv)
│   ├── test_persistence_edges.py        # corrupt / partial JSON, config, coercions
│   ├── test_brief_command.py            # seed cache, run command; early signals, recent changes
│   ├── test_cli_inspect.py              # seed snapshot, run `inspect`
│   ├── test_engine.py                   # mocks FundamentalsSource + verifies prices.db
│   ├── test_migrate_command.py          # legacy cache → Yahoo symbols, once
│   ├── test_pick_command.py             # seed scores.json + prices.db, run command
│   ├── test_screen_command.py           # a fake Yahoo screener that answers reads AND the preview's
│   │                                    #   questions; whole reads, size bands, failures named
│   ├── test_preview.py                  # the arithmetic of "how many marks does it clear", coverage
│   ├── test_scorecard.py                # cards, ranking, reuse of fresh cache rows, fetch concurrency
│   ├── test_compare_command.py          # primed cache, the real table and JSON
│   ├── test_resolver.py                 # mocks yfinance.Search
│   ├── test_judgments.py                # pure I/O, tmp_path
│   ├── test_snapshot_store.py           # pure I/O, tmp_path
│   ├── test_universe.py                 # pure I/O, tmp_path
│   ├── test_ticker.py                   # pure logic
│   ├── test_budget_parser.py            # pure logic
│   └── test_leader_mode_classifier.py   # pure logic
├── scorers/
│   ├── test_fundamentals_scorer.py      # inline BalanceSheetSnapshot; global scenarios
│   ├── test_scoring_curves.py           # every band of every curve
│   ├── test_marks.py                    # the screening marks sit where the scoring curves say
│   ├── test_valuation_scorer.py         # hand-computed blends, bank P/B, value-trap guard,
│   │                                    #   one-offs removed, cyclical peaks capped
│   ├── test_early_signals.py            # every detector: fires past its threshold, silent below
│   │                                    #   it, silent on missing data
│   └── test_composite_scorer.py         # inline ScorerResult
├── screener/
│   ├── test_dip_detector.py             # inline StockScore + PriceSnapshot
│   └── test_price_history.py            # SQLite :memory:, real series
├── reports/
│   ├── test_formatting.py               # money formatting per currency
│   └── test_stock_report.py             # inline snapshot + blended score
└── signals/
    ├── test_fileio.py                   # atomic writes + locks
    └── test_signal_bus.py               # tmp_path, round-trip, corrupt rows
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
- **One shared guard, no shared data.** Every refresh reads the news
  radar, so `conftest.py` swaps the feed library for one that returns an
  empty feed (autouse). A test about news installs its own entries with
  `monkeypatch.setattr(news_source, "_fetch_feed_sync", …)`.
- **A threshold is tested on both sides, and on nothing.** For a
  detector or a veto: one case just past the threshold fires, one just
  short of it doesn't, and one with the input missing produces neither a
  concern nor a strength.

## Adding a new test

1. Decide if it's a unit test (pure logic, inline inputs) or an
   integration test (runs a command against a primed cache).
2. For integration tests, always set `DATA_CACHE_DIR=tmp_path/cache`
   via monkeypatch so the real project `data_cache/` is untouched.
3. For data source tests, patch the `_fetch_*` function on the module
   object (`monkeypatch.setattr(fundamentals_source, "_fetch_yfinance_sync", fake)`).
4. Construct test inputs explicitly. Never load JSON.

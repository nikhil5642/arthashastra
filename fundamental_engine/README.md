# fundamental_engine/

**Orchestrator layer** — CLI entry point, configuration, analysis
pipeline, background cache warmer, command handlers, and shared
helpers. This is the **top of the stack**: it wires together
`data_sources/`, `scorers/`, `screener/`, `reports/`, and `signals/`.
Nothing below this layer imports from here.

Apart from `resolver.py` (Yahoo search), this package makes no network
calls of its own — it delegates HTTP to `data_sources/` and scoring to
`scorers/`.

## Layout

```
fundamental_engine/
├── __main__.py              # argparse + dispatch (thin)
├── commands/                # vertical-slice CLI handlers
│   ├── __init__.py
│   ├── screen.py            #   screen [--market …] [filters] [--score N]
│   ├── pick.py              #   pick --amount <BUDGET> [--tickers …]
│   ├── brief.py             #   brief
│   └── migrate.py           #   migrate-ids (one-time)
├── analyse_pipeline.py      # analyse_ticker / score_snapshot / AnalysisResult + judgment ageing
├── tracking.py              # record_analysis — persist a run + what changed since the last look
├── analysis_context.py      # analysis_context_panel + changes_panel + judgment-age helpers
├── extended_score.py        # ExtendedScore typed record for scores.json rows (incl. early signals)
├── dip_candidate.py         # DipCandidateRecord typed record for dip_candidates.json
├── engine.py                # refresh_one / refresh_all / background cache warmer
├── snapshot_store.py        # SnapshotStore — full per-ticker snapshot cache
├── resolver.py              # free-text → ticker on any exchange
├── judgments.py             # Judgment + JudgmentStore (typed JSON boundary)
├── universe.py              # load_scores / load_dip_candidates typed loaders
├── leader_mode_classifier.py  # LeaderMode StrEnum + classify_mode
├── budget_parser.py         # Magnitude StrEnum + parse_budget
├── ticker.py                # canonical / slug
├── config.py                # AnalystConfig.from_env()
└── cache.py                 # JsonStore — dict[str, object] boundary
```

## Modules

| Module | Purpose |
|---|---|
| `__main__.py` | `python3 -m fundamental_engine <subcommand>` router. Subcommands: `resolve`, `refresh`, `analyse`, `inspect`, `news`, `research`, `screen`, `pick`, `brief`, `engine`, `migrate-ids`. Each handler is a thin function that parses args and delegates to a command module or pipeline. `news <TICKER> --json` prints `{ticker, company, window_days, radar, stories}`: `stories` are de-duplicated and newest first, `radar` is the last week's count per material category. |
| `commands/screen.py` | `run_screen_command(ScreenRequest)` — screens every listed stock in the chosen markets (default all 59) through `data_sources.universe_source`: Yahoo applies the ratio filters server-side; here market-cap bounds are converted per market and re-checked per listing in the base currency, depositary receipts and foreign copies are skipped (< 0.001% of market cap traded a day, or a cross-currency quote under 1.5× earnings — `_receipt_quote`), and each company keeps one listing: its home-currency listing first, then the most traded. `--score N` runs the top N through `Engine.refresh_one` so they join the cache, and the scored table shows each name's early-signal tally. `ScreenSort` StrEnum orders by market cap, drawdown, or earnings yield. |
| `commands/pick.py` | `run_pick_command(amount, …, tickers=None)` — budget in `BASE_CURRENCY`; converts each listing's price with its cached FX rate, blocks integrity-vetoed tickers, runs the dip gate against `prices.db`, diversifies by sector and country, allocates, emits `DIP_DETECTED` signals, and persists `DipCandidateRecord` rows. Early signals never block a pick (a dip usually comes with concerns), but every pick shows its tally and urgent concerns are spelled out under the table. `tickers` narrows the universe (e.g. to a screen's scored names) and names any that aren't cached. |
| `commands/brief.py` | `run_brief_command()` — top names with their early-signal tally, early concerns and strengths across the cache (the most severe per ticker; INFO stays in each stock's own report), what changed in the last 14 days (verdict moves, new concerns and strengths from the signal log), dip candidates, sector coverage, integrity vetoes, stale rows; terminal panels + Markdown mirror. |
| `commands/migrate.py` | `run_migrate_command(dry_run)` — one-time re-key of legacy bare NSE tickers to Yahoo symbols across every cache file, with a backup and a never-twice marker. |
| `snapshot_store.py` | `SnapshotStore` — `analyse` / `refresh` save the full scored snapshot (raw dumps, provenance, conflicts) to `data_cache/snapshots/<TICKER>.json`; `inspect` reads it. |
| `analyse_pipeline.py` | `analyse_ticker()` (fetch + news radar + score), `score_snapshot()` (pure, no I/O), `news_digest_for(snap)` (the week's story counts; None when the feed can't be read), and the `AnalysisResult` wrapper, which carries the `early_signals`. Judgment ageing lives here (`_judged`): a news judgment older than 30 days is neutral with zero confidence, leadership / prospects older than 90 days count at confidence 0.5 with a STALE line, an integrity veto never expires, and an unreadable `updated_at` counts as old. |
| `tracking.py` | `record_analysis(result, cfg, fx_rate_to_base=…)` — the one way a run is persisted: the `scores.json` row, the full snapshot, the daily closes, and the signal-bus rows. Because it sees the old row and the new one, it is also where change is noticed: `changes_since(previous, current)` returns a `VERDICT_CHANGED` signal when the band moved and an `EARLY_CONCERN` / `EARLY_STRENGTH` signal for each material early signal that is new or more severe than last time. `analyse`, `refresh`, `screen --score` and the engine all end here. |
| `analysis_context.py` | `analysis_context_panel(result, ticker, price_rows_written)` — the banner shown at the top of every `analyse` run — and `changes_panel(recorded)`, the "Changed since <date>" panel (None on a first look or when nothing moved). Also `judgment_age_days()` + `format_age()` helpers used by the STALE warning hint. |
| `extended_score.py` | **`ExtendedScore` dataclass — the single serialization boundary for `scores.json`.** `.from_analysis(result)` builds from an `AnalysisResult` (score, listing facts, early signals), `.from_dict(data)` parses raw JSON via explicit isinstance-checked helpers (`_as_float`, `_as_str`, `_parse_evidence`), `.to_dict()` flattens for storage. Corrupt rows return `None` instead of crashing; an early-signal row of an unknown kind is skipped, not fatal. |
| `dip_candidate.py` | **`DipCandidateRecord` dataclass — the single serialization boundary for `dip_candidates.json`.** Wraps a `DipSignal` with name + sector metadata. Uses `_parse_severity`, `_as_float`, `_as_int`, `_as_str` for untyped-input coercion. |
| `engine.py` | `Engine.refresh_one(ticker)` / `refresh_all()` and the opt-in background warmer. Fetches via `FundamentalsSource.fetch_full()` (with SEC's long history for US filers), reads the news radar, scores, and hands the result to `tracking.record_analysis`. `RefreshResult` carries the early-signal tally and how many changes were new. Never calls an LLM. |
| `resolver.py` | Free-text name → Yahoo symbol on any exchange. Equities only, ranked by name match with Yahoo's relevance order as tie-breaker; legal suffixes stripped via `data_sources.names`. |
| `judgments.py` | `Judgment` + `PillarJudgment` + `JudgmentStore`. The only place Claude writes back to the cache. Per-ticker JSON files under `data_cache/judgments/`. `from_dict` narrows raw JSON with explicit isinstance checks and holds every pillar score to 0–100; `Judgment.age_days` (from `updated_at`) is what the pipeline ages a judgment by. |
| `universe.py` | `load_universe(cache_dir) -> list[str]` (names only), `load_scores(cache_dir) -> dict[str, ExtendedScore]`, `load_dip_candidates(cache_dir) -> dict[str, DipCandidateRecord]`. Every loader returns typed objects; rows that fail parsing are logged and skipped. |
| `leader_mode_classifier.py` | `LeaderMode` StrEnum (FOUNDER_LED / FAMILY_CONTROLLED / STATE_CONTROLLED / PROFESSIONAL; legacy names still parse) + `classify_mode()` heuristic. |
| `ticker.py` | `canonical()` (uppercased Yahoo symbol — the exchange suffix is never stripped) and `slug()` (path-safe filename). |
| `budget_parser.py` | `Magnitude` enum (K / M / B / LAKH / CRORE) + `parse_budget("50k"/"2.5m"/"1L"/"1cr")` → amount in the base currency. |
| `config.py` | `AnalystConfig.from_env()` loads an optional `.env` at the project root (falls back to `.env.example`) and exposes `data_cache_dir`, `brave_api_key`, and `base_currency` (`BASE_CURRENCY`, default INR). |
| `cache.py` | `JsonStore` — bottom-of-stack JSON file wrapper, typed as `dict[str, object]`. Any typed view of the data layers a typed record (`ExtendedScore` / `DipCandidateRecord` / `Judgment`) on top. |

## Type-safety rules for this layer

- **No `typing.Any` anywhere.** This layer holds the JSON persistence
  boundary, so `dict[str, object]` is used at every `to_dict()` /
  `from_dict()` — `object` forces callers to narrow before use.
- **Every persisted JSON shape has a typed record** (`ExtendedScore`,
  `DipCandidateRecord`, `Judgment`). Downstream consumers *never*
  touch raw dicts from the cache; they go through `load_scores` /
  `load_dip_candidates` / `JudgmentStore.get` and work with typed
  objects from the point of load.
- **Untyped → typed conversion uses explicit narrowing helpers**
  (`_as_float`, `_as_str`, `_as_int`, `_parse_evidence`,
  `_parse_severity`, `_str_field`, `_dict_field`) that isinstance-
  check before coercing and return the default on mismatch. No
  `float(data.get("x", 0))` blind coercions.

## When to touch each file

- **New CLI subcommand** → create `commands/<name>.py` and register
  it in `__main__.py`. Keep the handler in `commands/` so `__main__.py`
  stays a thin parser/dispatcher.
- **New env var** → `config.py` only.
- **Name resolution edge case** → `resolver.py`.
- **New scoring data field persisted to scores.json** → add it to
  `ExtendedScore` (both the dataclass fields AND the `from_dict` /
  `to_dict` pair), then follow the callers. Never bypass the typed
  record with a raw dict write.
- **New persisted JSON shape** → define a new typed record alongside
  `extended_score.py` / `dip_candidate.py` with `from_dict` / `to_dict`
  and isinstance-based narrowing helpers.
- **Anything persisted to `data_cache/`** → a run's score row, snapshot,
  daily closes and signals are written through
  `tracking.record_analysis`, one ticker at a time (the JSON files under
  a file lock). `pick` writes only its own `dip_candidates.json` and
  dip signals. Don't add a second write path for scores: change
  detection depends on every run passing through the same door.
- **A new kind of change worth announcing** → `tracking.changes_since`
  (pure, so it tests without a cache), plus a `SignalType` if it is a
  new category.

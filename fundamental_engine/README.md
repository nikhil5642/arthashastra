# fundamental_engine/

**Orchestrator layer** — CLI entry point, configuration, analysis
pipeline, background cache warmer, command handlers, and shared
helpers. This is the **top of the stack**: it wires together
`data_sources/`, `scorers/`, `screener/`, `reports/`, and `signals/`.
Nothing below this layer imports from here.

Everything in this package is pure Python with no live network calls
of its own — it delegates HTTP to `data_sources/` and scoring to
`scorers/`.

## Layout

```
fundamental_engine/
├── __main__.py              # argparse + dispatch (thin)
├── commands/                # vertical-slice CLI handlers
│   ├── __init__.py
│   ├── pick.py              #   pick --amount <INR>  (was reports/picks_report.py)
│   └── brief.py             #   brief                (was reports/market_brief.py)
├── analyse_pipeline.py      # analyse_ticker / score_snapshot / build_extended_score_dict
├── analysis_context.py      # analysis_context_panel + judgment_age_days + format_age
├── engine.py                # background cache warmer
├── resolver.py              # free-text → ticker
├── judgments.py             # Judgment + JudgmentStore
├── universe.py              # emergent universe loader
├── leader_mode_classifier.py  # LeaderMode StrEnum + classify_mode
├── budget_parser.py         # "1L" / "2.5L" / "1cr" → INR
├── ticker.py                # canonical / slug
├── config.py                # AnalystConfig.from_env()
└── cache.py                 # JsonStore
```

## Modules

| Module | Purpose |
|---|---|
| `__main__.py` | `python3 -m fundamental_engine <subcommand>` router. Subcommands: `resolve`, `refresh`, `analyse`, `news`, `research`, `pick`, `brief`, `engine`. Each handler is a thin function that parses args and delegates to a command module or pipeline. |
| `commands/pick.py` | `run_pick_command(amount, …)` — loads `scores.json`, filters by verdict and composite, checks dip criteria against `prices.db`, emits signals, and renders the picks table. Orchestrates data + presentation end-to-end. |
| `commands/brief.py` | `run_brief_command()` — loads cached state, counts dip candidates by sector, summarises recent signals, and renders the brief panel. |
| `analyse_pipeline.py` | `analyse_ticker()` (fetch + score), `score_snapshot()` (pure, no I/O), and `build_extended_score_dict()` — the single source of truth for the `scores.json` row shape. |
| `analysis_context.py` | `analysis_context_panel(result, ticker, price_rows_written)` — the banner shown at the top of every `analyse` run. Also `judgment_age_days()` + `format_age()` helpers used by the STALE warning hint. |
| `engine.py` | Background cache warmer. Walks the emergent universe, fetches via `FundamentalsSource.fetch_full()`, persists both the extended score (`scores.json`) and the raw daily closes (`prices.db`). Never calls an LLM. |
| `resolver.py` | Free-text name → NSE/BSE ticker via `yfinance.Search`. Strips common corporate suffixes (`ltd`, `limited`, `corp`) and retries with shorter variants. |
| `judgments.py` | `Judgment` + `JudgmentStore`. The only place Claude writes back to the cache. Per-ticker JSON files under `data_cache/judgments/`. |
| `universe.py` | Loads the emergent universe from `data_cache/scores.json`. The analyst does **not** ship a curated ticker list. |
| `leader_mode_classifier.py` | `LeaderMode` StrEnum (FIRST_GEN / MULTI_GEN_FAMILY / PROFESSIONAL) + `classify_mode()` heuristic. Consumed by the `/research-founder` skill. |
| `ticker.py` | `canonical()` (drop `.NS`/`.BO`) and `slug()` (path-safe filename from a person name). Note: yfinance-symbol formatting lives *inside* `yfinance_adapter.py` as `_yf_symbol` — keeping yfinance details out of the engine layer. |
| `budget_parser.py` | `"1L"` / `"2.5L"` / `"1cr"` / `"50k"` → INR float. |
| `config.py` | `AnalystConfig.from_env()` loads `.env.{ANALYST_ENV}` and exposes `data_cache_dir` + `brave_api_key`. No LLM fields — this project is Claude-Code-native. |
| `cache.py` | `JsonStore` — read/write/upsert for small JSON blobs (`scores.json`, `dip_candidates.json`). Time series live in SQLite, not here. |

## When to touch each file

- **New CLI subcommand** → create `commands/<name>.py` and register
  it in `__main__.py`. Keep the handler in `commands/` so `__main__.py`
  stays a thin parser/dispatcher.
- **New env var** → `config.py` only.
- **Name resolution edge case** → `resolver.py`.
- **Scoring data shape change** → `analyse_pipeline.py::
  build_extended_score_dict`, then follow the callers.
- **Anything persisted to `data_cache/`** → only `engine.py` writes
  aggregate files. CLI readers may write the single row they're
  operating on.

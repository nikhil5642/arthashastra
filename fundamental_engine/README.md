# fundamental_engine/

Orchestrator layer — CLI entry point, configuration, analysis pipeline,
background cache warmer, and shared helpers. Everything in this package
is pure Python with no live network calls of its own; it wires together
`data_sources/`, `scorers/`, `screener/`, `reports/`, and `signals/`.

## Modules

| Module | Purpose |
|---|---|
| `__main__.py` | `python3 -m fundamental_engine <subcommand>` router. Subcommands: `resolve`, `refresh`, `analyse`, `news`, `research`, `pick`, `brief`, `engine`. |
| `config.py` | `AnalystConfig.from_env()` loads `.env.{ANALYST_ENV}` and exposes `data_cache_dir` + `brave_api_key`. No LLM fields — this project is Claude-Code-native. |
| `resolver.py` | Free-text name → NSE/BSE ticker via `yfinance.Search`. Strips common suffixes (`ltd`, `limited`, `corp`) and retries with shorter variants. |
| `ticker.py` | `canonical()` (drop `.NS`/`.BO`), `yf_symbol()` (append suffix), `slug()` (path-safe filename from a person name). |
| `budget_parser.py` | `"1L"` / `"2.5L"` / `"1cr"` / `"50k"` → INR float. |
| `universe.py` | Loads the emergent universe from `data_cache/scores.json`. The analyst does **not** ship a curated ticker list. |
| `cache.py` | `JsonStore` — read/write/upsert for small JSON blobs (`scores.json`, `dip_candidates.json`). Time series live in SQLite, not here. |
| `judgments.py` | `Judgment` + `JudgmentStore`. The only place Claude writes back to the cache. Per-ticker JSON files under `data_cache/judgments/`. |
| `analyse_pipeline.py` | `analyse_ticker()` (fetch + score), `score_snapshot()` (pure, no I/O), and `build_extended_score_dict()` — the single source of truth for the `scores.json` row shape. |
| `engine.py` | Background cache warmer. Walks the emergent universe, fetches via `FundamentalsSource.fetch_full()`, persists both the extended score (`scores.json`) and the raw daily closes (`prices.db`). Never calls an LLM. |
| `leader_mode_classifier.py` | `classify_mode()` heuristic — FIRST_GEN / MULTI_GEN_FAMILY / PROFESSIONAL. Consumed by the `/research-founder` skill, not by Python. |

## When to touch each file

- New CLI subcommand → `__main__.py` + usually a new helper module.
- New env var → `config.py` only.
- Name resolution edge case → `resolver.py`.
- Scoring data shape change → `analyse_pipeline.py::build_extended_score_dict`,
  then follow the callers.
- Anything persisted to `data_cache/` → only `engine.py` writes aggregate
  files. CLI readers may write the single row they're operating on.

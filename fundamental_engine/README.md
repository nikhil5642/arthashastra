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
├── analyse_pipeline.py      # analyse_ticker / score_snapshot / AnalysisResult
├── analysis_context.py      # analysis_context_panel + judgment_age_days + format_age
├── extended_score.py        # ExtendedScore typed record for scores.json rows
├── dip_candidate.py         # DipCandidateRecord typed record for dip_candidates.json
├── engine.py                # background cache warmer
├── resolver.py              # free-text → ticker
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
| `__main__.py` | `python3 -m fundamental_engine <subcommand>` router. Subcommands: `resolve`, `refresh`, `analyse`, `news`, `research`, `pick`, `brief`, `engine`. Each handler is a thin function that parses args and delegates to a command module or pipeline. |
| `commands/pick.py` | `run_pick_command(amount, …)` — loads typed `ExtendedScore` objects via `load_scores`, filters by verdict and composite, checks dip criteria against `prices.db`, emits signals, and persists typed `DipCandidateRecord` rows to `dip_candidates.json`. Orchestrates data + presentation end-to-end. |
| `commands/brief.py` | `run_brief_command()` — loads typed `ExtendedScore` + `DipCandidateRecord` objects, counts dip candidates by sector, summarises recent signals, renders the brief panel and Markdown mirror. |
| `analyse_pipeline.py` | `analyse_ticker()` (fetch + score), `score_snapshot()` (pure, no I/O), and the `AnalysisResult` wrapper. The scores.json row shape itself is owned by `ExtendedScore.from_analysis` / `to_dict` in `extended_score.py`. |
| `analysis_context.py` | `analysis_context_panel(result, ticker, price_rows_written)` — the banner shown at the top of every `analyse` run. Also `judgment_age_days()` + `format_age()` helpers used by the STALE warning hint. |
| `extended_score.py` | **`ExtendedScore` dataclass — the single serialization boundary for `scores.json`.** `.from_analysis(result)` builds from an `AnalysisResult`, `.from_dict(data)` parses raw JSON via explicit isinstance-checked helpers (`_as_float`, `_as_str`, `_parse_evidence`), `.to_dict()` flattens for storage. Corrupt rows return `None` instead of crashing. |
| `dip_candidate.py` | **`DipCandidateRecord` dataclass — the single serialization boundary for `dip_candidates.json`.** Wraps a `DipSignal` with name + sector metadata. Uses `_parse_severity`, `_as_float`, `_as_int`, `_as_str` for untyped-input coercion. |
| `engine.py` | Background cache warmer. Walks the emergent universe, fetches via `FundamentalsSource.fetch_full()`, persists `ExtendedScore.from_analysis(result).to_dict()` to `scores.json` and raw daily closes to `prices.db`. Never calls an LLM. |
| `resolver.py` | Free-text name → NSE/BSE ticker via `yfinance.Search`. Strips common corporate suffixes (`ltd`, `limited`, `corp`) and retries with shorter variants. |
| `judgments.py` | `Judgment` + `PillarJudgment` + `JudgmentStore`. The only place Claude writes back to the cache. Per-ticker JSON files under `data_cache/judgments/`. `from_dict` narrows raw JSON with explicit isinstance checks. |
| `universe.py` | `load_universe(cache_dir) -> list[str]` (names only), `load_scores(cache_dir) -> dict[str, ExtendedScore]`, `load_dip_candidates(cache_dir) -> dict[str, DipCandidateRecord]`. Every loader returns typed objects; rows that fail parsing are logged and skipped. |
| `leader_mode_classifier.py` | `LeaderMode` StrEnum (FIRST_GEN / MULTI_GEN_FAMILY / PROFESSIONAL) + `classify_mode()` heuristic. Consumed by the `/research-founder` skill. |
| `ticker.py` | `canonical()` (drop `.NS`/`.BO`) and `slug()` (path-safe filename). Note: yfinance-symbol formatting lives *inside* `yfinance_adapter.py` as `_yf_symbol` — keeping yfinance details out of the engine layer. |
| `budget_parser.py` | `Magnitude` StrEnum (K / LAKH / CRORE, float-valued) + `parse_budget("1L"/"2.5L"/"1cr"/"50k")` → INR float. |
| `config.py` | `AnalystConfig.from_env()` loads an optional `.env` at the project root (falls back to `.env.example`) and exposes `data_cache_dir` + `brave_api_key`. No LLM fields, no sandbox / live split — this project is Claude-Code-native and there's nothing to flag-gate. |
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
- **Anything persisted to `data_cache/`** → only `engine.py` writes
  aggregate files. CLI readers may write the single row they're
  operating on.

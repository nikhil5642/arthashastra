# reports/

On-demand report generators. Each module implements one CLI subcommand's
rendering logic. Rich is used **only** for formatted one-shot terminal
output — there is no continuous dashboard.

## Modules

| Module | CLI subcommand | Reads | Writes |
|---|---|---|---|
| `stock_report.py` | `analyse <TICKER>` | `BalanceSheetSnapshot` + `StockScore` (in-memory from the pipeline) | `data_cache/reports/<TICKER>_<date>.md` |
| `picks_report.py` | `pick --amount <INR>` | `data_cache/scores.json` + `data_cache/prices.db` | `data_cache/dip_candidates.json` + signals |
| `market_brief.py` | `brief` | `data_cache/scores.json` + `data_cache/dip_candidates.json` + `data_cache/signals.json` | `data_cache/reports/brief_<date>.md` |
| `formatting.py` | — | — | Rich helpers: `VERDICT_STYLES`, `make_console(quiet=False)`, `verdict_text()`, `evidence_table()`, `section_panel()`. |

## Conventions

- **Reports are one-shot.** No streaming, no live updating, no TUI.
- **Reports are read-mostly.** They may write their own output file
  (`dip_candidates.json`, saved Markdown) but must not touch
  `scores.json` or `signals.json` aggregates — that's the engine's job.
- **Pending-Claude-judgment markers** are mandatory. If a pillar score
  is the neutral 50 prior with zero completeness, the renderer surfaces
  "pillar pending Claude judgment" in both the terminal panel and the
  saved Markdown. Don't hide an incomplete verdict.
- **Tests** construct `BalanceSheetSnapshot` / `StockScore` inline in
  the test function. No fixture JSONs. See
  `tests/reports/test_stock_report.py` for the pattern.

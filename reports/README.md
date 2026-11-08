# reports/

**Pure presentation layer.** Takes already-prepared data structures
(`BalanceSheetSnapshot`, `StockScore`) and renders them for human
consumption (Rich terminal panels + Markdown mirrors). This layer has
**no upward dependencies** — it doesn't reach into `fundamental_engine/`
for config or cache loading. Command handlers that need to *orchestrate*
(load from cache, filter, emit signals, then render) live one layer up
in `fundamental_engine/commands/`.

## Layout

```
reports/
├── formatting.py       # console, verdict styles, gate labels, money formatting
└── stock_report/       # /analyse <TICKER> renderer
    ├── __init__.py     #   re-exports render_stock_report
    ├── render.py       #   entry + Markdown writer
    ├── header.py       #   header panel + valuation + analyst bundle
    ├── financial_profile.py   # profile dispatcher + industrial / bank variants
    ├── data_quality.py #   statement freshness + source conflicts
    └── trends.py       #   multi-year + shareholding + quarterly rows
```

## Modules

| Module | Purpose |
|---|---|
| `stock_report/render.py` | `render_stock_report(snap, score, *, output_dir=None, console=None)` — entry. Composes header + business summary + financial profile + four pillar sections + composite verdict, optionally saving a Markdown mirror to `data_cache/reports/<TICKER>_<date>.md`. |
| `stock_report/header.py` | `header_panel(snap, score)` + the valuation / analyst-bundle helpers. Renders ticker, price, 52w range, market cap, P/E · P/B · div yield, analyst consensus target + recommendation. |
| `stock_report/financial_profile.py` | `financial_profile_panel(snap)` — dispatches to the industrial or bank variant based on `snap.is_bank`. Industrial: margin stack, cost mix, working-capital days, asset composition. Bank: spread & fees (NIM, NII), profitability, balance sheet (deposits / borrowings / CD ratio). Both variants share the trend and quarterly rows. |
| `stock_report/trends.py` | Multi-year trend (ROCE 5y, op margin 5y, borrowings 3y Δ), shareholding trend (Promoter / FII / DII / retail with YoY deltas and smart-money flags), and quarterly rows (4 most-recent quarters with *date-aware* QoQ and YoY lookups so gaps in the series don't misalign the comparison). |
| `formatting.py` | `VERDICT_STYLES` / `GATE_LABELS` (keyed by enums), `make_console`, `verdict_text`, `evidence_table`, `section_panel`, and the money helpers: `format_price` (per-share, minor units like pence shown in pounds), `format_money` (large amounts — ₹ crore for INR, K / M / B / T elsewhere), `format_amount` (plain). |
| `stock_report/data_quality.py` | Latest annual period with its age (STALE after 15 months) and every source conflict, as a terminal panel, a banner summary line, and a Markdown section. |

## Conventions

- **Every amount prints in its own currency** — prices and market cap
  in the trading currency, statement lines in the reporting currency.
  Never hardcode ₹ / Cr.
- **Pure presentation, no I/O orchestration.** A `reports/` module
  takes prepared data structures as arguments. If it needs to *load*
  data from `data_cache/` or the universe, it's in the wrong layer —
  move it to `fundamental_engine/commands/`.
- **Reports are one-shot.** No streaming, no live updating, no TUI.
- **Pending-Claude-judgment markers** are mandatory. If a pillar score
  is the neutral 50 prior with zero completeness, surface "pillar
  pending Claude judgment" in both the terminal panel and the saved
  Markdown. Don't hide an incomplete verdict.
- **Verdict / Pillar are enums, not strings.** Import from
  `scorers.base`. Use `dict[Verdict, …]` / `dict[Pillar, …]` not
  `dict[str, …]`.
- **Tests** construct `BalanceSheetSnapshot` / `StockScore` inline in
  the test function. No fixture JSONs. See
  `tests/reports/test_stock_report.py` for the pattern.

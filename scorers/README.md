# scorers/

Deterministic scoring layer. **Fundamentals only** — the qualitative
pillars (leadership, prospects, news) are judged by Claude in the
`/analyse-stock` slash command and written to
`data_cache/judgments/<TICKER>.json`. This folder holds zero LLM code
on purpose. See `CLAUDE.md` §Rules for the architectural boundary.

## Modules

| Module | Purpose |
|---|---|
| `base.py` | `ScorerResult`, `StockScore`, `Verdict`, `VERDICT_BANDS`, `verdict_for(composite)`. Pure data classes — no imports from the rest of the project. |
| `fundamentals_scorer.py` | `score_fundamentals(snap)` — deterministic rubric with graceful degradation. Debt health 25% + profitability 25% + growth 20% + cash quality 15% + promoter behaviour 15%. Pledge > 10% clamps the final score to 40. |
| `composite_scorer.py` | `blend(...)` weights the four pillars (40/30/15/15), `pending_judgment(pillar)` emits a clearly-labelled placeholder when no Claude judgment exists yet. |

## Sub-scorer rubric (inside `fundamentals_scorer.py`)

Each sub-scorer returns `(score_or_None, evidence_list)`. Missing inputs
drop the component entirely and the remaining weights rescale, so a
sparse balance sheet still produces a meaningful score — just with
`data_completeness_pct` under 100.

- **Debt health (25%)** — D/E + interest coverage, averaged.
- **Profitability (25%)** — ROE + ROCE, averaged. `≥ 20% → 100`,
  `≥ 15% → 80..100`, `≥ 10% → 50..80`, `≥ 5% → 20..50`, else `val × 4`.
- **Growth (20%)** — 3-year revenue + PAT CAGR, averaged.
- **Cash quality (15%)** — OCF / PAT. Healthy is ≥ 1.2.
- **Promoter (15%)** — holding + pledge, averaged. `holding ≥ 60% → 100`,
  `pledge == 0 → 100`.

## Do not add

- `leadership_scorer.py`
- `qualitative_scorer.py`

Both are intentionally absent. Claude scores those pillars in-session
via the slash commands and writes to `JudgmentStore`.

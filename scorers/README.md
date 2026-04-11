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
| `fundamentals_scorer.py` | `score_fundamentals(snap)` — deterministic rubric with graceful degradation. Balance-sheet health 35% + profitability 20% + growth 15% + earnings quality 15% + promoter 15%. Pledge > 10% clamps the final score to 40. Growth-deceleration veto caps the score at 70/60/50 when both 3y CAGRs fall below 7/5/3%. |
| `composite_scorer.py` | `blend(...)` weights the four pillars (fundamentals 40, leadership 30, prospects 15, news 15), `pending_judgment(pillar)` emits a clearly-labelled placeholder when no Claude judgment exists yet. |

## Sub-scorer rubric (inside `fundamentals_scorer.py`)

Each sub-scorer returns `(score_or_None, evidence_list)`. Missing inputs
drop the component entirely and the remaining weights rescale, so a
sparse balance sheet still produces a meaningful score — just with
`data_completeness_pct` under 100.

- **Balance-sheet health (35%)** — internal blend of five ratios:
  D/E (30%) + interest coverage (25%) + current ratio (15%) +
  quick ratio (15%) + net debt / EBITDA (15%). Each ratio has its own
  piecewise curve; net-cash businesses max out, liquidity-crunched
  ones get dragged down hard regardless of leverage.
- **Profitability (20%)** — ROE + ROCE + ROA, averaged. ROA uses a
  lower band (`≥ 12% → 100`, `≥ 8% → 80..100`); ROE/ROCE band is
  `≥ 20% → 100`, `≥ 15% → 80..100`, `≥ 10% → 50..80`, …
- **Growth (15%)** — 3-year revenue + PAT CAGR, averaged.
- **Earnings quality (15%)** — OCF/PAT + FCF/PAT, averaged. OCF/PAT
  ≥ 1.2 is the gold standard; FCF/PAT lets capex-heavy businesses
  still score reasonably if they convert ≥ 0.5× of PAT to free cash.
- **Promoter (15%)** — holding + pledge, averaged. `holding ≥ 60% → 100`,
  `pledge == 0 → 100`.

## Do not add

- `leadership_scorer.py`
- `qualitative_scorer.py`

Both are intentionally absent. Claude scores those pillars in-session
via the slash commands and writes to `JudgmentStore`.

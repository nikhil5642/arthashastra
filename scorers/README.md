# scorers/

Deterministic scoring layer. **Fundamentals only** — the qualitative
pillars (leadership, prospects, news) are judged by Claude in the
`/analyse-stock` slash command and written to
`data_cache/judgments/<TICKER>.json`. This folder holds zero LLM code
on purpose. See `CLAUDE.md` §Rules for the architectural boundary.

## Layout

```
scorers/
├── base.py                  # Verdict + Pillar StrEnums + ScorerResult + StockScore
├── composite_scorer.py      # blend(...) + pending_judgment(pillar)
└── fundamentals_scorer/     # split by rubric variant
    ├── __init__.py          #   score_fundamentals dispatcher
    ├── industrial.py        #   industrial rubric + 5 sub-scorers
    └── bank.py              #   bank / NBFC rubric + 4 sub-scorers
```

## Modules

| Module | Purpose |
|---|---|
| `base.py` | `Verdict` and `Pillar` `StrEnum`s (members are `str` subclasses so JSON + dict-key usage keep working), `ScorerResult`, `StockScore`, `VERDICT_BANDS`, `verdict_for(composite)`. Pure data classes — no imports from the rest of the project. |
| `composite_scorer.py` | `blend(...)` weights the four pillars (fundamentals 40, leadership 30, prospects 15, news 15) keyed by `Pillar` enum. `pending_judgment(Pillar.LEADERSHIP)` emits a clearly-labelled placeholder when no Claude judgment exists yet. |
| `fundamentals_scorer/__init__.py` | `score_fundamentals(snap)` — dispatches to industrial or bank based on `is_bank_snapshot(snap)`. |
| `fundamentals_scorer/industrial.py` | Industrial rubric: balance-sheet health 35% + profitability 20% + growth 15% + earnings quality 15% + promoter 15%. Pledge > 10% clamps the final score to 40. Growth-deceleration veto caps at 70/60/50 when both 3y CAGRs fall below 7/5/3%. |
| `fundamentals_scorer/bank.py` | Bank / NBFC rubric: profitability 35% (ROE + ROA + NIM on bank-calibrated curves) + growth 25% (PAT 3y CAGR only) + franchise 20% (non-interest income ratio + CD ratio) + governance 20% (pledge + promoter, with 0% promoter treated as neutral). |

## Industrial sub-scorer rubric

Each sub-scorer returns `(score_or_None, evidence_list)`. Missing
inputs drop the component entirely and the remaining weights rescale,
so a sparse balance sheet still produces a meaningful score — just
with `data_completeness_pct` under 100.

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

## Bank sub-scorer rubric

- **Profitability (35%)** — ROE + ROA + NIM on bank-calibrated curves
  (ROA 1.5% is *good* for a bank, not a minimum; NIM ≥ 4% = 100).
- **Growth (25%)** — PAT 3y CAGR only. Revenue CAGR is explicitly
  *excluded* because mergers (HDFC Bank + HDFC Ltd) create step-changes
  that inflate the CAGR without reflecting organic growth.
- **Franchise (20%)** — non-interest income ratio (fee / treasury
  share of total income — HDFC Bank runs ~47%, PSU banks often <15%)
  + Credit-Deposit ratio (> 100% = stressed funding). The ratio is
  skipped if the NBFC has no meaningful non-interest income (fee
  income is baked into revenue for most NBFCs).
- **Governance (20%)** — pledge clean + promoter holding where zero
  is treated as *neutral* rather than penalised (institutional
  ownership is normal for private banks).

**Data limitations for banks**: screener.in does NOT expose GNPA,
NNPA, CASA, or CRAR in its standard tables, so asset quality and
capital adequacy cannot be scored deterministically. Banks are
flagged in the Analysis Context banner so Claude's leadership pillar
can cover these manually.

## Do not add

- `leadership_scorer.py`
- `qualitative_scorer.py`

Both are intentionally absent. Claude scores those pillars in-session
via the slash commands and writes to `JudgmentStore`.

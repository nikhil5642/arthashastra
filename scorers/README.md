# scorers/

Deterministic scoring layer. **Fundamentals and valuation only** — the
qualitative pillars (leadership, prospects, news) are judged by Claude
in the `/analyse-stock` slash command and written to
`data_cache/judgments/<TICKER>.json`. This folder holds zero LLM code
on purpose. See `CLAUDE.md` §Rules for the architectural boundary.

```
composite = 0.35·fundamentals + 0.15·valuation + 0.25·leadership + 0.15·prospects + 0.10·news
```

## Layout

```
scorers/
├── base.py                  # Verdict + Pillar + Gate StrEnums + ScorerResult + StockScore
├── composite_scorer.py      # blend(...) + integrity gate + pending_judgment(pillar)
├── valuation_scorer.py      # score_valuation: price vs what the business earns
└── fundamentals_scorer/     # split by rubric variant
    ├── __init__.py          #   score_fundamentals dispatcher
    ├── industrial.py        #   industrial rubric + 5 sub-scorers
    ├── bank.py              #   bank / NBFC rubric + 5 sub-scorers
    └── ownership.py         #   shared ownership sub-score
```

## Modules

| Module | Purpose |
|---|---|
| `base.py` | `Verdict`, `Pillar`, and `Gate` `StrEnum`s (members are `str` subclasses so JSON + dict-key usage keep working), `ScorerResult`, `StockScore` (incl. `gates_applied`), `VERDICT_BANDS`, `verdict_for(composite)`. Pure data classes — no imports from the rest of the project. |
| `composite_scorer.py` | `blend(...)` weights the five pillars by `PILLAR_WEIGHTS` (fundamentals 35, valuation 15, leadership 25, prospects 15, news 10), then applies gates: `integrity_veto=True` caps the composite at 54.0 (AVOID). `pending_judgment(Pillar.LEADERSHIP)` emits a clearly-labelled placeholder when no Claude judgment exists yet. |
| `valuation_scorer.py` | `score_valuation(snap)` — earnings yield 35% + FCF yield 35% + P/E vs its own history 30%; banks swap FCF yield for P/B vs ROE ÷ 11% cost of equity. Loss-makers score 0 on earnings yield; the value-trap guard caps slow growers at 70. Unscoreable (no price and statements in one currency) → neutral 50, confidence 0. |
| `fundamentals_scorer/__init__.py` | `score_fundamentals(snap)` — dispatches to industrial or bank based on `is_bank_snapshot(snap)` (structure first: screener's bank P&L shape, or a financial sector with no current-assets split). |
| `fundamentals_scorer/industrial.py` | Industrial rubric: balance-sheet health 35% + profitability 20% + growth 15% + earnings quality 15% + ownership 15%. Pledge > 10% clamps the final score to 40. Growth-deceleration veto caps at 70/60/50 when both 3y CAGRs fall below 7/5/3% — `growth_deceleration_cap(snap)` is public because the valuation guard and the backtests share the definition. |
| `fundamentals_scorer/bank.py` | Bank / NBFC rubric: profitability 30% (ROE + ROA + NIM) + asset quality 25% (gross / net NPA + trend) + growth 20% (PAT 3y CAGR only) + franchise 15% (non-interest income ratio + CD ratio) + governance 10% (shared ownership sub-score). |
| `fundamentals_scorer/ownership.py` | Shared ownership sub-score: pledge (only when a source reported it), insider / promoter stake change, and a neutral-centred holding level — a widely-held company is not penalised. |

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
- **Ownership (15%)** — averaged from whichever exist: pledge
  (`0% → 100`, `> 25% → 0`; skipped when unknown), insider stake change
  (`≥ +3pts → 95`, `±1pt → 65`, `≤ −3pts → 15`), and holding level
  (`≥ 50% → 85`, `≥ 25% → 75`, widely held `→ 65`, neutral).

## Bank sub-scorer rubric

- **Profitability (30%)** — ROE + ROA + NIM on bank-calibrated curves
  (ROA 1.5% is *good* for a bank, not a minimum; NIM ≥ 4% = 100).
- **Asset quality (25%)** — gross NPA (`≤ 1% → 100`, `> 10% → 0`), net
  NPA (`≤ 0.3% → 100`), and the 1y gross-NPA trend. Most bank failures
  are credit failures; this is where they show first.
- **Growth (20%)** — PAT 3y CAGR only. Revenue CAGR is explicitly
  *excluded* because mergers (HDFC Bank + HDFC Ltd) create step-changes.
- **Franchise (15%)** — non-interest income ratio + Credit-Deposit
  ratio (> 100% = stressed funding).
- **Governance (10%)** — the shared ownership sub-score.

**Data limitations for banks**: screener's bank quarterly table exposes
Gross / Net NPA % (read from the standalone page, since consolidated
cells are empty); CASA and CRAR are not exposed anywhere, so funding mix
and capital adequacy are checked in Claude's judgment. When a lender has
no NPA data at all, the evidence says asset quality is unscored.

## Valuation rubric

Curves are universal (one set for every market), piecewise linear,
flat beyond their ends:

- **Earnings yield (35%)** — 100 / P/E: `0% → 0`, `2.5% → 40`,
  `4% → 65`, `6% → 85`, `≥ 8% → 100`. A loss-maker scores 0, not "n/a".
- **FCF yield (35%)** — free cash flow per share ÷ price: `0% → 10`,
  `3% → 60`, `5% → 85`, `≥ 7% → 100`; negative → 0.
- **P/B vs justified (banks, 35%)** — justified P/B = ROE ÷ 11%; the
  ratio of actual to justified scores `≤ 0.7 → 100`, `1.0 → 70`,
  `1.5 → 40`, `≥ 3 → 0`. ROE ≤ 0 → 0.
- **vs own history (30%)** — today's P/E ÷ the median of its past
  fiscal-year-end P/Es (needs 3+): `≤ 0.7 → 100`, `1.0 → 60`,
  `1.25 → 40`, `≥ 2.1 → 0`.

The inputs come from `data_sources/fundamentals_source/valuation.py`,
which the live pipeline and the backtests both run.

## Do not add

- `leadership_scorer.py`
- `qualitative_scorer.py`

Both are intentionally absent. Claude scores those pillars in-session
via the slash commands and writes to `JudgmentStore`.

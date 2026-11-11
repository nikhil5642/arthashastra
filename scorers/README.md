# scorers/

Deterministic scoring layer. **Fundamentals and valuation only** — the
qualitative pillars (leadership, prospects, news) are judged by Claude
in the `/analyse-stock` slash command and written to
`data_cache/judgments/<TICKER>.json`. This folder holds zero LLM code
on purpose. See `CLAUDE.md` §Rules for the architectural boundary.

Next to the two scores sits a third output that carries no weight:
**early signals** — named concerns and strengths that the newest
quarters, the latest balance sheet, the ownership tape and the week's
headlines show before any score moves.

```
composite = 0.35·fundamentals + 0.15·valuation + 0.25·leadership + 0.15·prospects + 0.10·news
```

## Layout

```
scorers/
├── base.py                  # Verdict + Pillar + Gate StrEnums + ScorerResult + StockScore
├── composite_scorer.py      # blend(...) + integrity gate + pending_judgment(pillar)
├── valuation_scorer.py      # score_valuation: price vs what the business can keep earning
├── early_signals.py         # detect_early_signals: concerns + strengths beyond the scored ratios
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
| `valuation_scorer.py` | `score_valuation(snap)` — earnings yield 35% + FCF yield 35% + P/E vs its own history 30%; banks swap FCF yield for P/B vs ROE ÷ 11% cost of equity. Earnings are `sustainable_earnings(snap)`: one-off gains out, cyclical peaks capped (see below). Loss-makers score 0 on earnings yield; the value-trap guard caps slow growers at 70. Unscoreable (no price-vs-statements ratio, or a `quote_unreliable` snapshot) → neutral 50, confidence 0. |
| `early_signals.py` | `detect_early_signals(snap, news=None)` → `list[EarlySignal]`, concerns first, most severe first. One detector per `EarlySignalKind`; `tally(signals)` gives the "3 concerns (1 urgent) · 2 strengths" line that `brief`, `pick` and `screen` print. Reads the snapshot and an optional `NewsDigest`; touches no score. |
| `fundamentals_scorer/__init__.py` | `score_fundamentals(snap)` — dispatches to industrial or bank based on `is_bank_snapshot(snap)` (structure first: screener's bank P&L shape, or a financial sector with no current-assets split). |
| `fundamentals_scorer/industrial.py` | Industrial rubric: balance-sheet health 35% + profitability 20% + growth 15% + earnings quality 15% + ownership 15%. Pledge > 10% clamps the final score to 40. Growth-deceleration veto caps at 70/60/50 when both 3y CAGRs fall below 7/5/3% — `growth_deceleration_cap(snap)` is public because the valuation guard and the backtests share the definition. Known-bad facts are scored, not dropped: `has_negative_equity(snap)` puts the D/E axis at 0, and a profit that became a loss arrives as a −100% PAT CAGR (`pat_growth_label` prints it as "profit wiped out"). |
| `fundamentals_scorer/bank.py` | Bank / NBFC rubric: profitability 30% (ROE + ROA + NIM) + asset quality 25% (gross / net NPA + trend) + growth 20% (PAT 3y CAGR only) + franchise 15% (non-interest income ratio + CD ratio) + governance 10% (shared ownership sub-score). |
| `fundamentals_scorer/ownership.py` | Shared ownership sub-score: pledge (only when a source reported it), insider / promoter stake change, and a neutral-centred holding level — a widely-held company is not penalised. |

## Industrial sub-scorer rubric

Each sub-scorer returns `(score_or_None, evidence_list)`. Missing
inputs drop the component entirely and the remaining weights rescale,
so a sparse balance sheet still produces a meaningful score — just
with `data_completeness_pct` under 100. Every input is the latest
audited fiscal year; what the quarters since then show is the early
signals' job.

"Missing" means the source said nothing. A fact without a ratio is
still a fact: liabilities above assets have no meaningful debt / equity,
so negative equity scores that axis 0 instead of dropping out (which
used to let the most levered balance sheets score like unlevered ones).

- **Balance-sheet health (35%)** — internal blend of five ratios:
  D/E (30%) + interest coverage (25%) + current ratio (15%) +
  quick ratio (15%) + net debt / EBITDA (15%). Each ratio has its own
  piecewise curve; net-cash businesses max out, liquidity-crunched
  ones get dragged down hard regardless of leverage.
- **Profitability (20%)** — ROE + ROCE + ROA, averaged. ROA uses a
  lower band (`≥ 12% → 100`, `≥ 8% → 80..100`); ROE/ROCE band is
  `≥ 20% → 100`, `≥ 15% → 80..100`, `≥ 10% → 50..80`, …
- **Growth (15%)** — 3-year revenue + PAT CAGR, averaged. A profit
  that turned into a loss has no CAGR, so it counts as −100%
  (`PROFIT_WIPED_OUT_CAGR`) — the growth veto can then fire on it.
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

- **Earnings yield (35%)** — 100 / P/E on sustainable earnings: `0% → 0`,
  `2.5% → 40`, `4% → 65`, `6% → 85`, `≥ 8% → 100`. A loss over the
  freshest twelve months scores 0, not "n/a".
- **FCF yield (35%)** — free cash flow ÷ the equity's market value, over
  the trailing twelve months when four consecutive quarters exist, else
  the fiscal year: `0% → 10`, `3% → 60`, `5% → 85`, `≥ 7% → 100`;
  negative → 0. Scored as measured — it is cash.
- **P/B vs justified (banks, 35%)** — justified P/B = ROE ÷ 11%; the
  ratio of actual to justified scores `≤ 0.7 → 100`, `1.0 → 70`,
  `1.5 → 40`, `≥ 3 → 0`. ROE ≤ 0 → 0.
- **vs own history (30%)** — today's P/E on sustainable earnings ÷ the
  median of its past fiscal-year-end P/Es (needs 3+): `≤ 0.7 → 100`,
  `1.0 → 60`, `1.25 → 40`, `≥ 2.1 → 0`.

The inputs come from `data_sources/fundamentals_source/valuation.py`,
which the live pipeline and the backtests both run.

### Sustainable earnings

A P/E is only as good as the "E". `sustainable_earnings(snap)` returns
the share of reported profit the pillar counts — it only ever lowers it:

- **One-offs out** (`core_ratio`). `snap.core_earnings_ratio` is Yahoo's
  normalized net income ÷ reported net income over the same twelve
  months as the P/E. Investment gains and one-off credits are removed;
  one-off *charges* are never added back (the ratio is held at 1), and
  an adjustment under 5% of profit is ignored as noise. Alphabet's
  reported P/E of 14.9 in November 2026 was 26.7 on core earnings — 44%
  of the profit was gains on its investments.
- **Cyclical peaks capped** (`cycle_ratio`). The core net margin is set
  against the median of the company's own last six fiscal years (four at
  least). Up to 1.5× that median counts as earned — businesses do
  improve — and the rest is treated as the top of a cycle. A company
  whose median margin was a loss earns no yield credit until it has a
  record. Lenders are exempt: revenue is not what a bank's margin is
  measured on.

Evidence lines starting `ADJ:` say which adjustment applied and by how
much; the report header prints both figures ("P/E 14.9 (core 26.7)").

## Early signals

`detect_early_signals` answers a different question from the scores:
*what is new since the audited numbers?* The fundamentals pillar reads a
fiscal year that can be most of a year old; these detectors read what is
fresher (four quarters, the latest balance sheet against the one a year
back, a week of headlines) or deeper (margin history, the ownership tape).

Three rules hold for every detector:

1. **A signal needs the numbers.** Missing data produces no signal —
   never a strength, and never a concern.
2. **A signal states facts, not a verdict.** No score is adjusted here.
   The valuation pillar already prices on sustainable earnings; Claude
   weighs the rest in the qualitative pillars.
3. **Thresholds are universal and currency-free**, like every curve.
4. **A strength paid for out of flagged profit is context.** When
   `ONE_OFF_PROFIT` or `PEAK_MARGIN` fires, the strengths that same
   profit pays for — `FREE_CASH_FLOW_RISING`, `MARGIN_EXPANDING`,
   `PROFIT_OUTPACING_SALES`, `DEBT_FALLING` — drop to INFO with a note. Deleveraging at the top of
   a cycle is what cyclicals do before it turns; buybacks, insider buying
   and revenue acceleration are separate facts and keep their severity.

Severity: **URGENT** changes how the headline numbers should be read,
**ALERT** is material, **INFO** is context.

| Area | Concerns | Strengths |
|---|---|---|
| Earnings quality | `ONE_OFF_PROFIT` — core earnings under 85% of reported (URGENT under 60%). `PEAK_MARGIN` — core net margin above 1.5× its own median, enough to cut counted earnings by 10%+ (URGENT at 3×), or a business that typically lost money. | — |
| Cash — trailing twelve months vs the fiscal year, non-lenders | `FREE_CASH_FLOW_FALLING` — 40%+ below the fiscal year's, still negative, or turned negative (URGENT). `CAPEX_OUTRUNS_CASH_FLOW` — capex is 85%+ of operating cash flow. `STOCK_PAY_HEAVY` — stock pay is 10%+ of revenue (URGENT at 20%). | `FREE_CASH_FLOW_RISING` — 40%+ above the fiscal year's, or turned positive. |
| Momentum — latest quarter vs the same quarter a year earlier, non-lenders | `REVENUE_SHRINKING` — down 2%+ (URGENT at 10%). `REVENUE_DECELERATING` — a 10%+ three-year grower now 8+ points under its pace. `MARGIN_COMPRESSING` — operating margin down 3+ points (URGENT at 8). `PROFIT_FALLING_AS_SALES_GROW` — profit down 15%+ on sales up 5%+. | `REVENUE_ACCELERATING` — 10%+ growth, 8+ points above the pace. `MARGIN_EXPANDING` — up 3+ points. `PROFIT_OUTPACING_SALES` (INFO) — profit up 15%+ and 10+ points ahead of sales, with the operating margin wider than a year earlier and the profit not made of one-offs. |
| Balance sheet — latest vs a year earlier | `DEBT_RISING` — debt up 30%+ with D/E at 0.2+ (URGENT: doubled, D/E at 0.5+). `NEGATIVE_EQUITY` (URGENT when also loss-making). `RECEIVABLES_` / `INVENTORY_OUTPACING_SALES` — up 20%+ with the days of sales they represent up 15%+ (a ratio: 127% against 106% sales growth is in step, 25% against 5% is not), when the line is worth 15+ days of business. `GOODWILL_JUMP` — up 50%+ and 5%+ of equity (ALERT at 20% of equity, else INFO). | `DEBT_FALLING` — down 25%+ from a D/E of 0.2+. |
| Ownership | `SHARE_DILUTION` — share count up 3%+ (URGENT at 10%). `INSIDER_SELLING` — stake down 1+ point (URGENT at 3). `FOREIGN_INSTITUTIONS_SELLING` — down 2+ points (INFO; ALERT at 5). `RETAIL_CROWDING` — shareholder count up 20%+ while foreign institutions cut 1+ point. | `SHARE_BUYBACK` — share count down 2%+. `INSIDER_BUYING` — up 1+ point. `FOREIGN_INSTITUTIONS_BUYING` — up 2+ points (INFO). |
| Lenders | `ASSET_QUALITY_WORSENING` — gross NPA up 0.25+ points (URGENT at 1.0). | `ASSET_QUALITY_IMPROVING` — down 0.5+ points. |
| News radar — the last 7 days | `GOVERNANCE_NEWS` — 3+ governance-tagged stories that are also 15%+ of the week's coverage, or one story carried by 5+ outlets (URGENT: 8+ and 30%+, or 10+ outlets). `CREDIT_RATING_NEWS` — any credit-rating story. | — |
| Tape and data | `PRICE_DRAWDOWN` — 30%+ below the 52-week high (INFO; ALERT at 50%). `STALE_STATEMENTS` — annual statements over 15 months old. `UNRELIABLE_QUOTE` (URGENT) — price and statements don't reconcile. | — |

Why the governance rule counts a *share* of coverage: a megacap draws
~80 stories a week and a few always say "antitrust" or "probe". A burst
is governance taking over the company's news, not appearing in it.

The news signals are counted from headlines nobody has judged yet. They
say "read these", not "this is bad" — Claude's news and leadership
judgments are where they get weighed.

Adding a detector: add the `EarlySignalKind`, write one
`_detector(facts) -> EarlySignal | None` that returns None when any
input is missing, list it in `_DETECTORS`, and test the threshold on
both sides plus the missing-data case in
`tests/scorers/test_early_signals.py`.

## Do not add

- `leadership_scorer.py`
- `qualitative_scorer.py`

Both are intentionally absent. Claude scores those pillars in-session
via the slash commands and writes to `JudgmentStore`.

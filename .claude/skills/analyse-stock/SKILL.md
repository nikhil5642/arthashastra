---
name: analyse-stock
description: Full fundamental analysis of an Indian equity. Resolve, refresh, news, research, judge, render. Built-in anti-optimism anchoring so disrupted businesses get downgraded, not excused.
argument-hint: <ticker or company name>
---

Run the full four-pillar fundamental analysis for **$ARGUMENTS**.

**Hard rules — read every time:**
- Always resolve the input first, even if it already looks like a ticker.
- Use the CLI exclusively. Do not `import` Python modules or write
  ad-hoc Python snippets — every piece of data is exposed as a
  subcommand.
- The Python executable is `python3`, not `python`.
- Write the judgment file via the Write tool; do not use `echo >` /
  heredocs in bash.
- **Always re-judge from current data.** If a cached judgment exists
  at `data_cache/judgments/<TICKER>.json`, delete it first (via the
  Write tool replacing its content, or `rm` in bash) so you don't
  anchor on stale scores.
- **No optimism by default.** The rubric below has explicit downward
  anchors for each pillar. Apply them — a business in structural
  decline should not score BUY just because the balance sheet is clean.

## Step 0 — Wipe any stale cached judgment

```bash
rm -f data_cache/judgments/<TICKER>.json
```

Delete after step 1 once you know the resolved ticker.

## Step 1 — Resolve to a canonical ticker

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Pick the top NSE match. Capture two values for later steps:

- `TICKER` — bare ticker (e.g. `RELIANCE`)
- `COMPANY` — the `long_name` field (e.g. `Reliance Industries Limited`)

If the top match is ambiguous (e.g. "Reliance" → Industries, Power,
Infrastructure, or Capital), show the top 3 candidates to the user and
ask which one they meant before proceeding.

## Step 2 — Refresh live fundamentals

```bash
python3 -m fundamental_engine refresh <TICKER>
```

This hits live yfinance + screener.in and writes an extended
StockScore to `data_cache/scores.json`. Read the log line — it shows
the raw composite from deterministic fundamentals + pending
placeholders, which gives you the honest unbiased baseline before
you add qualitative judgment.

**Look at the fundamentals score carefully.** If the growth penalty
fired (look for the `VETO: 3y growth decelerating ...` evidence
bullet in the cached entry), that is your strongest signal that this
is a slow-growth business and you should scale the qualitative
pillars accordingly.

## Step 3 — Growth-trend check (before any other scoring)

Read `data_cache/scores.json` for the ticker and note the 3-year
revenue and PAT CAGRs from the fundamentals evidence bullets.

- **Both CAGRs ≥ 12%** — growth business; normal scoring applies.
- **Both CAGRs 7–12%** — steady compounder; normal scoring.
- **Both CAGRs 3–7%** — SLOW business. Hard caps kick in (see below).
- **Both CAGRs < 3%, or negative** — STRUCTURAL DECLINE. Scoring caps
  are severe; only an explicit pipeline-backed turnaround story
  rescues it.

Write down the growth bucket. It drives every cap in steps 4–8.

## Step 4 — Fetch recent news

```bash
python3 -m fundamental_engine news <TICKER> --days 30 --json
```

Read the JSON. Flag items as **material** when they touch: earnings,
governance (SEBI / fraud / auditor / investigation / probe),
corporate actions (M&A, buyback, demerger, dividend), promoter
(pledge, bulk deal, insider trading), rating (upgrade / downgrade /
default), leadership (CEO / chairman change). Drop the rest.

**Also look for structural disruption signals:**
- Is the core service line being automated (AI, RPA, cloud-native)?
- Are clients deferring or cancelling multi-year deals?
- Has the company's competitive moat eroded vs new-entrant AI-native
  vendors?
- Are margins compressing in the last 2–4 quarters?

These items don't need an explicit "material" tag — they shape your
prospects and news scores directly.

## Step 5 — Fetch research on the primary leader

Pick the company's current chairman / MD / CEO from your knowledge of
the Indian large-cap universe. If you're not confident, ask the user
before running the research fetch.

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

This hits live Wikipedia and (if `BRAVE_API_KEY` is set) Brave Search.
Cached to `data_cache/research/<slug>.json`.

## Step 6 — Classify leadership mode

- **FIRST_GEN** — listed < 10 years ago + original founder still
  active (Zomato, Nykaa, PolicyBazaar, Paytm, Delhivery)
- **MULTI_GEN_FAMILY** — promoter holding ≥ 25% + ≥2nd generation at
  the helm (Reliance / Tata / Bajaj / Birla / Godrej / Mahindra)
- **PROFESSIONAL** — institutional / low-promoter / professional CEO
  line (HDFC Bank, ICICI Bank, Infosys, TCS)

## Step 7 — Score leadership (0–100) with anti-optimism anchors

Apply the mode rubric, **then apply the anchors**.

**Base rubric:**
- FIRST_GEN: integrity 35% + track record 30% + reputation 20% +
  first-time founder bonus 15%
- MULTI_GEN_FAMILY: governance 30% + promoter pledge clean 25% +
  succession quality 20% + related-party clean 15% +
  family reputation 10%
- PROFESSIONAL: CEO track record 35% + board independence 20% +
  capital allocation 20% + governance 15% + tenure stability 10%

**Anti-optimism anchors (apply these — do not hand-wave):**

1. **Integrity veto** (all modes): ANY mention of fraud, SEBI action,
   auditor resignation, prior venture bankruptcy, or material integrity
   failure → clamp the leadership score to ≤ 30 regardless of other
   axes.
2. **Growth-accountability cap** (PROFESSIONAL mode, applies always):
   If the CEO's tenure covers the current 3y window and BOTH 3y CAGRs
   are < 7%, the CEO owns the outcome. `ceo_track_record` sub-axis
   is capped at 55 regardless of tenure length or internal promotion.
   State this explicitly in the evidence.
3. **Structural disruption cap** (all modes): If step 4 surfaced
   genuine disruption signals (core service line automating away,
   deal pipeline shrinking, margins compressing), the overall
   leadership score is capped at 65 regardless of everything else.
4. **"Stable insider" bonus trap**: Do not add points for "N-year
   veteran, internal promotion" unless the CEO has actually delivered
   growth or margin expansion on their watch. Tenure by itself
   doesn't count as track record.

## Step 8 — Score prospects (0–100) with anti-optimism anchors

**Base rubric:** moat 40% + tailwind 30% + capex/guidance clarity 30%.

**Anchors:**

1. **Tailwind caps given growth bucket (from step 3):**
   - CAGRs ≥ 12% → tailwind can be 60–90
   - CAGRs 7–12% → tailwind capped at 70
   - CAGRs 3–7% → tailwind capped at **45** (near neutral)
   - CAGRs < 3% / negative → tailwind capped at **30**
2. **Structural-disruption cap:** If the business is facing an active
   disruptive headwind (AI automating legacy service revenue, EV
   replacing ICE, digital-native replacing incumbent), the overall
   prospects score is capped at **55** regardless of moat.
3. **Moat without growth is brittle:** You can still score moat 70–90
   for a genuinely deep moat even when growth is slow, but the
   weighted prospects score must still obey the tailwind and
   disruption caps above.

## Step 9 — Score news (0–100) with anti-optimism anchors

Rolling 30-day weighted sentiment × relevance over material items
only (from step 4).

**Anchors:**
- Material negatives in governance or rating → clamp to ≤ 35.
- Structural-disruption headlines (layoffs, guidance cut, deal
  cancellation, margin compression) → clamp to ≤ 50.
- A quiet news window (no material items) is **neutral, not bullish** —
  score 55, not 70. "Nothing bad happened" is not a buy signal.

## Step 10 — Write the judgment file

Use the Write tool to create `data_cache/judgments/<TICKER>.json`
with this exact schema:

    {
      "ticker": "<TICKER>",
      "leader_name": "<Leader Name>",
      "leadership": {
        "score": <int 0-100>,
        "mode": "FIRST_GEN|MULTI_GEN_FAMILY|PROFESSIONAL",
        "integrity_score": <int 0-100>,
        "evidence": ["bullet 1", "bullet 2", "..."],
        "red_flags": ["..."],
        "summary": "one paragraph",
        "anchors_applied": ["growth_accountability_cap", "..."]
      },
      "prospects": {
        "score": <int 0-100>,
        "evidence": ["bullet 1", "..."],
        "anchors_applied": ["tailwind_cap_slow_growth", "structural_disruption_cap", "..."]
      },
      "news": {
        "score": <int 0-100>,
        "evidence": ["bullet cites a specific headline", "..."],
        "material_negatives": ["..."]
      },
      "updated_at": "<ISO timestamp e.g. 2026-04-11T10:00:00>",
      "judged_by": "claude"
    }

The `anchors_applied` fields are mandatory whenever a cap or clamp
fires. They're your audit trail and they stop future sessions from
silently re-inflating the scores.

## Step 11 — Render the final blended report

```bash
python3 -m fundamental_engine analyse <TICKER>
```

The pipeline reads your judgment from step 10 and blends it with the
deterministic fundamentals. Check the composite against a sanity
ladder:

- Composite **≥ 80** (STRONG_BUY): reserved for genuinely underpriced
  quality growers with ≥ 12% CAGRs and no disruption flags.
- Composite **70–79** (BUY): steady compounders (7–12% CAGR) with
  clean governance and a clear catalyst.
- Composite **55–69** (HOLD): slow growers, mixed tailwinds,
  balance-sheet-strong-but-growth-challenged names.
- Composite **40–54** (AVOID): structural disruption not yet priced
  in, governance fragility, or pledge-veto cases.
- Composite **< 40** (RED_FLAG): avoid.

If your composite lands in a band that feels wrong for the reality,
go back and recheck the anchors — do not override the math.

## Step 12 — Present to the user

In 4–6 sentences: verdict + composite + confidence, one key fact per
pillar, the main risks, and a clear buy / hold / avoid call. **Name
the anchors you applied** — the user needs to see you chose to dock
prospects for disruption, not that you forgot to.

---
name: analyse-stock
description: Full fundamental analysis of an Indian equity. Always starts from a clean slate — wipes any prior judgment, refreshes live data, then re-scores. Includes anti-optimism anchors so disrupted businesses get downgraded, not excused.
argument-hint: <ticker or company name>
---

Run the full four-pillar fundamental analysis for **$ARGUMENTS**.

**Every run starts from a clean slate.** Step 2 below deletes any
previously cached judgment for this ticker before re-fetching data, so
the user never has to worry about stale state leaking into the verdict.

**Hard rules — read every time:**
- Always resolve the input first, even if it already looks like a ticker.
- Use the CLI exclusively. Do not `import` Python modules or write
  ad-hoc Python snippets — every piece of data is exposed as a
  subcommand.
- The Python executable is `python3`, not `python`.
- Write the judgment file via the Write tool; do not use `echo >` /
  heredocs in bash.
- **No optimism by default.** The rubric below has explicit downward
  anchors for each pillar. Apply them — a business in structural
  decline should not score BUY just because the balance sheet is clean.

## Step 1 — Resolve to a canonical ticker

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Pick the top NSE match. Capture two values:

- `TICKER` — bare ticker (e.g. `RELIANCE`)
- `COMPANY` — the `long_name` field (e.g. `Reliance Industries Limited`)

If the top match is ambiguous, show the top 3 candidates to the user
and ask which one they meant before proceeding.

## Step 2 — Wipe any stale judgment AND refresh fundamentals

```bash
python3 -m fundamental_engine refresh <TICKER> --rejudge
```

The `--rejudge` flag deletes `data_cache/judgments/<TICKER>.json` (if
present) before running the live fetch. The command prints one of:

    rejudge: cleared TCS judgment (7 days old)
    rejudge: no prior judgment for TCS — this will be a fresh analysis

**Remember which line appeared.** You will report it to the user at
step 12 so they can see exactly what prior state existed.

The refresh that follows always hits live yfinance + screener.in and
writes the extended score to `data_cache/scores.json`. It also
populates `prices.db` with the real daily closes needed for the dip
detector.

## Step 3 — Sanity-check the deterministic baseline

```bash
python3 -m fundamental_engine analyse <TICKER>
```

Read the "Analysis context" banner and the fundamentals evidence:

- **`Cached judgment: none`** is what you want to see here — step 2
  wiped it. The pillars should all show "pending Claude judgment"
  except fundamentals.
- Look at the fundamentals score and whether the growth-deceleration
  VETO fired (`VETO: 3y growth decelerating ...`). If it fired, the
  company is in the slow bucket — every later anchor caps harder.
- Note the 3y revenue and PAT CAGRs — they drive the growth-bucket
  classification in step 4.

## Step 4 — Growth-bucket classification

From the CAGRs you just read, pick the bucket:

- **Both CAGRs ≥ 12%** — growth business; normal scoring applies.
- **Both CAGRs 7–12%** — steady compounder; normal scoring.
- **Both CAGRs 3–7%** — SLOW business. Hard caps kick in.
- **Both CAGRs < 3%, or negative** — STRUCTURAL DECLINE. Caps are
  severe; only an explicit pipeline-backed turnaround story rescues it.

Write down the bucket. It drives every cap in steps 6–9.

## Step 5 — Fetch recent news

```bash
python3 -m fundamental_engine news <TICKER> --days 30 --json
```

Flag items as **material** when they touch: earnings, governance
(SEBI / fraud / auditor / investigation / probe), corporate actions
(M&A, buyback, demerger, dividend), promoter (pledge, bulk deal,
insider trading), rating (upgrade / downgrade / default), leadership
(CEO / chairman change). Drop the rest.

**Also look for structural disruption signals:**
- Is the core service line being automated (AI, RPA, cloud-native)?
- Are clients deferring or cancelling multi-year deals?
- Has the competitive moat eroded vs new-entrant AI-native vendors?
- Are margins compressing in the last 2–4 quarters?

These don't need an explicit "material" tag — they shape your
prospects and news scores directly.

## Step 6 — Fetch research on the primary leader

Pick the company's current chairman / MD / CEO from your knowledge.
If you're not confident, ask the user.

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

This hits live Wikipedia and (if `BRAVE_API_KEY` is set) Brave Search.

## Step 7 — Classify the leadership mode

- **FIRST_GEN** — listed < 10 years + original founder still active
- **MULTI_GEN_FAMILY** — promoter ≥ 25% + ≥2nd generation at the helm
- **PROFESSIONAL** — institutional / low-promoter / professional CEO

## Step 8 — Score leadership (0–100) with anti-optimism anchors

**Base rubric:**
- FIRST_GEN: integrity 35% + track record 30% + reputation 20% + first-time bonus 15%
- MULTI_GEN_FAMILY: governance 30% + pledge clean 25% + succession 20% + related-party 15% + family reputation 10%
- PROFESSIONAL: CEO track record 35% + board independence 20% + capital allocation 20% + governance 15% + tenure stability 10%

**Anti-optimism anchors (apply these — do not hand-wave):**

1. **Integrity veto** (all modes): fraud, SEBI action, auditor
   resignation, prior venture bankruptcy, or material integrity failure
   → clamp leadership ≤ 30.
2. **Growth-accountability cap** (PROFESSIONAL mode): if the CEO's
   tenure covers the current 3y window and BOTH CAGRs are < 7%, the
   CEO owns the outcome. `ceo_track_record` ≤ 55 regardless of
   tenure length or internal promotion. State explicitly in evidence.
3. **Structural disruption cap** (all modes): core service line
   automating away, deal pipeline shrinking, margins compressing
   → leadership ≤ 65 regardless of everything else.
4. **"Stable insider" bonus trap**: do not add points for "N-year
   veteran, internal promotion" unless the CEO actually delivered
   growth or margin expansion on their watch. Tenure by itself is
   not track record.

## Step 9 — Score prospects (0–100) with anti-optimism anchors

**Base rubric:** moat 40% + tailwind 30% + capex/guidance clarity 30%.

**Anchors:**

1. **Tailwind caps given growth bucket (from step 4):**
   - CAGRs ≥ 12% → tailwind can be 60–90
   - CAGRs 7–12% → tailwind capped at 70
   - CAGRs 3–7% → tailwind capped at **45** (near neutral)
   - CAGRs < 3% / negative → tailwind capped at **30**
2. **Structural-disruption cap:** active disruptive headwind (AI
   automating legacy service revenue, EV replacing ICE, digital-
   native replacing incumbent) → prospects ≤ **55** regardless of moat.
3. **Moat without growth is brittle:** you can still score moat
   70–90 for a genuine moat when growth is slow, but the weighted
   prospects score still obeys the tailwind and disruption caps.

## Step 10 — Score news (0–100) with anti-optimism anchors

Rolling 30-day weighted sentiment × relevance over material items
only.

**Anchors:**
- Material negatives in governance / rating → clamp to ≤ 35.
- Structural-disruption headlines (layoffs, guidance cut, deal
  cancellation, margin compression) → clamp to ≤ 50.
- A quiet news window (no material items) is **neutral, not
  bullish** — score 55, not 70. "Nothing bad happened" is not a
  buy signal.

## Step 11 — Write the judgment file

Use the Write tool to create `data_cache/judgments/<TICKER>.json`
with this exact schema:

    {
      "ticker": "<TICKER>",
      "leader_name": "<Leader Name>",
      "leadership": {
        "score": <int 0-100>,
        "mode": "FIRST_GEN|MULTI_GEN_FAMILY|PROFESSIONAL",
        "integrity_score": <int 0-100>,
        "evidence": ["bullet 1", "..."],
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
      "updated_at": "<ISO timestamp>",
      "judged_by": "claude"
    }

`anchors_applied` is mandatory whenever a cap fires. It's your audit
trail for future sessions.

## Step 12 — Render the final blended report

```bash
python3 -m fundamental_engine analyse <TICKER>
```

Now the "Analysis context" banner should say `Cached judgment: 0 days
old` with your leader name. The composite is blended with your fresh
judgment. Sanity ladder:

- **≥ 80** (STRONG_BUY): underpriced quality grower, ≥ 12% CAGRs,
  no disruption flags.
- **70–79** (BUY): steady compounder (7–12% CAGR), clean governance,
  clear catalyst.
- **55–69** (HOLD): slow grower, mixed tailwinds, or
  balance-sheet-strong-but-growth-challenged.
- **40–54** (AVOID): structural disruption not priced in, governance
  fragility, or pledge-veto cases.
- **< 40** (RED_FLAG): avoid.

If your composite lands in a band that feels wrong for reality, go
back and recheck the anchors — do not override the math.

## Step 13 — Present to the user

In your response, include these three things in order:

1. **Fresh-state confirmation.** One sentence stating what step 2
   reported: either "Started fresh — no prior judgment existed" or
   "Wiped a {N}-day-old judgment and started fresh".
2. **Verdict + composite + confidence**, one key fact per pillar,
   the main risks, a clear buy / hold / avoid call.
3. **Name the anchors you applied.** The user needs to see that
   prospects was docked for disruption, not that you forgot to dock
   it.

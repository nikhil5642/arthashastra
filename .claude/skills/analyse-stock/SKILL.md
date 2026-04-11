---
name: analyse-stock
description: Full fundamental analysis of an Indian equity. Resolve first, then refresh, news, research, judge, render.
argument-hint: <ticker or company name>
---

Run the full four-pillar fundamental analysis for **$ARGUMENTS**.

**Rules:**
- Always resolve the input first, even if it already looks like a ticker.
- Use the CLI exclusively. Do not `import` Python modules or write
  ad-hoc Python snippets — every piece of data is exposed as a
  subcommand.
- The Python executable is `python3`, not `python`.
- Write the judgment file via the Write tool; do not use `echo >` /
  heredocs in bash.

## Step 1 — Resolve to a canonical ticker

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Pick the top NSE match. Capture two values for later steps:

- `TICKER` — bare ticker (e.g. `RELIANCE`)
- `COMPANY` — the `long_name` field (e.g. `Reliance Industries Limited`)

If the top match is ambiguous (multiple companies with similar names,
e.g. "Reliance" could be Industries, Power, Infrastructure, or
Capital), show the top 3 candidates to the user and ask which one
they meant before proceeding.

## Step 2 — Refresh live fundamentals

```bash
python3 -m fundamental_engine refresh <TICKER>
```

This hits live yfinance (statements + history) + screener.in (ROE /
ROCE / promoter holding / pledge) and writes an extended StockScore
to `data_cache/scores.json`. The composite will be HOLD-ish because
leadership / prospects / news are still marked `pending Claude
judgment` — that's you.

## Step 3 — Fetch recent news

```bash
python3 -m fundamental_engine news <TICKER> --days 30 --json
```

This reads the cached `company_name` from step 2, runs a phrase-quoted
Google News RSS search, and saves the result to
`data_cache/news/<TICKER>.json`. Read the JSON.

Mark items as **material** if they touch: earnings, governance (SEBI,
fraud, auditor, investigation, probe), corporate actions (M&A,
buyback, demerger, dividend), promoter (pledge, bulk deal, insider
trading), rating (upgrade/downgrade/default), leadership (CEO/chairman
change). Drop the rest.

## Step 4 — Fetch research on the primary leader

Pick the company's current chairman / MD / CEO from your knowledge of
the Indian large-cap universe. If you're not confident, ask the user
before running the research fetch.

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

This hits live Wikipedia and (if `BRAVE_API_KEY` is set) Brave Search.
Cached to `data_cache/research/<slug>.json`.

## Step 5 — Classify leadership mode

- **FIRST_GEN** — listed < 10 years ago + original founder still
  active (Zomato, Nykaa, PolicyBazaar, Paytm, Delhivery)
- **MULTI_GEN_FAMILY** — promoter holding ≥ 25% + ≥2nd generation at
  the helm (Reliance / Tata / Bajaj / Birla / Godrej / Mahindra)
- **PROFESSIONAL** — institutional / low-promoter / professional CEO
  line (HDFC Bank, ICICI Bank, Infosys, TCS)

## Step 6 — Apply the mode-specific rubric

- **FIRST_GEN**: integrity 35% + track record 30% + reputation 20% +
  first-time founder bonus 15%
- **MULTI_GEN_FAMILY**: governance 30% + promoter pledge clean 25% +
  succession quality 20% + related-party clean 15% + family reputation 10%
- **PROFESSIONAL**: CEO track record 35% + board independence 20% +
  capital allocation 20% + governance 15% + tenure stability 10%

**Integrity veto** (all modes): ANY mention of fraud, SEBI action,
auditor resignation, or material integrity failure → clamp the final
leadership score to ≤ 30 regardless of other axes.

## Step 7 — Score prospects (0–100)

Sub-axes: moat 40% + tailwind 30% + capex/guidance clarity 30%.
Read the research bundle and the fundamentals numbers from step 2
(growth CAGRs, promoter holding, capex hints).

## Step 8 — Score news (0–100)

Rolling 30-day weighted sentiment × relevance over **material items
only** (from step 3). Material negatives in governance or rating
categories clamp the score to ≤ 35.

## Step 9 — Write the judgment file

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
        "summary": "one paragraph"
      },
      "prospects": {
        "score": <int 0-100>,
        "evidence": ["bullet 1", "..."]
      },
      "news": {
        "score": <int 0-100>,
        "evidence": ["bullet cites a specific headline", "..."],
        "material_negatives": ["..."]
      },
      "updated_at": "<ISO timestamp e.g. 2026-04-11T10:00:00>",
      "judged_by": "claude"
    }

## Step 10 — Render the final blended report

```bash
python3 -m fundamental_engine analyse <TICKER>
```

The pipeline reads your judgment from step 9 and blends it with the
deterministic fundamentals. Every pillar now shows a real score and
the composite reflects your full judgment.

## Step 11 — Present to the user

In 4–6 sentences: verdict + composite + confidence, one key fact per
pillar, the main risks, and a clear buy / hold / avoid call.

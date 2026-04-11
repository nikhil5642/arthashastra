---
name: market-brief
description: Market overview — cached universe stats, active dip candidates, sector coverage, recent signals, and a Claude-written narrative.
---

Produce a market overview for the user. Python renders the
deterministic baseline; you add the narrative.

## Step 1 — Run the deterministic brief

```bash
python3 -m fundamental_engine brief
```

This reads `data_cache/scores.json`, `data_cache/dip_candidates.json`,
and `data_cache/signals.json`. The output shows: top-by-composite,
active dips, sector coverage, leadership red flags, recent score
updates. It also saves `data_cache/reports/brief_<date>.md`.

## Step 2 — Optional: inspect stale-or-missing tickers

```bash
cat data_cache/scores.json
```

Look at the `last_updated` timestamps. If most are stale (> 2 days)
suggest a `/refresh-now all` before presenting the brief.

## Step 3 — Write the narrative in your response

In 100–150 words, cover:

- Overall tone of the cached universe (breadth, leaders, laggards)
- Top 3 dip candidates with a one-line thesis each
- Any red flags the user should know about
- Concrete next action: wait, add to watchlist, buy, trim, or run
  `/analyse-stock <TICKER>` on a specific name

This is the "morning coffee" briefing after the user has been away
for weeks. Be specific, not generic.

---
name: market-brief
description: Overview of the cached universe across every market — top names, active dip candidates, sector coverage, integrity vetoes, stale rows — plus a Claude-written narrative.
---

Produce an overview for the user. Python renders the deterministic
baseline; you add the narrative.

## Step 1 — Run the deterministic brief

```bash
python3 -m fundamental_engine brief
```

This reads the cached scores, dip candidates, and signals, and shows:
top-by-composite with each name's early-signal tally, **early concerns**
and **early strengths** across the cache (the most severe per ticker),
**what changed in the last 14 days** (verdict moves, concerns and
strengths that are new or worse), active dips, sector coverage, integrity
vetoes, and stale rows (older than 2 days). It also saves
`data_cache/reports/brief_<date>.md`.

The early signals are as fresh as each row's last refresh — a concern
only appears after `refresh` / `analyse` has seen it.

## Step 2 — Check freshness

If the brief lists many stale rows, suggest `/refresh-now all` before the
user acts on anything. For a specific name, `python3 -m
fundamental_engine inspect <TICKER>` shows its full cached state.

## Step 3 — Write the narrative in your response

In 100–150 words, cover:

- **What changed** since the user last looked: verdict moves and new
  urgent concerns first, by ticker
- Overall tone of the cached universe — breadth, leaders, laggards, and
  how it splits across markets
- Top 3 dip candidates with a one-line thesis each, and the early
  concerns each one carries
- Any integrity vetoes or urgent concerns the user should know about
- Concrete next action: wait, add to watchlist, buy, trim, or run
  `/analyse-stock <TICKER>` on a specific name

This is the "morning coffee" briefing after the user has been away for
weeks. Be specific, not generic.

---
name: explain-score
description: Explain the most recent cached score for any ticker — per-pillar evidence, gates, data quality — with no re-fetch.
argument-hint: <ticker or company name>
---

Walk the user through an existing cached score without re-fetching.

## Step 1 — Resolve if needed

If `$ARGUMENTS` is not already a full ticker symbol:

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Capture `TICKER` (e.g. `MSFT`, `RELIANCE.NS`, `0700.HK`).

## Step 2 — Read everything cached for the ticker

```bash
python3 -m fundamental_engine inspect <TICKER>
```

This prints JSON with three parts: `score` (the scores.json row — pillar
scores, evidence, `gates_applied`, and `early_signals`: each concern or
strength with its severity and message), `judgment` (Claude's last
qualitative pass, or null), and `snapshot` (every fetched field,
`field_sources` provenance, and `data_conflicts`). If it reports nothing cached, tell
the user to run `/analyse-stock <TICKER>` first. A null judgment means
the qualitative pillars are pending — say so.

## Step 3 — Present the audit trail

For each pillar (fundamentals 35%, valuation 15%, leadership 25%,
prospects 15%, news 10%):

- State the pillar score
- List its evidence bullets (from the score and the judgment)
- Explain in one sentence how the sub-score was reached

Then show the composite and verdict, and how
`0.35·fundamentals + 0.15·valuation + 0.25·leadership + 0.15·prospects + 0.10·news`
produced it. Fundamentals and valuation are deterministic (Python);
the other three are Claude's judgment.

Things to read out when they appear:

- **`ADJ:` lines in the valuation evidence** — the pillar counted less
  than the reported profit: one-off gains were taken out, or the margin
  sits far above the company's own norm. Give both P/Es and say which
  one the score used.
- **A valuation of exactly 50 with zero confidence** — it couldn't be
  scored, not "the price is fair": either no price-based ratio exists,
  or the snapshot is `quote_unreliable` (a depositary receipt, or
  statements with a wrong currency label). Point to the primary listing.
- **`EXPIRED:` / `STALE:` at the top of a pillar's evidence** — the
  cached judgment has aged: news older than 30 days no longer counts
  (the pillar is neutral), leadership and prospects older than 90 days
  count at lower confidence. Suggest `/analyse-stock <TICKER>`.
- **`gates_applied` contains `integrity_veto`** — the composite was
  capped at 54 (AVOID) after the blend. A veto never expires.
- **Early signals** — list the concerns and strengths, most severe
  first. They state what the newest data shows; they did not move the
  score.

Mention any data conflicts or missing fields that limit confidence.

## Step 4 — Answer follow-up questions

The user is likely challenging a number. Point to the exact cached field
— `inspect <TICKER> --section <field>` prints one field at a time.

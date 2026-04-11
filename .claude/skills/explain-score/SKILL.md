---
name: explain-score
description: Explain the most recent cached score for a ticker — per-pillar evidence, no re-fetch.
argument-hint: <ticker or company name>
---

Walk the user through an existing cached score without re-fetching
anything.

## Step 1 — Resolve if needed

If `$ARGUMENTS` is not already a valid ticker symbol:

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Capture `TICKER`.

## Step 2 — Read the cached score

```bash
cat data_cache/scores.json
```

Find the entry for `TICKER`. If it's not there, tell the user to run
`/analyse-stock <TICKER>` first.

## Step 3 — Read any cached judgment

```bash
cat data_cache/judgments/<TICKER>.json
```

This has richer per-pillar reasoning from the last `/analyse-stock`
run. If the file doesn't exist, the pillars will be `pending Claude
judgment` and you should tell the user so.

## Step 4 — Present the audit trail

For each pillar (leadership 40%, fundamentals 30%, prospects 15%,
news 15%):

- State the pillar score
- List every evidence bullet from both files
- Explain in one sentence how the sub-score was reached

Then show the composite and verdict, and explain exactly how
`0.40·leadership + 0.30·fundamentals + 0.15·prospects + 0.15·news`
yielded the final number.

## Step 5 — Answer follow-up questions

The user is likely challenging or verifying a number. Be specific
and point to the cached source data.

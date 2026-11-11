---
name: refresh-now
description: Force a live fundamentals refresh for one ticker (any market) or the entire cached universe.
argument-hint: <ticker, company name, or 'all'>
---

Refetch live fundamentals so subsequent commands (`analyse`, `pick`,
`brief`) work off the latest numbers and FX rates.

## Step 1 — Resolve first (unless input is 'all')

If `$ARGUMENTS` is `all`, skip to Step 2. Otherwise:

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Capture the primary listing's full Yahoo symbol as `TICKER` (`MSFT`,
`RELIANCE.NS`, `0700.HK`).

## Step 2 — Run refresh

Single ticker:

```bash
python3 -m fundamental_engine refresh <TICKER>
```

Entire cached universe (one ticker at a time, with a short pause between):

```bash
python3 -m fundamental_engine refresh all
```

## Step 3 — Report the outcome

For a single ticker, report the composite + verdict, the early-signal
tally (`early signals: 3 concerns (1 urgent) · 1 strength`) and how many
of them are new since the last look. For `refresh all`, report the
ok / no_data / error counts and name every ticker whose line shows an
urgent concern. `python3 -m fundamental_engine brief` then lists what
changed across the cache.

A refresh re-scores with the cached judgment at its current age: news
judged more than 30 days ago no longer counts, and leadership / prospects
judged more than 90 days ago count at lower confidence. If the composite
moved for that reason alone, say so and suggest `/analyse-stock`.

If no judgment was ever written for the ticker, remind the user to run
`/analyse-stock <TICKER>` afterwards — a bare refresh leaves the three
qualitative pillars pending.

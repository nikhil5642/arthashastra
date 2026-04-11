---
name: refresh-now
description: Force a live fundamentals refresh for one ticker or the entire cached universe.
argument-hint: <ticker, company name, or 'all'>
---

Bypass freshness gates and refetch live fundamentals so subsequent
commands (`analyse`, `pick`, `brief`) work off the latest numbers.

## Step 1 — Resolve first (unless input is 'all')

If `$ARGUMENTS` is `all`, skip to Step 2. Otherwise:

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Capture the top-match ticker as `TICKER`.

## Step 2 — Run refresh

Single ticker:

```bash
python3 -m fundamental_engine refresh <TICKER>
```

Entire cached universe:

```bash
python3 -m fundamental_engine refresh all
```

## Step 3 — Report the outcome

For a single ticker, report the resulting composite + verdict. For
`refresh all`, report the ok / no_data / error counts.

If the user wanted a single stock refreshed but no judgment was ever
written, remind them to run `/analyse-stock <TICKER>` afterwards to
get a meaningful verdict (a bare refresh leaves the three qualitative
pillars pending).

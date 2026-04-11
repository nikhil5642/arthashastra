---
name: resolve-ticker
description: Resolve a free-text company name to an NSE/BSE ticker via yfinance search.
argument-hint: <company name>
---

Turn a free-text company name into a canonical NSE/BSE ticker.

## Step 1 — Run the resolver

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

The resolver automatically cleans common corporate suffixes ("ltd",
"limited", "corp", "inc") before searching Yahoo, so inputs like
`"reliance ltd"` or `"HDFC Bank Limited"` resolve correctly.

## Step 2 — Pick a match

- If there's one clear top candidate and nothing else is close, use it.
- If the top is ambiguous (e.g. "Reliance" could be Industries /
  Power / Infrastructure / Capital), show the top 3 candidates to
  the user and ask which one they meant.
- Prefer NSE over BSE — that's where Indian liquidity sits.

## Step 3 — Return the chosen ticker

Reply with just the ticker and the full name:

> Resolved "$ARGUMENTS" → **RELIANCE** (Reliance Industries Limited, NSE)

If the resolver returned nothing for every variant, tell the user
and suggest a shorter / different query.

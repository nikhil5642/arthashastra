---
name: resolve-ticker
description: Resolve a free-text company name to its ticker on any exchange (US, India, China / HK, Japan, Europe, …) via Yahoo search.
argument-hint: <company name — e.g. Microsoft, Tencent, Reliance Industries>
---

Turn a free-text company name into a canonical ticker.

## Step 1 — Run the resolver

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

The resolver strips legal suffixes ("Ltd", "Inc", "Corp", "PLC") before
searching Yahoo, so `"reliance ltd"` or `"Toyota Motor Corp"` resolve
correctly. It returns equities only, on every exchange.

## Step 2 — Pick a match

Tickers are Yahoo symbols: US listings have no suffix (`MSFT`, `GOOGL`);
other exchanges carry one (`RELIANCE.NS`, `0700.HK`, `7203.T`, `SAP.DE`,
`SHEL.L`). One company often has several listings:

- Prefer the **primary home-market listing** — where the company is
  incorporated and most liquid: `7203.T` over the `TM` ADR, `0700.HK`
  over the `TCEHY` OTC line, `RELIANCE.NS` over `RELIANCE.BO`.
- Ignore secondary venues (Mexico `.MX`, Frankfurt `.F`, OTC) unless the
  user asked for that listing.
- If the name is ambiguous ("Reliance" could be Industries / Power /
  Infrastructure), show the top 3 candidates and ask which one.

## Step 3 — Return the chosen ticker

Reply with the ticker, full name, and exchange:

> Resolved "$ARGUMENTS" → **MSFT** (Microsoft Corporation, NASDAQ)

If the resolver returned nothing for every variant, say so and suggest a
shorter or different query.

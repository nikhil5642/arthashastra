"""Shared test setup — placeholder.

Tests construct `BalanceSheetSnapshot` / `ScorerResult` / `NewsItem`
directly in each test body (no fixture JSON files). Monkeypatching of
live data source adapters (yfinance, screener.in, RSS, Wikipedia,
Brave) happens at the function level in the specific test files.

There are no shared fixtures and no project-wide env setup — the
config layer has no sandbox/live split, so there's nothing to mock
here at import time.
"""

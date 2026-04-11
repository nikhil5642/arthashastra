"""Shared test setup — isolate env, provide helper constructors for test inputs.

Tests construct BalanceSheetSnapshot / ScorerResult / NewsItem directly in
each test body (no fixture JSON files). Monkeypatching of live data source
adapters (yfinance, screener.in, RSS, Wikipedia, Brave) happens at the
function level in the specific test files.
"""

from __future__ import annotations

import os

os.environ.setdefault("ANALYST_ENV", "sandbox")

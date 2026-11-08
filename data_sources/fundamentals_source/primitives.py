"""Parsing primitives shared by the yfinance and screener.in adapters.

Everything in this module is pure-functional — no network, no I/O, no
logging. These helpers stay small and testable so future changes to one
adapter don't accidentally affect the other.

Alignment rule: whenever two numbers are combined into a ratio they must
come from the *same period*. `_latest_aligned` (same column of one table)
and `_value_at_label` (same column label across two tables) exist so no
adapter pairs a TTM cell with an annual one, or FY25 profit with FY26
assets.
"""

from __future__ import annotations

import math
from datetime import date

# ── Screener.in transport constants ──────────────────────────────────────────

SCREENER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SCREENER_BASE_URL = "https://www.screener.in/company"
HTTP_TIMEOUT_SECONDS = 20.0


# ── Scalar parsers ───────────────────────────────────────────────────────────


def _parse_pct(text: str | None) -> float | None:
    """Parse '10.5%' / '1,200.00' / ' 8.40 ' → float; None on failure."""
    if text is None:
        return None
    try:
        cleaned = text.strip().replace(",", "").rstrip("%")
        return _finite(float(cleaned))
    except (ValueError, AttributeError):
        return None


def _parse_float(text: str | None) -> float | None:
    """Parse '1,827,979' / '1350' → float; None on failure."""
    if text is None:
        return None
    try:
        return _finite(float(text.strip().replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _finite(v: float) -> float | None:
    """`float()` happily parses 'nan' / 'inf' — treat those as missing."""
    return v if math.isfinite(v) else None


# ── Series helpers (None-tolerant) ───────────────────────────────────────────


def _latest(values: list[float | None] | None) -> float | None:
    """Rightmost non-None value in a None-padded series."""
    if not values:
        return None
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _latest_aligned(
    *series: list[float | None] | None,
) -> tuple[float, ...] | None:
    """Values from the rightmost column where EVERY series is populated.

    Use this instead of independent `_latest` calls whenever the values
    are combined (PBT + interest, borrowings / equity): independent
    lookups can silently pair a TTM cell of one row with an annual cell
    of another.
    """
    if not series or any(not s for s in series):
        return None
    width = min(len(s) for s in series if s)
    for i in range(width - 1, -1, -1):
        column = [s[i] for s in series if s]
        if all(v is not None for v in column):
            return tuple(v for v in column if v is not None)
    return None


def _latest_with_label(
    headers: list[str], values: list[float | None] | None
) -> tuple[str, float] | None:
    """(column label, value) of the rightmost populated cell."""
    if not values:
        return None
    for i in range(min(len(headers), len(values)) - 1, -1, -1):
        v = values[i]
        if v is not None:
            return headers[i], v
    return None


def _value_at_label(
    headers: list[str], values: list[float | None] | None, label: str
) -> float | None:
    """The cell under column `label`, matched across tables by header text."""
    if not values or label not in headers:
        return None
    i = headers.index(label)
    return values[i] if i < len(values) else None


def _latest_and_yoy_delta(
    values: list[float | None] | None,
) -> tuple[float | None, float | None]:
    """(latest, latest − same quarter a year earlier) on a quarterly row.

    Positional: quarterly columns are consecutive quarters, so the
    year-ago value sits 4 columns left of the latest populated one.
    """
    if not values:
        return None, None
    for idx in range(len(values) - 1, -1, -1):
        latest = values[idx]
        if latest is None:
            continue
        prior = values[idx - 4] if idx >= 4 else None
        delta = round(latest - prior, 2) if prior is not None else None
        return latest, delta
    return None, None


def _annual_headers(headers: list[str]) -> list[str]:
    """Headers with any trailing TTM column removed (see `_drop_ttm`)."""
    if headers and headers[-1].strip().upper() == "TTM":
        return headers[:-1]
    return headers


def _drop_ttm(
    headers: list[str], values: list[float | None] | None
) -> list[float | None]:
    """Return `values` with any trailing TTM column trimmed off.

    Screener appends a `TTM` column to P&L tables for most Indian
    companies. TTM mixes a partial fiscal year with a trailing-12-months
    window, so `values[-1]` is newer than `values[-2]` by ~6 months
    rather than 12 — using it as the terminal point of a 3-year CAGR
    yields a nonsense window (e.g. Mar-23 → TTM-25.5 ÷ 3, underestimating
    growth). Drop it before anything trend-based.
    """
    if not values:
        return []
    if headers and headers[-1].strip().upper() == "TTM":
        return values[:-1]
    return values


def _screener_cagr(
    values: list[float | None] | None, *, years: int
) -> float | None:
    """CAGR from an annual row (TTM already dropped by caller).

    Requires `years + 1` populated points. Picks the last and `years`-th-
    last populated points. Returns None when either endpoint is missing,
    zero, or negative.
    """
    if not values or len(values) < years + 1:
        return None
    current = values[-1]
    past = values[-(years + 1)]
    if current is None or past is None:
        return None
    if past <= 0 or current <= 0:
        return None
    return round((((current / past) ** (1 / years)) - 1) * 100, 2)


# ── Quarterly change lookups ─────────────────────────────────────────────────


def pct_change_by_date(
    values: list[float],
    dates: list[str],
    *,
    current_idx: int,
    days_ago: int,
    tolerance: int,
) -> float | None:
    """% change vs the entry dated ~`days_ago` before `dates[current_idx]`.

    `values` / `dates` are aligned and newest-first. The lookup is by
    date, not position, so a missing quarter can't silently turn a YoY
    comparison into a 15-month one. Returns None when no counterpart sits
    within ±`tolerance` days, or the past value is zero / negative.
    """
    if current_idx >= len(values) or current_idx >= len(dates):
        return None
    try:
        current_date = date.fromisoformat(dates[current_idx])
    except (TypeError, ValueError):
        return None

    best_idx: int | None = None
    best_gap = tolerance + 1
    for j in range(current_idx + 1, min(len(values), len(dates))):
        try:
            past_date = date.fromisoformat(dates[j])
        except (TypeError, ValueError):
            continue
        gap = abs((current_date - past_date).days - days_ago)
        if gap < best_gap:
            best_gap, best_idx = gap, j
    if best_idx is None:
        return None
    past = values[best_idx]
    if past <= 0:
        return None
    return (values[current_idx] - past) / past * 100


def latest_yoy_growth(values: list[float], dates: list[str]) -> float | None:
    """Newest quarter vs the same quarter a year earlier, rounded to 0.01."""
    change = pct_change_by_date(
        values, dates, current_idx=0, days_ago=365, tolerance=45
    )
    return round(change, 2) if change is not None else None


# ── Date helpers ─────────────────────────────────────────────────────────────

_MONTH_MAP: dict[str, str] = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_MONTH_END_DAY: dict[str, str] = {
    "01": "31", "02": "28", "03": "31", "04": "30",
    "05": "31", "06": "30", "07": "31", "08": "31",
    "09": "30", "10": "31", "11": "30", "12": "31",
}


def _screener_month_to_iso(label: str) -> str | None:
    """Convert 'Sep 2025' → '2025-09-30', 'Mar 2025' → '2025-03-31'.

    Approximates end-of-month day (Feb always 28 for simplicity — we're
    not doing calendar arithmetic, only quarter labelling).
    """
    if not label:
        return None
    parts = label.strip().split()
    if len(parts) != 2:
        return None
    mon_key = parts[0][:3].lower()
    if mon_key not in _MONTH_MAP:
        return None
    try:
        year = int(parts[1])
    except ValueError:
        return None
    month = _MONTH_MAP[mon_key]
    return f"{year:04d}-{month}-{_MONTH_END_DAY[month]}"

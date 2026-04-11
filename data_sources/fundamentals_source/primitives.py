"""Parsing primitives shared by the yfinance and screener.in adapters.

Everything in this module is pure-functional — no network, no I/O, no
logging. These helpers stay small and testable so future changes to one
adapter don't accidentally affect the other.
"""

from __future__ import annotations

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
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def _parse_float(text: str | None) -> float | None:
    """Parse '1,827,979' / '1350' → float; None on failure."""
    if text is None:
        return None
    try:
        return float(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


# ── Series helpers (None-tolerant) ───────────────────────────────────────────


def _latest(values: list[float | None] | None) -> float | None:
    """Rightmost non-None value in a None-padded series."""
    if not values:
        return None
    for v in reversed(values):
        if v is not None:
            return v
    return None


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

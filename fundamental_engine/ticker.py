"""Ticker symbol normalization."""

from __future__ import annotations

NSE_SUFFIX = ".NS"
BSE_SUFFIX = ".BO"


def canonical(raw: str) -> str:
    """Bare canonical ticker without exchange suffix ('reliance' → 'RELIANCE')."""
    t = raw.strip().upper()
    if t.endswith(NSE_SUFFIX) or t.endswith(BSE_SUFFIX):
        return t[:-3]
    return t


def yf_symbol(raw: str, *, prefer: str = "NSE") -> str:
    """Yahoo Finance symbol ('RELIANCE' → 'RELIANCE.NS')."""
    t = raw.strip().upper()
    if t.endswith(NSE_SUFFIX) or t.endswith(BSE_SUFFIX):
        return t
    return f"{t}{NSE_SUFFIX if prefer.upper() == 'NSE' else BSE_SUFFIX}"


def slug(raw: str) -> str:
    """Filesystem-safe slug for a name ('Mukesh Ambani' → 'mukesh_ambani')."""
    return "_".join(raw.strip().lower().split())

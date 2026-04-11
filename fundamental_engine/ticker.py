"""Ticker symbol and name normalization helpers."""

from __future__ import annotations

import re
import unicodedata

NSE_SUFFIX = ".NS"
BSE_SUFFIX = ".BO"

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_COLLAPSE_RE = re.compile(r"[-\s]+")

# Characters to drop entirely (rather than replace with a separator). Used
# for apostrophes so "O'Shaughnessy" becomes "oshaughnessy", not
# "o_shaughnessy".
_SLUG_DROP_CHARS = re.compile(r"['’`]")


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
    """Filesystem-safe slug for a name.

    ASCII-lowercase, underscores between words, all punctuation dropped.

        'Mukesh Ambani'         → 'mukesh_ambani'
        'N. R. Narayana Murthy' → 'n_r_narayana_murthy'
        "O'Shaughnessy"         → 'oshaughnessy'
    """
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    # Drop apostrophes inline so "O'Shaughnessy" → "OShaughnessy", not
    # "O Shaughnessy". Every other punctuation becomes a word separator.
    no_quotes = _SLUG_DROP_CHARS.sub("", ascii_only)
    stripped = _SLUG_STRIP_RE.sub(" ", no_quotes).strip()
    collapsed = _SLUG_COLLAPSE_RE.sub("_", stripped)
    return collapsed.lower()

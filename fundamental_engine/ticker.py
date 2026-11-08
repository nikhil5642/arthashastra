"""Ticker identity and name normalization helpers."""

from __future__ import annotations

import re
import unicodedata

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_COLLAPSE_RE = re.compile(r"[-\s]+")

# Characters to drop entirely (rather than replace with a separator). Used
# for apostrophes so "O'Shaughnessy" becomes "oshaughnessy", not
# "o_shaughnessy".
_SLUG_DROP_CHARS = re.compile(r"['’`]")


def canonical(raw: str) -> str:
    """The canonical ticker: the Yahoo symbol, uppercased.

        'msft'        → 'MSFT'          (US listings carry no suffix)
        'reliance.ns' → 'RELIANCE.NS'
        '0700.hk'     → '0700.HK'

    The exchange suffix is part of the identity — 'BHP' (NYSE) and
    'BHP.AX' (ASX) are different listings — so it is never stripped and
    no market is ever assumed.
    """
    return raw.strip().upper()


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

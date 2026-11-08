"""Parse budget strings like '100000', '50k', '2.5m', '1L', '1 cr' → amount.

The amount is in the user's base currency (`BASE_CURRENCY`). Both
international and Indian magnitudes are accepted, whatever the currency:
    K       = thousand
    M       = million         (also written `mn`)
    B       = billion         (also written `bn`)
    Lakh    = 100,000         (also written `L` or `Lac`)
    Crore   = 10,000,000      (also written `Cr`)

These are typed as a real `Magnitude` enum so downstream code can
reason about them structurally. The spellings a human might type are
only input-parser synonyms, mapped to the enum at the boundary.
"""

from __future__ import annotations

import re
from enum import Enum


class Magnitude(float, Enum):
    """Magnitude multipliers, typed so they can't be confused.

    `float` base class means arithmetic with Magnitude members works
    transparently: `100 * Magnitude.LAKH == 10_000_000.0`.
    """

    K = 1_000.0
    LAKH = 100_000.0
    MILLION = 1_000_000.0
    CRORE = 10_000_000.0
    BILLION = 1_000_000_000.0


# Free-form string spellings a human might type → the typed enum.
# Keys are lowercased and space-stripped before lookup.
_MAGNITUDE_SYNONYMS: dict[str, Magnitude] = {
    "k": Magnitude.K,
    "m": Magnitude.MILLION,
    "mn": Magnitude.MILLION,
    "million": Magnitude.MILLION,
    "b": Magnitude.BILLION,
    "bn": Magnitude.BILLION,
    "billion": Magnitude.BILLION,
    "l": Magnitude.LAKH,
    "lac": Magnitude.LAKH,
    "lakh": Magnitude.LAKH,
    "cr": Magnitude.CRORE,
    "crore": Magnitude.CRORE,
}

# Regex alternation is generated from the synonym keys so there's a
# single source of truth and no typo drift between the two.
_UNIT_ALTERNATION = "|".join(sorted(_MAGNITUDE_SYNONYMS, key=len, reverse=True))
_BUDGET_RE = re.compile(rf"^(\d+(?:\.\d+)?)({_UNIT_ALTERNATION})?$")


def parse_budget(raw: str) -> float | None:
    """Return the budget amount as a float, or None if unparseable."""
    s = raw.strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return None
    m = _BUDGET_RE.match(s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        return num
    return num * _MAGNITUDE_SYNONYMS[unit].value

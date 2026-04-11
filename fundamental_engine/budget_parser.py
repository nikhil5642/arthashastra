"""Parse budget strings like '100000', '1L', '2.5L', '10k', '1 cr' → INR.

Indian number notation uses a fixed set of magnitudes:
    K     = thousand
    Lakh  = 100,000           (also written `L` or `Lac`)
    Crore = 10,000,000        (also written `Cr`)

These are typed as a real `Magnitude` enum so downstream code can
reason about them structurally. The string spellings a human might
actually type ('l', 'lakh', 'lac', 'k', 'cr', 'crore') are only used
as input-parser synonyms, mapped to the enum at the boundary.
"""

from __future__ import annotations

import re
from enum import Enum


class Magnitude(float, Enum):
    """Indian magnitude multipliers, typed so they can't be confused.

    `float` base class means arithmetic with Magnitude members works
    transparently: `100 * Magnitude.LAKH == 10_000_000.0`.
    """

    K = 1_000.0
    LAKH = 100_000.0
    CRORE = 10_000_000.0


# Free-form string spellings a human might type → the typed enum.
# Keys are lowercased and space-stripped before lookup.
_MAGNITUDE_SYNONYMS: dict[str, Magnitude] = {
    "k": Magnitude.K,
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
    """Return budget in INR as float, or None if unparseable."""
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

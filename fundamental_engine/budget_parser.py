"""Parse budget strings like '100000', '1L', '2.5L', '10k', '1 cr'."""

from __future__ import annotations

import re

_UNIT_MULT: dict[str, float] = {
    "l": 100_000.0,
    "lakh": 100_000.0,
    "lac": 100_000.0,
    "cr": 10_000_000.0,
    "crore": 10_000_000.0,
    "k": 1_000.0,
}


def parse_budget(raw: str) -> float | None:
    """Return budget in INR as float, or None if unparseable."""
    s = raw.strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)(l|lakh|lac|cr|crore|k)?$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        return num
    return num * _UNIT_MULT[unit]

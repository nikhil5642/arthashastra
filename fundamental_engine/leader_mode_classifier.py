"""Classify a listed Indian company into a leadership mode.

Three modes per PLAN.md §Leadership scorer:

    FIRST_GEN        — listed < 10 years AND single-gen founder still active
    MULTI_GEN_FAMILY — promoter holding ≥ 25% AND ≥2nd generation at the helm
    PROFESSIONAL     — everything else (professional CEO, low/no promoter)

Used by the `research-founder` skill so Claude can pick the right rubric
for the company in question. The heuristic below is the default when the
skill doesn't override it with its own reasoning.

`LeaderMode` is a `StrEnum` — instances compare equal to their string
value so JSON serialization and dict-key usage keep working, but
callers get typo-safety and autocomplete. Module-level constants
(`FIRST_GEN`, …) are re-exported for backwards compatibility.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum


class LeaderMode(StrEnum):
    FIRST_GEN = "FIRST_GEN"
    MULTI_GEN_FAMILY = "MULTI_GEN_FAMILY"
    PROFESSIONAL = "PROFESSIONAL"


# Module-level aliases (used by tests + judgment JSON values). Keep
# these so callers that compare against raw strings still pass.
FIRST_GEN = LeaderMode.FIRST_GEN
MULTI_GEN_FAMILY = LeaderMode.MULTI_GEN_FAMILY
PROFESSIONAL = LeaderMode.PROFESSIONAL

VALID_MODES: frozenset[LeaderMode] = frozenset(LeaderMode)

FIRST_GEN_MAX_AGE_YEARS = 10
MULTI_GEN_MIN_HOLDING_PCT = 25.0


def classify_mode(
    *,
    listed_year: int | None = None,
    promoter_holding_pct: float | None = None,
    is_founder_active: bool = False,
    generation: int = 1,
    has_family_promoter: bool = False,
) -> LeaderMode:
    """Return the `LeaderMode` enum for the given company profile."""
    current_year = date.today().year

    if listed_year is not None and is_founder_active and generation == 1:
        if current_year - listed_year < FIRST_GEN_MAX_AGE_YEARS:
            return LeaderMode.FIRST_GEN

    if (
        promoter_holding_pct is not None
        and promoter_holding_pct >= MULTI_GEN_MIN_HOLDING_PCT
        and (generation >= 2 or has_family_promoter)
    ):
        return LeaderMode.MULTI_GEN_FAMILY

    return LeaderMode.PROFESSIONAL

"""Classify who controls a listed company into a leadership mode.

Four modes, valid in any market:

    FOUNDER_LED        — the founder still runs or controls the company,
                         however long it has been listed (NVIDIA, Meta,
                         BYD, a recent Indian IPO).
    FAMILY_CONTROLLED  — a family / promoter group controls it: 2nd+
                         generation, or a family bloc holding >= 25%
                         (Indian promoter groups, Korean chaebol, Hong
                         Kong tycoon families, European family firms).
    STATE_CONTROLLED   — a government or state entity controls it
                         (Chinese SOEs, Indian PSUs, Gulf state companies).
    PROFESSIONAL       — widely held with professional management
                         (Microsoft, Infosys, HDFC Bank).

Each mode has its own leadership rubric in the `/analyse-stock` and
`/research-founder` skills. `classify_mode` is the default heuristic
when the skill has the facts but no reason to override it.

Judgments written before 2026-11 used FIRST_GEN / MULTI_GEN_FAMILY;
`LeaderMode("FIRST_GEN")` still parses, to the mode that replaced it.
"""

from __future__ import annotations

from enum import StrEnum

FAMILY_CONTROL_MIN_HOLDING_PCT = 25.0


class LeaderMode(StrEnum):
    FOUNDER_LED = "FOUNDER_LED"
    FAMILY_CONTROLLED = "FAMILY_CONTROLLED"
    STATE_CONTROLLED = "STATE_CONTROLLED"
    PROFESSIONAL = "PROFESSIONAL"

    @classmethod
    def _missing_(cls, value: object) -> "LeaderMode | None":
        return _LEGACY_MODES.get(str(value).upper())


_LEGACY_MODES: dict[str, LeaderMode] = {
    "FIRST_GEN": LeaderMode.FOUNDER_LED,
    "MULTI_GEN_FAMILY": LeaderMode.FAMILY_CONTROLLED,
}

VALID_MODES: frozenset[LeaderMode] = frozenset(LeaderMode)


def classify_mode(
    *,
    founder_active: bool = False,
    state_controlled: bool = False,
    insider_holding_pct: float | None = None,
    generation: int = 1,
    has_family_control: bool = False,
) -> LeaderMode:
    """Return the `LeaderMode` for the given control facts.

    Precedence: state control beats everything (the state's agenda shapes
    capital allocation even with a founder in place), then an active
    first-generation founder, then family control.
    """
    if state_controlled:
        return LeaderMode.STATE_CONTROLLED
    if founder_active and generation == 1:
        return LeaderMode.FOUNDER_LED
    family_bloc = (
        insider_holding_pct is not None
        and insider_holding_pct >= FAMILY_CONTROL_MIN_HOLDING_PCT
    )
    if family_bloc and (generation >= 2 or has_family_control):
        return LeaderMode.FAMILY_CONTROLLED
    return LeaderMode.PROFESSIONAL

"""Shared scoring types: `Verdict`, `Pillar`, `Gate`, `ScorerResult`, `StockScore`.

Design notes:
  - `Verdict` and `Pillar` are `StrEnum`s. Each member IS a string (subclass
    of `str`) so JSON serialization, dict-key usage, and equality with
    raw string literals all keep working — but callers get IDE
    autocomplete, typo-safety, and a single canonical list of valid
    values. No more `if verdict == "STRONG_BYU":` typos that silently
    evaluate to False.
  - `StockScore.evidence` is typed as `dict[Pillar, list[str]]` but
    accepts string keys transparently because StrEnum members are strs.
  - `verdict_for(composite)` returns a `Verdict` (not a bare string) so
    downstream call sites can iterate / compare against the enum.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum


# ── Enums (StrEnum = str subclass, backwards-compat with string literals) ────


class Verdict(StrEnum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    AVOID = "AVOID"
    RED_FLAG = "RED_FLAG"


class Pillar(StrEnum):
    FUNDAMENTALS = "fundamentals"
    VALUATION = "valuation"
    LEADERSHIP = "leadership"
    PROSPECTS = "prospects"
    NEWS = "news"


class Gate(StrEnum):
    """Composite-level caps applied after the weighted pillar blend."""

    INTEGRITY_VETO = "integrity_veto"


# ── Verdict banding ──────────────────────────────────────────────────────────

VERDICT_BANDS: tuple[tuple[float, Verdict], ...] = (
    (80, Verdict.STRONG_BUY),
    (70, Verdict.BUY),
    (55, Verdict.HOLD),
    (40, Verdict.AVOID),
    (0, Verdict.RED_FLAG),
)


def verdict_for(composite: float) -> Verdict:
    """Map a 0-100 composite score to its Verdict band."""
    for threshold, v in VERDICT_BANDS:
        if composite >= threshold:
            return v
    return Verdict.RED_FLAG


# ── Scorer output types ─────────────────────────────────────────────────────


@dataclass(slots=True)
class ScorerResult:
    """One pillar's output. The composite is built by blending four of these."""

    pillar: Pillar
    score: float
    confidence: float
    evidence: list[str] = field(default_factory=list)
    data_completeness_pct: float = 100.0

    @property
    def low_confidence(self) -> bool:
        return self.data_completeness_pct < 60.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"low_confidence": self.low_confidence}


@dataclass(slots=True)
class StockScore:
    """Final blended score across all five pillars."""

    ticker: str
    leadership_score: float
    fundamentals_score: float
    prospects_score: float
    news_score: float
    composite: float
    verdict: Verdict
    confidence: float
    data_completeness_pct: float
    valuation_score: float = 50.0  # neutral for rows cached before the pillar existed
    evidence: dict[Pillar, list[str]] = field(default_factory=dict)
    last_updated: str = ""
    low_confidence: bool = False
    # Composite-level caps that fired after the weighted blend.
    gates_applied: list[Gate] = field(default_factory=list)
    # Pillars sitting at the neutral prior because nothing could be scored:
    # a Claude judgment not written yet (or expired), a valuation with no
    # usable price ratio. Their 50 is a placeholder, not a verdict.
    unscored: list[Pillar] = field(default_factory=list)
    # Share of the fundamentals rubric that had data behind it. A score built
    # on a sliver of the rubric is not comparable with one built on all of it.
    fundamentals_coverage_pct: float = 100.0

    def __post_init__(self) -> None:
        if not self.last_updated:
            self.last_updated = datetime.now().isoformat(timespec="seconds")
        self.low_confidence = self.data_completeness_pct < 60.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

"""`ExtendedScore` — the typed row shape persisted to `scores.json`.

This module is the **single serialization boundary** between typed
domain objects (StockScore + BalanceSheetSnapshot) and the flat JSON
row format the cache stores. Every consumer of `scores.json` goes
through `ExtendedScore.from_dict` / `to_dict` — there is no "just use
`.get(...)`" path allowed anywhere else in the codebase.

Why this exists: before this class, every caller that loaded
`scores.json` invented its own "what's the default if the key is
missing" logic with `entry.get("composite") or 0` all over the place.
That's a latent bug factory — typos silently return the default
instead of raising. Now the conversion happens once, in one place,
and downstream code works with typed `ExtendedScore` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import Pillar, StockScore, Verdict

if TYPE_CHECKING:
    from fundamental_engine.analyse_pipeline import AnalysisResult


@dataclass(slots=True)
class ExtendedScore:
    """StockScore + the snapshot metadata pick / brief need to run.

    Stored as one row per ticker in `data_cache/scores.json`. Callers
    that need to iterate the cached universe should go through
    `fundamental_engine.universe.load_scores()` which returns typed
    `ExtendedScore` objects — never raw dicts.
    """

    score: StockScore
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    current_price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap_cr: float | None = None
    source: str = "unknown"

    # ── Constructors ────────────────────────────────────────────────────────

    @classmethod
    def from_analysis(cls, result: "AnalysisResult") -> "ExtendedScore":
        """Wrap the output of `analyse_pipeline.analyse_ticker` for caching."""
        snap = result.snapshot
        return cls(
            score=result.score,
            company_name=snap.company_name,
            sector=snap.sector,
            industry=snap.industry,
            current_price=snap.current_price,
            high_52w=snap.high_52w,
            low_52w=snap.low_52w,
            market_cap_cr=snap.market_cap_cr,
            source=snap.source,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtendedScore | None":
        """Parse a row from `scores.json`. Returns None if it's unusable.

        "Unusable" means the row lacks the minimum StockScore fields
        (`composite`, `verdict`). Callers skip these silently — a row
        that can't be parsed almost always means a partially-written
        cache entry from an aborted refresh.
        """
        if "composite" not in data or "verdict" not in data:
            return None
        try:
            verdict = Verdict(data["verdict"])
        except ValueError:
            return None
        try:
            evidence_raw = data.get("evidence") or {}
            evidence: dict[Pillar, list[str]] = {}
            for key, bullets in evidence_raw.items():
                try:
                    pillar = Pillar(key)
                except ValueError:
                    continue
                evidence[pillar] = list(bullets or [])

            score = StockScore(
                ticker=str(data.get("ticker", "")),
                leadership_score=float(data.get("leadership_score", 50)),
                fundamentals_score=float(data.get("fundamentals_score", 50)),
                prospects_score=float(data.get("prospects_score", 50)),
                news_score=float(data.get("news_score", 50)),
                composite=float(data["composite"]),
                verdict=verdict,
                confidence=float(data.get("confidence", 0.5)),
                data_completeness_pct=float(data.get("data_completeness_pct", 50)),
                evidence=evidence,
                last_updated=str(data.get("last_updated", "")),
            )
        except (KeyError, ValueError, TypeError):
            return None

        return cls(
            score=score,
            company_name=str(data.get("company_name") or ""),
            sector=str(data.get("sector") or ""),
            industry=str(data.get("industry") or ""),
            current_price=_optional_float(data.get("current_price")),
            high_52w=_optional_float(data.get("high_52w")),
            low_52w=_optional_float(data.get("low_52w")),
            market_cap_cr=_optional_float(data.get("market_cap_cr")),
            source=str(data.get("source") or "unknown"),
        )

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Flatten to the `scores.json` row shape.

        StockScore fields live at the top level (composite, verdict,
        confidence, evidence, …) so the JSON is a flat dict, not a
        nested `{"score": {...}, "company_name": ...}` structure. The
        flatness is a historical decision we preserve for backwards
        compatibility with existing cache files.
        """
        d = self.score.to_dict()
        d.update(
            {
                "company_name": self.company_name,
                "sector": self.sector,
                "industry": self.industry,
                "current_price": self.current_price,
                "high_52w": self.high_52w,
                "low_52w": self.low_52w,
                "market_cap_cr": self.market_cap_cr,
                "source": self.source,
            }
        )
        return d


def _optional_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

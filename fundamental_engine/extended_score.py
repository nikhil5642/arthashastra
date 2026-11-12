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
from typing import TYPE_CHECKING

from scorers.base import Gate, Pillar, StockScore, Verdict
from scorers.early_signals import EarlySignal
from scorers.risk_profile import RiskProfile, risk_profile

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
    market_cap: float | None = None  # absolute, in the major unit of `currency`
    source: str = "unknown"
    exchange: str = ""
    country: str = ""
    currency: str = ""  # trading currency as Yahoo reports it ("USD", "GBp", …)
    # Units of the base currency per one unit of `currency` at refresh time;
    # None when no FX quote was available.
    fx_rate_to_base: float | None = None
    # Latest quarter vs the same quarter a year ago — lets `pick` spot
    # "earnings still growing while the price fell" without a re-fetch.
    latest_yoy_revenue_growth: float | None = None
    latest_yoy_pat_growth: float | None = None
    # Concerns and strengths found at refresh time — `brief`, `pick` and
    # `screen` show them without a re-fetch, and the next refresh diffs
    # against them to say what is new.
    early_signals: list[EarlySignal] = field(default_factory=list)
    # How risky the company is to hold, as far as it can be measured:
    # price swings, liquidity, the length and steadiness of its record.
    risk: RiskProfile = field(default_factory=RiskProfile)
    # Asked about by name (`analyse`, `refresh`), as against only met in a
    # screen or a comparison. `brief`, `refresh all` and the backtests work
    # on the followed companies; a screen can score hundreds in passing.
    followed: bool = True

    # ── Constructors ────────────────────────────────────────────────────────

    @classmethod
    def from_analysis(
        cls, result: "AnalysisResult", *, fx_rate_to_base: float | None = None
    ) -> "ExtendedScore":
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
            market_cap=snap.market_cap,
            source=snap.source,
            exchange=snap.exchange,
            country=snap.country,
            currency=snap.currency,
            fx_rate_to_base=fx_rate_to_base,
            latest_yoy_revenue_growth=snap.latest_yoy_revenue_growth,
            latest_yoy_pat_growth=snap.latest_yoy_pat_growth,
            early_signals=list(result.early_signals),
            risk=risk_profile(snap),
        )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtendedScore | None":
        """Parse a row from `scores.json`. Returns None if it's unusable.

        "Unusable" means the row lacks the minimum StockScore fields
        (`composite`, `verdict`). Callers skip these silently — a row
        that can't be parsed almost always means a partially-written
        cache entry from an aborted refresh.
        """
        if "composite" not in data or "verdict" not in data:
            return None
        verdict_raw = data["verdict"]
        if not isinstance(verdict_raw, str):
            return None
        try:
            verdict = Verdict(verdict_raw)
        except ValueError:
            return None

        try:
            score = StockScore(
                ticker=_as_str(data.get("ticker"), default=""),
                leadership_score=_as_float(data.get("leadership_score"), default=50.0),
                fundamentals_score=_as_float(
                    data.get("fundamentals_score"), default=50.0
                ),
                valuation_score=_as_float(data.get("valuation_score"), default=50.0),
                prospects_score=_as_float(data.get("prospects_score"), default=50.0),
                news_score=_as_float(data.get("news_score"), default=50.0),
                composite=_as_float(data["composite"], default=0.0),
                verdict=verdict,
                confidence=_as_float(data.get("confidence"), default=0.5),
                data_completeness_pct=_as_float(
                    data.get("data_completeness_pct"), default=50.0
                ),
                evidence=_parse_evidence(data.get("evidence")),
                last_updated=_as_str(data.get("last_updated"), default=""),
                gates_applied=_parse_gates(data.get("gates_applied")),
                unscored=_parse_pillars(data.get("unscored")),
                fundamentals_coverage_pct=_as_float(
                    data.get("fundamentals_coverage_pct"), default=100.0
                ),
            )
        except (KeyError, ValueError, TypeError):
            return None

        return cls(
            score=score,
            company_name=_as_str(data.get("company_name"), default=""),
            sector=_as_str(data.get("sector"), default=""),
            industry=_as_str(data.get("industry"), default=""),
            current_price=_optional_float(data.get("current_price")),
            high_52w=_optional_float(data.get("high_52w")),
            low_52w=_optional_float(data.get("low_52w")),
            market_cap=_market_cap(data),
            source=_as_str(data.get("source"), default="unknown"),
            exchange=_as_str(data.get("exchange"), default=""),
            country=_as_str(data.get("country"), default=""),
            currency=_as_str(data.get("currency"), default=""),
            fx_rate_to_base=_optional_float(data.get("fx_rate_to_base")),
            latest_yoy_revenue_growth=_optional_float(
                data.get("latest_yoy_revenue_growth")
            ),
            latest_yoy_pat_growth=_optional_float(data.get("latest_yoy_pat_growth")),
            early_signals=_parse_early_signals(data.get("early_signals")),
            risk=RiskProfile.from_dict(data.get("risk")),
            # Rows written before the field existed were all asked for by name.
            followed=data.get("followed") is not False,
        )

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
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
                "market_cap": self.market_cap,
                "source": self.source,
                "exchange": self.exchange,
                "country": self.country,
                "currency": self.currency,
                "fx_rate_to_base": self.fx_rate_to_base,
                "latest_yoy_revenue_growth": self.latest_yoy_revenue_growth,
                "latest_yoy_pat_growth": self.latest_yoy_pat_growth,
                "early_signals": [signal.to_dict() for signal in self.early_signals],
                "risk": self.risk.to_dict(),
                "followed": self.followed,
            }
        )
        return d


def _market_cap(data: dict[str, object]) -> float | None:
    """Absolute market cap; rows cached before 2026-11 stored ₹ crore."""
    absolute = _optional_float(data.get("market_cap"))
    if absolute is not None:
        return absolute
    legacy_crore = _optional_float(data.get("market_cap_cr"))
    return legacy_crore * 1e7 if legacy_crore is not None else None


def _optional_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _as_float(v: object, *, default: float) -> float:
    """Coerce an untyped JSON value to a float or return the default."""
    parsed = _optional_float(v)
    return parsed if parsed is not None else default


def _as_str(v: object, *, default: str) -> str:
    """Coerce an untyped JSON value to a string or return the default."""
    if v is None:
        return default
    if isinstance(v, str):
        return v or default
    return str(v)


def _parse_early_signals(raw: object) -> list[EarlySignal]:
    """A JSON list of early-signal rows; rows that don't fit are skipped, not fatal."""
    if not isinstance(raw, list):
        return []
    parsed = (EarlySignal.from_dict(row) for row in raw if isinstance(row, dict))
    return [signal for signal in parsed if signal is not None]


def _parse_gates(raw: object) -> list[Gate]:
    """A JSON list of gate names; unknown names are skipped, not fatal."""
    if not isinstance(raw, list):
        return []
    gates: list[Gate] = []
    for item in raw:
        try:
            gates.append(Gate(str(item)))
        except ValueError:
            continue
    return gates


def _parse_pillars(raw: object) -> list[Pillar]:
    """A JSON list of pillar names; unknown names are skipped, not fatal."""
    if not isinstance(raw, list):
        return []
    pillars: list[Pillar] = []
    for item in raw:
        try:
            pillars.append(Pillar(str(item)))
        except ValueError:
            continue
    return pillars


def _parse_evidence(raw: object) -> dict[Pillar, list[str]]:
    """Parse the {pillar: [bullets]} shape from JSON, skipping unknown keys."""
    out: dict[Pillar, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, bullets in raw.items():
        if not isinstance(key, str):
            continue
        try:
            pillar = Pillar(key)
        except ValueError:
            continue
        if not isinstance(bullets, list):
            out[pillar] = []
            continue
        out[pillar] = [str(b) for b in bullets]
    return out

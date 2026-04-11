"""`DipCandidateRecord` — the typed row shape persisted to `dip_candidates.json`.

Wraps a `DipSignal` (price/technical fields) with the human-readable
metadata the `brief` command needs (company name, sector) so the
caller doesn't have to cross-reference `scores.json` every time it
wants to render a candidate row.

Like `ExtendedScore`, this is a single serialization boundary: every
consumer of `dip_candidates.json` goes through `from_dict` / `to_dict`
and works with typed objects from there on.
"""

from __future__ import annotations

from dataclasses import dataclass

from screener.dip_detector import DipSignal
from signals.signal_bus import Severity


@dataclass(slots=True)
class DipCandidateRecord:
    """One row of `dip_candidates.json`.

    `dip` carries every price/technical field (drawdown, RSI, 52w
    range, reason, severity); `name` and `sector` are the display
    metadata pulled from the ExtendedScore at the time the candidate
    qualified.
    """

    ticker: str
    name: str
    sector: str
    dip: DipSignal

    # ── Constructors ────────────────────────────────────────────────────────

    @classmethod
    def from_dip(
        cls,
        *,
        ticker: str,
        name: str,
        sector: str,
        dip: DipSignal,
    ) -> "DipCandidateRecord":
        return cls(ticker=ticker, name=name, sector=sector, dip=dip)

    @classmethod
    def from_dict(
        cls, ticker: str, data: dict[str, object]
    ) -> "DipCandidateRecord | None":
        """Reconstruct a record from a row of `dip_candidates.json`.

        Returns None for rows that are obviously broken (missing the
        `drawdown_pct` / `severity` / `composite_score` fields that
        make a DipSignal meaningful).
        """
        required = {"drawdown_pct", "severity", "composite_score"}
        if not required.issubset(data.keys()):
            return None

        severity = _parse_severity(data.get("severity"))
        if severity is None:
            return None

        try:
            dip = DipSignal(
                ticker=_as_str(data.get("ticker"), default=ticker),
                current_price=_as_float(data.get("current_price"), default=0.0),
                high_52w=_as_float(data.get("high_52w"), default=0.0),
                drawdown_pct=_as_float(data["drawdown_pct"], default=0.0),
                below_50dma=bool(data.get("below_50dma", False)),
                rsi14=_as_float(data.get("rsi14"), default=0.0),
                composite_score=_as_float(data["composite_score"], default=0.0),
                divergence_bonus=_as_int(data.get("divergence_bonus"), default=0),
                reason=_as_str(data.get("reason"), default=""),
                severity=severity,
                triggered_at=_as_str(data.get("triggered_at"), default=""),
            )
        except (KeyError, ValueError, TypeError):
            return None

        name_field = data.get("name")
        if not isinstance(name_field, str) or not name_field:
            company_name = data.get("company_name")
            if isinstance(company_name, str) and company_name:
                name_field = company_name
            else:
                name_field = ticker

        sector_field = data.get("sector")
        if not isinstance(sector_field, str) or not sector_field:
            sector_field = "Unknown"

        return cls(
            ticker=ticker,
            name=name_field,
            sector=sector_field,
            dip=dip,
        )

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, object]:
        """Flat dict for `dip_candidates.json`. DipSignal fields at top level."""
        d: dict[str, object] = self.dip.to_dict()
        d["ticker"] = self.ticker
        d["name"] = self.name
        d["sector"] = self.sector
        return d


# ── Untyped-JSON coercion helpers ────────────────────────────────────────────


def _parse_severity(raw: object) -> Severity | None:
    if isinstance(raw, Severity):
        return raw
    if isinstance(raw, str):
        try:
            return Severity(raw)
        except ValueError:
            return None
    return None


def _as_str(v: object, *, default: str) -> str:
    if v is None:
        return default
    if isinstance(v, str):
        return v or default
    return str(v)


def _as_float(v: object, *, default: float) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return default
    return default


def _as_int(v: object, *, default: int) -> int:
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            return default
    return default

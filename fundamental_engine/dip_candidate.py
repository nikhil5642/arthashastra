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
from typing import Any

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
        cls, ticker: str, data: dict[str, Any]
    ) -> "DipCandidateRecord | None":
        """Reconstruct a record from a row of `dip_candidates.json`.

        Returns None for rows that are obviously broken (missing the
        `drawdown_pct` or `severity` fields that make a DipSignal
        meaningful).
        """
        required = {"drawdown_pct", "severity", "composite_score"}
        if not required.issubset(data.keys()):
            return None
        try:
            severity_raw = data["severity"]
            severity = (
                severity_raw
                if isinstance(severity_raw, Severity)
                else Severity(severity_raw)
            )
            dip = DipSignal(
                ticker=str(data.get("ticker") or ticker),
                current_price=float(data.get("current_price") or 0.0),
                high_52w=float(data.get("high_52w") or 0.0),
                drawdown_pct=float(data["drawdown_pct"]),
                below_50dma=bool(data.get("below_50dma", False)),
                rsi14=float(data.get("rsi14") or 0.0),
                composite_score=float(data["composite_score"]),
                divergence_bonus=int(data.get("divergence_bonus") or 0),
                reason=str(data.get("reason") or ""),
                severity=severity,
                triggered_at=str(data.get("triggered_at") or ""),
            )
        except (KeyError, ValueError, TypeError):
            return None

        return cls(
            ticker=ticker,
            name=str(data.get("name") or data.get("company_name") or ticker),
            sector=str(data.get("sector") or "Unknown"),
            dip=dip,
        )

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Flat dict for `dip_candidates.json`. DipSignal fields at top level."""
        d = self.dip.to_dict()
        d["ticker"] = self.ticker
        d["name"] = self.name
        d["sector"] = self.sector
        return d

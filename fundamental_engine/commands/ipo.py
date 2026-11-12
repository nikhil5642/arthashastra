"""`ipo` — a company that is preparing to list: how far along it is, and its numbers.

    ipo <NAME…> [--price P] [--json]

Two halves, like every qualitative question in this project:

  - **Python finds the registration.** `data_sources.ipo_source` asks SEC
    EDGAR who has a registration statement on file under that name, and
    reads the filing list: stage (draft / filed / priced / trading), the
    reserved ticker, the newest prospectus, every amendment.
  - **Claude reads the prospectus.** `/analyse-ipo` transcribes its annual
    statements into `data_cache/ipos/<slug>.json`. When that file exists,
    this command scores it on the same rubric as any listed company and
    prints the ordinary stock report, labelled as transcribed.

Nothing found is not "not filing": most IPOs start as confidential drafts,
which are invisible until the company files publicly, and a listing on an
exchange outside the US is not an SEC registration at all.

An unlisted company is not in the universe: nothing here writes to
`scores.json` or the signal bus.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_sources import ipo_source
from data_sources.fundamentals_source import BalanceSheetSnapshot
from data_sources.fundamentals_source.prospectus import (
    ProspectusStatements,
    missing_lines,
    snapshot_from_prospectus,
)
from data_sources.ipo_source import IpoRegistration, IpoStage, Registrant
from fundamental_engine.analyse_pipeline import AnalysisResult, NoDataError, score_snapshot
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.ticker import slug
from reports.formatting import format_money, format_price, make_console
from reports.stock_report import render_stock_report
from scorers.base import Pillar
from scorers.risk_profile import risk_discount, risk_profile

IPOS_DIR = "ipos"

_STAGE_WORDS: dict[IpoStage, str] = {
    IpoStage.DRAFT: "confidential draft only — no public prospectus yet",
    IpoStage.FILED: "filed publicly — a prospectus is on file, not priced yet",
    IpoStage.PRICED: "priced — the final prospectus is filed; trading usually starts within days",
    IpoStage.TRADING: "already trading",
    IpoStage.WITHDRAWN: "registration withdrawn — no offering is on file now",
}


@dataclass(slots=True)
class _Lookup:
    """What SEC knows about the name, and the transcribed statements if Claude wrote them."""

    query: str
    registrants: list[Registrant]
    registration: IpoRegistration | None
    statements_path: Path  # where the statements are, or where /analyse-ipo should write them
    statements: ProspectusStatements | None
    statements_unreadable: bool = False
    sec_answered: bool = True  # False: the search failed, so "nothing found" can't be claimed


def run_ipo_command(name: str, *, offer_price: float | None = None, as_json: bool = False) -> int:
    query = name.strip()
    if not query:
        print("[ipo] Name a company: ipo anthropic")
        return 2
    cfg = AnalystConfig.from_env()
    lookup = _look_up(query, cfg)
    analysed, scoring_error = _score(lookup, cfg.data_cache_dir, offer_price)

    if as_json:
        print(json.dumps(_payload(lookup, analysed, offer_price), indent=2))
        return 0

    console = make_console()
    _render_registration(console, lookup)
    _render_statements(console, lookup, analysed, offer_price, scoring_error)
    return 0


# ── Lookup ───────────────────────────────────────────────────────────────────


def _look_up(query: str, cfg: AnalystConfig) -> _Lookup:
    async def fetch() -> tuple[list[Registrant] | None, IpoRegistration | None]:
        found = await ipo_source.find_registrants(query, user_agent=cfg.sec_user_agent)
        if not found:
            return found, None
        return found, await ipo_source.fetch_registration(found[0].cik, user_agent=cfg.sec_user_agent)

    answer, registration = asyncio.run(fetch())
    registrants = answer or []
    registered_name = (
        registration.name if registration else registrants[0].name if registrants else ""
    )
    path = _statements_path(cfg.data_cache_dir, registered_name, query)
    raw = JsonStore(path).read() if path.exists() else {}
    statements = ProspectusStatements.from_dict(raw) if raw else None
    return _Lookup(
        query=query,
        registrants=registrants,
        registration=registration,
        statements_path=path,
        statements=statements,
        statements_unreadable=path.exists() and statements is None,
        sec_answered=answer is not None,
    )


def _statements_path(cache_dir: Path, registered_name: str, query: str) -> Path:
    """The registrant's own name first, then the name as asked; the first that exists wins."""
    folder = cache_dir / IPOS_DIR
    candidates = [folder / f"{slug(n)}.json" for n in (registered_name, query) if slug(n)]
    return next((path for path in candidates if path.exists()), candidates[0])


def _score(
    lookup: _Lookup, cache_dir: Path, offer_price: float | None
) -> tuple[AnalysisResult | None, str]:
    """The transcribed statements scored like any listed company; an error line when they can't be."""
    if lookup.statements is None:
        return None, ""
    snap = snapshot_from_prospectus(lookup.statements, as_of=date.today(), offer_price=offer_price)
    if snap is None:
        missing = ", ".join(row.value for row in missing_lines(lookup.statements))
        return None, f"no ratio can be worked out from it — the newest fiscal year lacks {missing}"
    try:
        return score_snapshot(snap, data_cache_dir=cache_dir), ""
    except NoDataError as e:
        return None, str(e)


# ── Terminal output ──────────────────────────────────────────────────────────


def _render_registration(console: Console, lookup: _Lookup) -> None:
    registration = lookup.registration
    if registration is None:
        if lookup.registrants:
            first = lookup.registrants[0]
            console.print(
                f"[yellow]SEC lists {first.name} (CIK {first.cik}) as a registrant, but did not "
                f"return its filing list — try again later.[/yellow]"
            )
        elif not lookup.sec_answered:
            console.print(
                f"[yellow]SEC did not answer the search for {lookup.query} — nothing can be "
                f"said about whether it has filed. Try again in a minute.[/yellow]"
            )
        else:
            console.print(
                Panel(
                    f"SEC has no public registration statement (S-1 / F-1) under "
                    f"[bold]{lookup.query}[/bold].\n\n"
                    "That does NOT mean the company isn't preparing one: most IPOs start as a "
                    "confidential draft, which SEC does not show until the company files "
                    "publicly, and a listing on an exchange outside the US is not an SEC "
                    "registration at all.",
                    title="No public registration found",
                    title_align="left",
                    border_style="yellow",
                )
            )
        _render_other_matches(console, lookup.registrants[1:])
        return

    stage = registration.stage
    first, latest = registration.first_public_filing, registration.latest_prospectus
    lines = [
        f"[bold]{registration.name}[/bold]  (SEC CIK {registration.cik})",
        f"Stage:              {_STAGE_WORDS[stage]}",
        f"Ticker:             {registration.proposed_ticker or '— none reserved yet'}"
        + (f" on {registration.exchange}" if registration.exchange else ""),
        f"SEC industry:       {registration.industry or '—'}",
        f"First filed public: {first.filed.isoformat() if first else '—'}",
        f"Amendments:         {registration.amendments}",
        f"Latest prospectus:  {latest.url if latest else '—'}"
        + (f"  ({latest.form}, {latest.filed.isoformat()})" if latest else ""),
    ]
    if stage is IpoStage.TRADING:
        ticker = registration.proposed_ticker
        lines.append(
            f"\n[bold]It already trades.[/bold] Analyse it as a listed company: "
            f"`python3 -m fundamental_engine analyse {ticker or '<ticker>'}`."
        )
    console.print(Panel("\n".join(lines), title="IPO registration", title_align="left"))

    if registration.filings:
        table = Table(title="Filing timeline (newest first)")
        for column in ("Filed", "Form", "Document"):
            table.add_column(column)
        for filing in registration.filings:
            table.add_row(filing.filed.isoformat(), filing.form, f"[dim]{filing.url}[/dim]")
        console.print(table)
    _render_other_matches(console, lookup.registrants[1:])


def _render_other_matches(console: Console, others: list[Registrant]) -> None:
    if others:
        names = ", ".join(
            f"{r.name}" + (f" ({r.proposed_ticker})" if r.proposed_ticker else "") for r in others
        )
        console.print(f"[dim]Other registrants matching the name: {names}[/dim]")


def _render_statements(
    console: Console,
    lookup: _Lookup,
    analysed: AnalysisResult | None,
    offer_price: float | None,
    scoring_error: str,
) -> None:
    if lookup.statements is None:
        if lookup.statements_unreadable:
            console.print(
                f"[red]{lookup.statements_path} exists but could not be read as prospectus "
                f"statements (it needs a company, a currency and at least one dated fiscal "
                f"year). Fix or rewrite it with /analyse-ipo.[/red]"
            )
        elif lookup.registration is None or lookup.registration.stage is not IpoStage.TRADING:
            console.print(
                f"\nNo transcribed statements yet. `/analyse-ipo {lookup.query}` reads the "
                f"prospectus and writes them to {lookup.statements_path}."
            )
        return
    if analysed is None:
        console.print(f"[red]The statements in {lookup.statements_path} can't be scored: "
                      f"{scoring_error}.[/red]")
        return

    console.print(_transcription_note(lookup.statements, analysed.snapshot, offer_price))
    render_stock_report(analysed.snapshot, analysed.score, early_signals=analysed.early_signals)
    if analysed.judgment is None:
        console.print(
            f"\n[hint] Leadership / prospects / news await Claude's judgment: "
            f"`/analyse-ipo {lookup.query}` writes it to "
            f"data_cache/judgments/{analysed.snapshot.ticker}.json."
        )


def _transcription_note(
    statements: ProspectusStatements, snap: BalanceSheetSnapshot, offer_price: float | None
) -> Panel:
    lines = [
        "[bold]These figures were transcribed from the prospectus by Claude, not fetched "
        "from a data feed.[/bold] Check them against the filing before relying on them.",
        f"Source:          {statements.source_url or '— not recorded'}",
        f"Transcribed at:  {statements.transcribed_at or '— not recorded'}",
    ]
    lines += [f"Note:            {note}" for note in statements.notes]
    lines += [
        "",
        "The company has no trading history: price volatility, worst fall, P/E against its "
        "own past and the newest-quarter signals are UNKNOWN — not clean.",
        _valuation_basis(statements, snap, offer_price),
    ]
    if snap.rev_3y_cagr is None and snap.pat_3y_cagr is None:
        lines.append(
            f"Growth is NOT scored: a growth rate needs at least three fiscal years with a "
            f"positive starting figure, and the file has {len(statements.fiscal_years)} years. "
            f"The growth veto is silent here, not passed."
        )
    return Panel("\n".join(lines), title="Prospectus figures", title_align="left",
                 border_style="yellow")


def _valuation_basis(
    statements: ProspectusStatements, snap: BalanceSheetSnapshot, offer_price: float | None
) -> str:
    offering, currency = statements.offering, statements.currency
    if snap.market_cap is None:
        if offer_price is None and offering.price is None:
            return "Valuation: none — the offering terms (price range) aren't set yet."
        return ("Valuation: none — the share count after the offering isn't in the statements "
                "file, so there is no market value to price the earnings against.")
    cap = format_money(snap.market_cap, currency)
    if offer_price is not None:
        return f"Valuation: at --price {format_price(offer_price, currency)} (market value {cap})."
    low, high = offering.price_low, offering.price_high
    if low is not None and high is not None and low != high:
        return (f"Valuation: at the midpoint of the {format_price(low, currency)}–"
                f"{format_price(high, currency)} range (market value {cap}).")
    return f"Valuation: at the offer price {format_price(offering.price, currency)} (market value {cap})."


# ── JSON ─────────────────────────────────────────────────────────────────────


def _payload(
    lookup: _Lookup, analysed: AnalysisResult | None, offer_price: float | None
) -> dict[str, object]:
    found = lookup.statements is not None
    return {
        "query": lookup.query,
        "sec_answered": lookup.sec_answered,
        "registration": lookup.registration.to_dict() if lookup.registration else None,
        "other_matches": [r.to_dict() for r in lookup.registrants[1:]],
        "statements_file": str(lookup.statements_path) if found else None,
        "write_statements_to": str(lookup.statements_path),
        "scored": _scored(lookup.statements, analysed, offer_price) if analysed else None,
    }


def _scored(
    statements: ProspectusStatements | None, analysed: AnalysisResult, offer_price: float | None
) -> dict[str, object]:
    score, snap = analysed.score, analysed.snapshot
    profile = risk_profile(snap)
    price = offer_price if offer_price is not None else (
        statements.offering.price if statements else None
    )
    return {
        "ticker": snap.ticker,
        "fundamentals": score.fundamentals_score,
        "valuation": None if Pillar.VALUATION in score.unscored else score.valuation_score,
        "composite": score.composite,
        "verdict": score.verdict.value,
        "unscored": [pillar.value for pillar in score.unscored],
        "risk": profile.to_dict(),
        "risk_discount": risk_discount(profile),
        "early_signals": [signal.to_dict() for signal in analysed.early_signals],
        "market_cap": snap.market_cap,
        "offer_price": price if snap.market_cap is not None else None,
        "evidence": {pillar.value: list(lines) for pillar, lines in score.evidence.items()},
    }

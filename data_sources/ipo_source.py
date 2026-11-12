"""IPO registrations — who has filed to list, and how far along they are.

A company that has not listed yet has no Yahoo symbol, no price and no
statements feed. What it does have, once it files publicly, is a
registration statement: the prospectus (Form S-1, or F-1 for a foreign
issuer), its amendments, and at the end the priced prospectus (424B4).
SEC EDGAR publishes the filing list for every registrant, so this adapter
answers three questions from live data:

  - has this company filed to list?           `find_registrants(name)`
  - how far along is it?                      `IpoRegistration.stage`
  - which document is the newest prospectus?  `IpoRegistration.latest_prospectus`

It does not read the prospectus. A registrant's statements only become
machine-readable with its first quarterly report after listing; before
that the numbers are in the prospectus document, which Claude reads in
`/analyse-ipo` and transcribes into `ProspectusStatements`
(`data_sources.fundamentals_source.prospectus`).

Coverage is SEC registrants — US companies and foreign issuers listing in
the US. A listing on another exchange is not covered: `find_registrants`
returns nothing, and "nothing found" must never be read as "not filing".
A confidential draft (most IPOs start with one) is invisible here until
the company files publicly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

SEC_ENTITY_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
HTTP_TIMEOUT_SECONDS = 30.0

# The registration statement and its amendments; the search matches amendments too.
_REGISTRATION_FORMS = ("S-1", "F-1")
_DRAFT_FORMS = frozenset({"DRS", "DRS/A"})
_PROSPECTUS_FORMS = frozenset({"S-1", "S-1/A", "F-1", "F-1/A"})
_PRICED_FORMS = frozenset({"424B4", "424B1"})
_WITHDRAWAL_FORMS = frozenset({"RW"})  # the company asked SEC to withdraw the registration
# Reports only a company with listed shares files.
_LISTED_COMPANY_FORMS = frozenset({"10-Q", "10-K", "20-F", "6-K"})

_DISPLAY_NAME = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<ticker>[A-Z.\-]{1,6})\))?\s*\(CIK \d+\)\s*$")


class IpoStage(StrEnum):
    """How far a registrant is along the road to trading, read off its filing list."""

    DRAFT = "draft"  # only confidential drafts have been made public
    FILED = "filed"  # a public prospectus, no final price yet
    PRICED = "priced"  # the priced prospectus is filed; trading starts within days
    TRADING = "trading"  # SEC lists an exchange, or the company files listed-company reports
    WITHDRAWN = "withdrawn"  # the registration was withdrawn and nothing has been filed since


@dataclass(frozen=True, slots=True)
class Registrant:
    """A company with a registration statement on file — one search hit."""

    cik: int
    name: str
    proposed_ticker: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"cik": self.cik, "name": self.name, "proposed_ticker": self.proposed_ticker}


@dataclass(frozen=True, slots=True)
class IpoFiling:
    form: str
    filed: date
    url: str

    def to_dict(self) -> dict[str, object]:
        return {"form": self.form, "filed": self.filed.isoformat(), "url": self.url}


@dataclass(slots=True)
class IpoRegistration:
    """One registrant's offering filings, newest first."""

    cik: int
    name: str
    proposed_ticker: str = ""
    exchange: str = ""  # empty until SEC records a listing
    industry: str = ""  # SEC's SIC description
    fiscal_year_end: str = ""  # MMDD
    filings: list[IpoFiling] = field(default_factory=list)
    reports_as_listed: bool = False

    @property
    def current_filings(self) -> list[IpoFiling]:
        """The filings of the live attempt: everything since the last withdrawal, newest first."""
        current: list[IpoFiling] = []
        for filing in self.filings:
            if filing.form in _WITHDRAWAL_FORMS:
                break
            current.append(filing)
        return current

    @property
    def stage(self) -> IpoStage:
        forms = {filing.form for filing in self.current_filings}
        if self.exchange or self.reports_as_listed:
            return IpoStage.TRADING
        if not forms and self.filings:
            return IpoStage.WITHDRAWN
        if forms & _PRICED_FORMS:
            return IpoStage.PRICED
        if forms & _PROSPECTUS_FORMS:
            return IpoStage.FILED
        return IpoStage.DRAFT

    @property
    def latest_prospectus(self) -> IpoFiling | None:
        """The newest full prospectus: the priced one if filed, else the latest amendment."""
        wanted = _PRICED_FORMS | _PROSPECTUS_FORMS
        return next((filing for filing in self.current_filings if filing.form in wanted), None)

    @property
    def first_public_filing(self) -> IpoFiling | None:
        public = [filing for filing in self.current_filings if filing.form in _PROSPECTUS_FORMS]
        return public[-1] if public else None

    @property
    def amendments(self) -> int:
        return sum(1 for filing in self.current_filings if filing.form in ("S-1/A", "F-1/A"))

    def to_dict(self) -> dict[str, object]:
        latest, first = self.latest_prospectus, self.first_public_filing
        return {
            "cik": self.cik,
            "name": self.name,
            "proposed_ticker": self.proposed_ticker,
            "exchange": self.exchange,
            "industry": self.industry,
            "fiscal_year_end": self.fiscal_year_end,
            "stage": self.stage.value,
            "amendments": self.amendments,
            "first_public_filing": first.to_dict() if first else None,
            "latest_prospectus": latest.to_dict() if latest else None,
            "filings": [filing.to_dict() for filing in self.filings],
        }


# ── Public entry points ──────────────────────────────────────────────────────


async def find_registrants(name: str, *, user_agent: str) -> list[Registrant] | None:
    """Companies whose name matches and that have a registration statement on file.

    None when SEC did not answer — which is not the same as an empty list.
    """
    query = urlencode({"q": "", "entityName": name.strip(), "forms": ",".join(_REGISTRATION_FORMS)})
    payload = await _get_json(f"{SEC_ENTITY_SEARCH_URL}?{query}", user_agent)
    return None if payload is None else parse_registrants(payload)


async def fetch_registration(cik: int, *, user_agent: str) -> IpoRegistration | None:
    """The registrant's offering filings; None when SEC does not answer."""
    return parse_registration(await _get_json(SEC_SUBMISSIONS_URL.format(cik=cik), user_agent))


# ── Parsing (pure) ───────────────────────────────────────────────────────────


def parse_registrants(payload: object) -> list[Registrant]:
    """Distinct registrants in a search response, in the order SEC ranked them."""
    found: dict[int, Registrant] = {}
    for source in _search_sources(payload):
        ciks, names = source.get("ciks"), source.get("display_names")
        if not isinstance(ciks, list) or not isinstance(names, list):
            continue
        for raw_cik, display in zip(ciks, names):
            match = _DISPLAY_NAME.match(display) if isinstance(display, str) else None
            if match is None or not isinstance(raw_cik, str) or not raw_cik.isdigit():
                continue
            cik, ticker = int(raw_cik), match.group("ticker") or ""
            known = found.get(cik)
            # Later filings carry the ticker once the company has reserved one.
            if known is None or (ticker and not known.proposed_ticker):
                found[cik] = Registrant(cik, match.group("name").strip(), ticker)
    return list(found.values())


def parse_registration(payload: object) -> IpoRegistration | None:
    if not isinstance(payload, dict):
        return None
    cik, name = _as_int(payload.get("cik")), payload.get("name")
    if cik is None or not isinstance(name, str):
        return None
    forms, filed, accessions, documents = _recent_columns(payload)
    offering_forms = _DRAFT_FORMS | _PROSPECTUS_FORMS | _PRICED_FORMS | _WITHDRAWAL_FORMS
    filings = [
        IpoFiling(form, day, _document_url(cik, accession, document))
        for form, day, accession, document in zip(forms, filed, accessions, documents)
        if form in offering_forms and day is not None
    ]
    return IpoRegistration(
        cik=cik,
        name=name,
        proposed_ticker=_first_text(payload.get("tickers")),
        exchange=_first_text(payload.get("exchanges")),
        industry=_text(payload.get("sicDescription")),
        fiscal_year_end=_text(payload.get("fiscalYearEnd")),
        filings=sorted(filings, key=lambda filing: filing.filed, reverse=True),
        reports_as_listed=bool(_LISTED_COMPANY_FORMS & set(forms)),
    )


def _search_sources(payload: object) -> list[dict[str, object]]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    rows = hits.get("hits") if isinstance(hits, dict) else None
    if not isinstance(rows, list):
        return []
    sources = (row.get("_source") for row in rows if isinstance(row, dict))
    return [source for source in sources if isinstance(source, dict)]


def _recent_columns(
    payload: dict[str, object],
) -> tuple[list[str], list[date | None], list[str], list[str]]:
    """The parallel columns of SEC's recent-filings table."""
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        return [], [], [], []

    def column(key: str) -> list[str]:
        values = recent.get(key)
        return [_text(value) for value in values] if isinstance(values, list) else []

    return (
        column("form"),
        [_as_date(day) for day in column("filingDate")],
        column("accessionNumber"),
        column("primaryDocument"),
    )


def _document_url(cik: int, accession: str, document: str) -> str:
    return SEC_ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""), document=document)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_text(values: object) -> str:
    if not isinstance(values, list):
        return ""
    return next((text for text in map(_text, values) if text), "")


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _as_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ── Transport (tests monkeypatch `_get_json`) ────────────────────────────────


async def _get_json(url: str, user_agent: str) -> object | None:
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        log.warning("SEC request failed (%s): %s", url, e)
        return None
    if resp.status_code != 200:
        log.warning("SEC returned %s for %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except ValueError:
        return None

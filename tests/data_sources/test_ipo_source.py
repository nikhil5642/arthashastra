"""IPO-registration tests — SEC-shaped payloads built inline, the transport faked."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from data_sources import ipo_source
from data_sources.ipo_source import (
    IpoFiling,
    IpoRegistration,
    IpoStage,
    Registrant,
    parse_registrants,
    parse_registration,
)


def _hit(cik: str, display: str, form: str = "S-1/A") -> dict[str, object]:
    return {"_source": {"ciks": [cik], "display_names": [display], "form": form,
                        "file_date": "2026-11-10"}}


def _search(*hits: dict[str, object]) -> dict[str, object]:
    return {"hits": {"hits": list(hits)}}


def _submissions(
    filings: list[tuple[str, str]],
    *,
    tickers: list[object] | None = None,
    exchanges: list[object] | None = None,
) -> dict[str, object]:
    forms = [form for form, _ in filings]
    return {
        "cik": "2130357",
        "name": "Fictional Labs, PBC",
        "tickers": tickers if tickers is not None else ["FLAB"],
        "exchanges": exchanges if exchanges is not None else [None],
        "sicDescription": "Services-Prepackaged Software",
        "fiscalYearEnd": "1231",
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": [day for _, day in filings],
                "accessionNumber": [f"0001628280-26-0{i:05d}" for i in range(len(forms))],
                "primaryDocument": [f"doc{i}.htm" for i in range(len(forms))],
            }
        },
    }


# ── Search ───────────────────────────────────────────────────────────────────


def test_registrants_are_distinct_in_sec_order_and_pick_up_a_later_ticker():
    payload = _search(
        _hit("0002130357", "Fictional Labs, PBC  (CIK 0002130357)", "S-1"),
        _hit("0002130357", "Fictional Labs, PBC  (FLAB)  (CIK 0002130357)"),
        _hit("0000000042", "Fictional Labs Holdings Ltd  (CIK 0000000042)", "F-1"),
    )
    assert parse_registrants(payload) == [
        Registrant(2130357, "Fictional Labs, PBC", "FLAB"),
        Registrant(42, "Fictional Labs Holdings Ltd", ""),
    ]
    assert Registrant(42, "X").to_dict() == {"cik": 42, "name": "X", "proposed_ticker": ""}


@pytest.mark.parametrize(
    "payload",
    [None, [], "nope", {"hits": None}, {"hits": {"hits": "x"}}, {"hits": {"hits": [1, {"_source": 2}]}},
     _search({"_source": {"ciks": "1", "display_names": []}}),
     _search(_hit("abc", "Bad CIK  (CIK abc)")),
     _search({"_source": {"ciks": ["1"], "display_names": [7]}}),
     _search(_hit("1", "no cik suffix at all"))],
)
def test_garbage_search_payloads_find_nobody(payload: object):
    assert parse_registrants(payload) == []


# ── Filing list and stage ────────────────────────────────────────────────────


def test_a_public_prospectus_with_amendments_is_filed():
    registration = parse_registration(_submissions([
        ("S-1/A", "2026-11-10"), ("8-K", "2026-11-05"), ("S-1/A", "2026-11-02"),
        ("S-1", "2026-10-26"), ("DRS/A", "2026-10-09"), ("DRS", "2026-06-01"),
    ]))
    assert registration is not None
    assert registration.stage is IpoStage.FILED
    assert (registration.cik, registration.proposed_ticker, registration.exchange) == (2130357, "FLAB", "")
    assert registration.amendments == 2
    assert [f.form for f in registration.filings] == ["S-1/A", "S-1/A", "S-1", "DRS/A", "DRS"]  # no 8-K
    latest, first = registration.latest_prospectus, registration.first_public_filing
    assert latest is not None and (latest.form, latest.filed) == ("S-1/A", date(2026, 11, 10))
    assert latest.url == ("https://www.sec.gov/Archives/edgar/data/2130357/"
                          "000162828026000000/doc0.htm")
    assert first is not None and (first.form, first.filed) == ("S-1", date(2026, 10, 26))

    as_dict = registration.to_dict()
    assert as_dict["stage"] == "filed" and as_dict["amendments"] == 2
    assert as_dict["latest_prospectus"] == latest.to_dict()
    assert as_dict["first_public_filing"] == {"form": "S-1", "filed": "2026-10-26", "url": first.url}
    assert len(as_dict["filings"]) == 5  # type: ignore[arg-type]


def test_only_confidential_drafts_is_draft():
    registration = parse_registration(_submissions([("DRS/A", "2026-10-09"), ("DRS", "2026-06-01")], tickers=[]))
    assert registration is not None
    assert registration.stage is IpoStage.DRAFT
    assert registration.latest_prospectus is None and registration.first_public_filing is None
    assert registration.proposed_ticker == ""
    assert registration.to_dict()["latest_prospectus"] is None


def test_a_priced_prospectus_is_the_latest_one():
    registration = parse_registration(_submissions([("424B4", "2026-05-14"), ("S-1/A", "2026-05-11"), ("F-1", "2026-04-17")]))
    assert registration is not None and registration.stage is IpoStage.PRICED
    assert registration.latest_prospectus is not None and registration.latest_prospectus.form == "424B4"


@pytest.mark.parametrize(
    "filings, exchanges",
    [([("424B4", "2026-05-14")], ["Nasdaq"]),  # SEC records the exchange
     ([("10-Q", "2026-08-10"), ("424B4", "2026-05-14")], [None])],  # or it files as a listed company
)
def test_a_listed_company_is_trading(filings: list[tuple[str, str]], exchanges: list[object]):
    registration = parse_registration(_submissions(filings, exchanges=exchanges))
    assert registration is not None and registration.stage is IpoStage.TRADING


@pytest.mark.parametrize(
    "payload",
    [None, [], {"name": "No CIK"}, {"cik": "12", "name": 5}, {"cik": True, "name": "Bool"},
     {"cik": "x1", "name": "Letters"}],
)
def test_garbage_submissions_are_none(payload: object):
    assert parse_registration(payload) is None


def test_a_filing_list_with_holes_keeps_what_it_can():
    payload = _submissions([("S-1", "not a date"), ("S-1/A", "2026-11-02")])
    payload["cik"] = 2130357  # an int CIK is read too
    assert (registration := parse_registration(payload)) is not None
    assert [f.form for f in registration.filings] == ["S-1/A"]

    no_table = {"cik": "7", "name": "Bare Co", "filings": {"recent": "x"}, "tickers": "FLAB"}
    bare = parse_registration(no_table)
    assert bare is not None and bare.filings == [] and bare.stage is IpoStage.DRAFT
    assert bare.proposed_ticker == "" and bare.industry == ""

    ragged = _submissions([("S-1", "2026-10-26")])
    ragged["filings"]["recent"]["form"] = "S-1"  # type: ignore[index]
    assert parse_registration(ragged).filings == []  # type: ignore[union-attr]


def test_an_empty_registration_is_a_draft_with_nothing_to_show():
    empty = IpoRegistration(cik=1, name="Nobody")
    assert empty.stage is IpoStage.DRAFT and empty.amendments == 0
    assert IpoFiling("S-1", date(2026, 1, 2), "u").to_dict() == {"form": "S-1", "filed": "2026-01-02", "url": "u"}


# ── Entry points (transport faked) ───────────────────────────────────────────


def test_entry_points_ask_sec_with_the_given_user_agent(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_get_json(url: str, user_agent: str) -> object | None:
        calls.append((url, user_agent))
        if "search-index" in url:
            return _search(_hit("0002130357", "Fictional Labs, PBC  (FLAB)  (CIK 0002130357)"))
        return _submissions([("S-1", "2026-10-26")])

    monkeypatch.setattr(ipo_source, "_get_json", fake_get_json)
    found = asyncio.run(ipo_source.find_registrants("  fictional labs ", user_agent="research-tool/1.0"))
    assert found == [Registrant(2130357, "Fictional Labs, PBC", "FLAB")]
    registration = asyncio.run(ipo_source.fetch_registration(2130357, user_agent="research-tool/1.0"))
    assert registration is not None and registration.stage is IpoStage.FILED

    search_url, submissions_url = calls[0][0], calls[1][0]
    assert "entityName=fictional+labs" in search_url and "forms=S-1%2CF-1" in search_url
    assert submissions_url == "https://data.sec.gov/submissions/CIK0002130357.json"
    assert {agent for _, agent in calls} == {"research-tool/1.0"}


def test_an_unanswered_request_is_not_an_empty_answer(monkeypatch):
    async def no_answer(url: str, user_agent: str) -> object | None:
        return None

    monkeypatch.setattr(ipo_source, "_get_json", no_answer)
    assert asyncio.run(ipo_source.find_registrants("x", user_agent="ua")) is None
    assert asyncio.run(ipo_source.fetch_registration(1, user_agent="ua")) is None


# ── Transport (httpx faked) ──────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code, self._body = status_code, body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _fake_client(response: _Response | Exception):
    class Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> _Response:
            assert headers["User-Agent"] == "ua"
            if isinstance(response, Exception):
                raise response
            return response

    return Client


@pytest.mark.parametrize(
    "response, expected",
    [(_Response(200, {"ok": 1}), {"ok": 1}),
     (_Response(403, {"ok": 1}), None),
     (_Response(200, ValueError("not json")), None),
     (RuntimeError("offline"), None)],
)
def test_get_json_returns_the_body_or_none(monkeypatch, response, expected):
    monkeypatch.setattr(ipo_source.httpx, "AsyncClient", _fake_client(response))
    assert asyncio.run(ipo_source._get_json("https://example.test", "ua")) == expected


def test_a_withdrawn_attempt_does_not_date_the_one_that_followed():
    def registration(forms: list[str], days: list[str]) -> ipo_source.IpoRegistration:
        parsed = ipo_source.parse_registration({
            "cik": "42", "name": "Second Try Inc.", "tickers": [], "exchanges": [None],
            "filings": {"recent": {"form": forms, "filingDate": days,
                                   "accessionNumber": ["0000000042-26-000001"] * len(forms),
                                   "primaryDocument": ["doc.htm"] * len(forms)}},
        })
        assert parsed is not None
        return parsed

    refiled = registration(["S-1/A", "S-1", "RW", "S-1/A", "S-1"],
                           ["2026-04-20", "2026-04-01", "2025-06-01", "2024-11-01", "2024-09-30"])
    assert refiled.stage is ipo_source.IpoStage.FILED and refiled.amendments == 1
    assert refiled.first_public_filing is not None
    assert refiled.first_public_filing.filed.isoformat() == "2026-04-01"
    assert len(refiled.filings) == 5  # the whole timeline is still shown

    gone = registration(["RW", "S-1"], ["2025-06-01", "2024-09-30"])
    assert gone.stage is ipo_source.IpoStage.WITHDRAWN and gone.latest_prospectus is None

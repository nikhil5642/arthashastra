"""`ipo` tests — SEC lookups faked, a transcribed statements file written to tmp_path."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from data_sources import ipo_source
from data_sources.ipo_source import IpoFiling, IpoRegistration, Registrant
from fundamental_engine.commands.ipo import run_ipo_command
from fundamental_engine.judgments import Judgment, JudgmentStore, PillarJudgment

FILING_URL = "https://www.sec.gov/Archives/edgar/data/9/1/fictional-s1a.htm"


def _registration(*, exchange: str = "") -> IpoRegistration:
    return IpoRegistration(
        cik=9, name="Fictional Labs, PBC", proposed_ticker="FLAB", exchange=exchange,
        industry="Services-Prepackaged Software", fiscal_year_end="1231",
        filings=[
            IpoFiling("S-1/A", date(2026, 11, 10), FILING_URL),
            IpoFiling("S-1", date(2026, 10, 26), "https://www.sec.gov/first.htm"),
            IpoFiling("DRS", date(2026, 6, 1), "https://www.sec.gov/draft.htm"),
        ],
    )


def _statements(**offering: object) -> dict[str, object]:
    year = lambda end, revenue, profit: {  # noqa: E731
        "fiscal_year_end": end, "revenue": revenue, "operating_income": profit * 1.3,
        "interest_expense": 5, "net_income": profit, "operating_cash_flow": profit * 1.2,
        "capital_spend": 40, "total_assets": 2000, "current_assets": 900,
        "current_liabilities": 400, "stockholders_equity": 1200, "long_term_debt": 200,
    }
    return {
        "company": "Fictional Labs, PBC", "ticker": "FLAB", "currency": "USD",
        "amounts_in": 1_000_000, "source_url": FILING_URL,
        "transcribed_at": "2026-11-12T09:00:00", "notes": ["Balance sheet is pro forma."],
        "offering": offering,
        "fiscal_years": [year("2025-12-31", 1000, 150), year("2024-12-31", 800, 110),
                         year("2023-12-31", 600, 70)],
    }


@pytest.fixture
def cache(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    return tmp_path


def _sec(monkeypatch, registrants: list[Registrant], registration: IpoRegistration | None) -> list[str]:
    asked: list[str] = []

    async def find(name: str, *, user_agent: str) -> list[Registrant]:
        assert user_agent and "@" not in user_agent
        asked.append(name)
        return registrants

    async def fetch(cik: int, *, user_agent: str) -> IpoRegistration | None:
        assert cik == registrants[0].cik
        return registration

    monkeypatch.setattr(ipo_source, "find_registrants", find)
    monkeypatch.setattr(ipo_source, "fetch_registration", fetch)
    return asked


def _write(cache: Path, name: str, data: dict[str, object]) -> Path:
    path = cache / "ipos" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


FOUND = [Registrant(9, "Fictional Labs, PBC", "FLAB"), Registrant(10, "Fictional Labs Trust")]


def test_a_filed_registration_is_shown_with_its_timeline(cache, capsys, monkeypatch):
    asked = _sec(monkeypatch, FOUND, _registration())
    assert run_ipo_command("  fictional labs ") == 0
    out = capsys.readouterr().out
    assert asked == ["fictional labs"]
    assert "Fictional Labs, PBC" in out and "filed publicly" in out
    assert "FLAB" in out and "Services-Prepackaged Software" in out
    assert "2026-10-26" in out and "Amendments:         1" in out
    assert FILING_URL in out and "DRS" in out
    assert "Other registrants matching the name: Fictional Labs Trust" in out
    assert "/analyse-ipo fictional labs" in out and "fictional_labs_pbc.json" in out
    assert not (cache / "scores.json").exists()


def test_nothing_found_says_it_is_not_proof_of_not_filing(cache, capsys, monkeypatch):
    _sec(monkeypatch, [], None)
    assert run_ipo_command("Bigco AI") == 0
    out = capsys.readouterr().out
    assert "no public registration statement" in out
    assert "does NOT mean" in out and "confidential draft" in out
    assert "bigco_ai.json" in out


def test_a_registrant_without_a_filing_list(cache, capsys, monkeypatch):
    _sec(monkeypatch, FOUND, None)
    assert run_ipo_command("fictional") == 0
    assert "did not return its filing list" in capsys.readouterr().out


def test_a_company_already_trading_is_handed_to_analyse(cache, capsys, monkeypatch):
    _sec(monkeypatch, FOUND[:1], _registration(exchange="Nasdaq"))
    assert run_ipo_command("fictional labs") == 0
    out = capsys.readouterr().out
    assert "already trading" in out and "analyse FLAB" in out and "on Nasdaq" in out
    assert "2026-11-10" in out  # timeline still shown
    assert "No transcribed statements" not in out


def test_transcribed_statements_are_scored_and_labelled(cache, capsys, monkeypatch):
    _sec(monkeypatch, FOUND, _registration())
    _write(cache, "fictional_labs_pbc", _statements(price_low=18, price_high=22,
                                                   shares_after_offering=100_000_000))
    assert run_ipo_command("fictional labs") == 0
    out = capsys.readouterr().out
    assert "transcribed from the prospectus by Claude" in out
    assert FILING_URL in out and "2026-11-12T09:00:00" in out and "Balance sheet is pro forma." in out
    assert "UNKNOWN — not clean" in out
    assert "midpoint of the $18.00–$22.00 range" in out
    assert "Fundamentals" in out  # the ordinary stock report follows
    assert "/analyse-ipo fictional labs" in out  # no judgment yet
    assert not (cache / "scores.json").exists() and not (cache / "signals.json").exists()


def test_the_query_slug_is_the_fallback_and_price_overrides_the_range(cache, capsys, monkeypatch):
    _sec(monkeypatch, [], None)
    _write(cache, "fictional", _statements(price_low=18, price_high=22, shares_after_offering=1e8))
    assert run_ipo_command("Fictional", offer_price=30.0) == 0
    assert "at --price $30.00" in capsys.readouterr().out


@pytest.mark.parametrize(
    "offering, expected",
    [({}, "aren't set yet"),
     ({"price_low": 20}, "share count after the offering isn't"),
     ({"price_low": 20, "price_high": 20, "shares_after_offering": 1e8}, "at the offer price $20.00")],
)
def test_the_valuation_basis_is_named(cache, capsys, monkeypatch, offering, expected):
    _sec(monkeypatch, [], None)
    _write(cache, "fictional", _statements(**offering))
    assert run_ipo_command("fictional") == 0
    assert expected in capsys.readouterr().out


def test_an_unreadable_or_unscorable_file_is_named(cache, capsys, monkeypatch):
    _sec(monkeypatch, [], None)
    path = _write(cache, "fictional", {"company": "No currency"})
    assert run_ipo_command("fictional") == 0
    assert "could not be read" in capsys.readouterr().out

    path.write_text(json.dumps({"company": "Thin", "currency": "USD", "fiscal_years": [
        {"fiscal_year_end": "2025-12-31", "revenue": 1}]}))
    assert run_ipo_command("fictional") == 0
    assert "can't be scored" in capsys.readouterr().out


def test_json_shape_with_a_judgment(cache, capsys, monkeypatch):
    _sec(monkeypatch, FOUND, _registration())
    _write(cache, "fictional_labs_pbc", _statements(price_low=18, price_high=22,
                                                   shares_after_offering=100_000_000))
    JudgmentStore(cache).save(Judgment(
        ticker="FLAB", leader_name="A. Founder",
        leadership=PillarJudgment(score=60, evidence=["thin record"]),
        prospects=PillarJudgment(score=55, evidence=["Position risk: a tenth of the leader"]),
        news=PillarJudgment(score=55, evidence=["listing excitement is neutral"]),
        updated_at=datetime.now().isoformat(timespec="seconds"),
    ))
    assert run_ipo_command("fictional labs", as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"query", "registration", "other_matches", "statements_file", "scored"}
    assert payload["registration"]["stage"] == "filed"
    assert payload["other_matches"] == [{"cik": 10, "name": "Fictional Labs Trust", "proposed_ticker": ""}]
    assert payload["statements_file"].endswith("ipos/fictional_labs_pbc.json")
    scored = payload["scored"]
    assert scored["ticker"] == "FLAB" and scored["offer_price"] == 20.0
    assert scored["market_cap"] == 2e9 and scored["valuation"] is not None
    assert scored["unscored"] == []  # the judgment was picked up under the reserved ticker
    assert scored["risk"]["volatility_1y_pct"] is None and scored["risk_discount"] >= 10
    assert isinstance(scored["early_signals"], list) and "fundamentals" in scored["evidence"]
    assert {"fundamentals", "composite", "verdict"} <= set(scored)


def test_json_without_statements_or_registration(cache, capsys, monkeypatch):
    _sec(monkeypatch, [], None)
    assert run_ipo_command("nobody", as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registration"] is None and payload["scored"] is None
    assert payload["statements_file"] is None and payload["write_statements_to"].endswith("nobody.json")


def test_json_unscored_valuation_is_null(cache, capsys, monkeypatch):
    _sec(monkeypatch, [], None)
    _write(cache, "fictional", _statements())
    assert run_ipo_command("fictional", as_json=True) == 0
    scored = json.loads(capsys.readouterr().out)["scored"]
    assert scored["valuation"] is None and scored["offer_price"] is None and scored["market_cap"] is None
    assert "valuation" in scored["unscored"]


def test_a_blank_name_is_refused(cache, capsys):
    assert run_ipo_command("   ") == 2
    assert "Name a company" in capsys.readouterr().out

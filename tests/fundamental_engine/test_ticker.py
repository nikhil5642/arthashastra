"""Ticker normalization tests."""

from __future__ import annotations

from fundamental_engine.ticker import canonical, slug, yf_symbol


def test_canonical_strips_exchange_suffix():
    assert canonical("reliance") == "RELIANCE"
    assert canonical("RELIANCE") == "RELIANCE"
    assert canonical("RELIANCE.NS") == "RELIANCE"
    assert canonical("RELIANCE.BO") == "RELIANCE"
    assert canonical("  tcs  ") == "TCS"


def test_yf_symbol_defaults_to_nse():
    assert yf_symbol("RELIANCE") == "RELIANCE.NS"
    assert yf_symbol("tcs") == "TCS.NS"


def test_yf_symbol_preserves_explicit_suffix():
    assert yf_symbol("RELIANCE.BO") == "RELIANCE.BO"
    assert yf_symbol("RELIANCE.NS") == "RELIANCE.NS"


def test_yf_symbol_respects_prefer_bse():
    assert yf_symbol("RELIANCE", prefer="BSE") == "RELIANCE.BO"


def test_slug_normalizes_whitespace_and_case():
    assert slug("Mukesh Ambani") == "mukesh_ambani"
    assert slug("  Deepinder  Goyal  ") == "deepinder_goyal"


def test_slug_strips_punctuation():
    assert slug("N. R. Narayana Murthy") == "n_r_narayana_murthy"
    assert slug("O'Shaughnessy") == "oshaughnessy"
    assert slug("Mukesh Ambani (Sr.)") == "mukesh_ambani_sr"
    assert slug("A & B Corp") == "a_b_corp"


def test_slug_handles_unicode():
    # Diacritics are stripped to ASCII equivalents.
    assert slug("Löfström") == "lofstrom"


def test_slug_empty_input_returns_empty():
    assert slug("") == ""
    assert slug("   ") == ""

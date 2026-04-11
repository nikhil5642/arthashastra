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
    assert slug("N. R. Narayana Murthy") == "n._r._narayana_murthy"

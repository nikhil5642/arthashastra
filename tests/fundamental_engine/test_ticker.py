"""Ticker normalization tests."""

from __future__ import annotations

from fundamental_engine.ticker import canonical, slug


def test_canonical_is_the_uppercased_yahoo_symbol():
    assert canonical("msft") == "MSFT"
    assert canonical("  googl  ") == "GOOGL"
    assert canonical("reliance.ns") == "RELIANCE.NS"
    assert canonical("0700.hk") == "0700.HK"


def test_canonical_never_assumes_a_market():
    # 'BHP' (NYSE) and 'BHP.AX' (ASX) are different listings.
    assert canonical("BHP") != canonical("BHP.AX")


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

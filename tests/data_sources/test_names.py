"""Company-name normalisation tests — pure logic."""

from __future__ import annotations

from data_sources.names import clean_company_name, news_query_name


def test_clean_company_name_drops_legal_suffixes_and_brackets():
    assert clean_company_name("Reliance Industries Limited") == "Reliance Industries"
    assert clean_company_name("HDFC Bank Ltd.") == "HDFC Bank"
    assert clean_company_name("Test Industries Ltd (Consolidated)") == "Test Industries"
    assert clean_company_name("Apple Inc.") == "Apple"


def test_clean_company_name_keeps_meaningful_words():
    assert clean_company_name("Larsen & Toubro Limited") == "Larsen & Toubro"
    assert clean_company_name("") == ""


def test_news_query_keeps_the_suffix_when_the_short_name_is_ambiguous():
    assert news_query_name("Tata Consultancy Services Limited") == "Tata Consultancy Services"
    # "ITC" alone matches unrelated headlines (e.g. the US Trade Commission).
    assert news_query_name("ITC Limited") == "ITC Limited"

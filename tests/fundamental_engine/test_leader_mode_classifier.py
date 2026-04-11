"""Leadership mode classifier tests — uses date-relative years, not hardcoded."""

from __future__ import annotations

from datetime import date

from fundamental_engine.leader_mode_classifier import (
    FIRST_GEN,
    FIRST_GEN_MAX_AGE_YEARS,
    MULTI_GEN_FAMILY,
    PROFESSIONAL,
    VALID_MODES,
    classify_mode,
)


def _years_ago(n: int) -> int:
    return date.today().year - n


def test_first_gen_recent_listing_with_active_founder():
    assert classify_mode(
        listed_year=_years_ago(3),
        is_founder_active=True,
        generation=1,
        promoter_holding_pct=35,
    ) == FIRST_GEN


def test_first_gen_rejected_when_founder_inactive():
    mode = classify_mode(
        listed_year=_years_ago(3),
        is_founder_active=False,
        generation=1,
        promoter_holding_pct=35,
    )
    assert mode != FIRST_GEN


def test_first_gen_rejected_when_too_old():
    mode = classify_mode(
        listed_year=_years_ago(FIRST_GEN_MAX_AGE_YEARS + 5),
        is_founder_active=True,
        generation=1,
        promoter_holding_pct=35,
    )
    assert mode != FIRST_GEN


def test_multi_gen_family_requires_holding_and_generation():
    assert classify_mode(
        listed_year=_years_ago(30),
        promoter_holding_pct=50,
        generation=2,
    ) == MULTI_GEN_FAMILY


def test_multi_gen_family_accepts_family_promoter_flag():
    assert classify_mode(
        listed_year=_years_ago(30),
        promoter_holding_pct=45,
        generation=1,
        has_family_promoter=True,
    ) == MULTI_GEN_FAMILY


def test_low_holding_becomes_professional():
    assert classify_mode(
        listed_year=_years_ago(30),
        promoter_holding_pct=15,
        generation=3,
    ) == PROFESSIONAL


def test_no_data_defaults_professional():
    assert classify_mode() == PROFESSIONAL


def test_valid_modes_set():
    assert VALID_MODES == {FIRST_GEN, MULTI_GEN_FAMILY, PROFESSIONAL}

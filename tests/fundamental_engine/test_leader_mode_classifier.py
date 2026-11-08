"""Leadership mode classifier tests — global modes, pure logic."""

from __future__ import annotations

from fundamental_engine.leader_mode_classifier import (
    VALID_MODES,
    LeaderMode,
    classify_mode,
)


def test_founder_led_regardless_of_listing_age():
    """NVIDIA / Meta style: listed decades ago, founder still running it."""
    assert classify_mode(founder_active=True, insider_holding_pct=4.0) == LeaderMode.FOUNDER_LED


def test_state_control_takes_precedence():
    assert (
        classify_mode(state_controlled=True, founder_active=True, insider_holding_pct=60.0)
        == LeaderMode.STATE_CONTROLLED
    )


def test_family_control_needs_a_bloc_and_a_family():
    assert classify_mode(insider_holding_pct=50.0, generation=2) == LeaderMode.FAMILY_CONTROLLED
    assert (
        classify_mode(insider_holding_pct=45.0, has_family_control=True)
        == LeaderMode.FAMILY_CONTROLLED
    )


def test_widely_held_is_professional():
    assert classify_mode(insider_holding_pct=0.1) == LeaderMode.PROFESSIONAL
    assert classify_mode() == LeaderMode.PROFESSIONAL


def test_legacy_mode_names_still_parse():
    assert LeaderMode("FIRST_GEN") == LeaderMode.FOUNDER_LED
    assert LeaderMode("MULTI_GEN_FAMILY") == LeaderMode.FAMILY_CONTROLLED


def test_valid_modes_set():
    assert VALID_MODES == {
        LeaderMode.FOUNDER_LED,
        LeaderMode.FAMILY_CONTROLLED,
        LeaderMode.STATE_CONTROLLED,
        LeaderMode.PROFESSIONAL,
    }

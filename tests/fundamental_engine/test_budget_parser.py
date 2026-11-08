"""Budget parser tests — international + Indian magnitudes, invalid input."""

from __future__ import annotations

import pytest

from fundamental_engine.budget_parser import parse_budget


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100000", 100_000),
        ("1,00,000", 100_000),
        ("1L", 100_000),
        ("1l", 100_000),
        ("1 L", 100_000),
        ("2.5L", 250_000),
        ("5lakh", 500_000),
        ("10 Lac", 1_000_000),
        ("1cr", 10_000_000),
        ("0.5 Crore", 5_000_000),
        ("50k", 50_000),
        ("100", 100),
        ("2.5m", 2_500_000),
        ("3 mn", 3_000_000),
        ("1.2bn", 1_200_000_000),
        ("1 billion", 1_000_000_000),
    ],
)
def test_parse_budget_happy_path(raw, expected):
    assert parse_budget(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "abc", "1.5.2L", "foo L", "L100"])
def test_parse_budget_invalid_returns_none(raw):
    assert parse_budget(raw) is None

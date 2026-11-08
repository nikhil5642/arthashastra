"""Money formatting tests — each amount in its own currency."""

from __future__ import annotations

from reports.formatting import format_amount, format_money, format_price


def test_prices_use_the_listing_currency():
    assert format_price(412.3, "USD") == "$412.30"
    assert format_price(1450.75, "INR") == "₹1,450.75"
    assert format_price(612.0, "HKD") == "HK$612.00"
    assert format_price(3722.5, "GBp") == "£37.23"  # London quotes in pence
    assert format_price(98.1, "CHF") == "CHF 98.10"
    assert format_price(None, "USD") == "—"


def test_large_amounts_scale_by_currency_convention():
    assert format_money(3.94e12, "USD") == "$3.94T"
    assert format_money(18_27_979 * 1e7, "INR") == "₹1,827,979 Cr"
    assert format_money(-2.5e9, "JPY") == "-¥2.50B"


def test_plain_amounts_do_not_scale():
    assert format_amount(25_000, "INR") == "₹25,000"


def test_asian_currencies():
    assert format_price(57900.0, "KRW") == "₩57,900.00"
    assert format_money(4.2e14, "KRW") == "₩420.00T"
    assert format_money(3.1e13, "TWD") == "NT$31.00T"

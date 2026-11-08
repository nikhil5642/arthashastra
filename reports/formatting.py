"""Rich-based terminal formatting helpers for on-demand reports."""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from data_sources.fx_source import major_unit
from scorers.base import Gate, Verdict

# Verdict → Rich style mapping. Keyed by the Verdict enum so typos at
# the call site raise a KeyError instead of falling through to "white".
VERDICT_STYLES: dict[Verdict, str] = {
    Verdict.STRONG_BUY: "bold green",
    Verdict.BUY: "green",
    Verdict.HOLD: "yellow",
    Verdict.AVOID: "red",
    Verdict.RED_FLAG: "bold red",
}

# ── Money ────────────────────────────────────────────────────────────────────
#
# Each amount prints in its own currency: prices and market cap in the
# listing's trading currency, statement lines in the reporting currency.
# INR keeps the crore convention Indian filings use; every other currency
# is scaled to K / M / B / T.

_CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "CN¥",
    "HKD": "HK$",
    "KRW": "₩",
    "TWD": "NT$",
}

_SCALES: tuple[tuple[float, str], ...] = (
    (1e12, "T"),
    (1e9, "B"),
    (1e6, "M"),
    (1e3, "K"),
)

RUPEES_PER_CRORE = 1e7


def currency_symbol(code: str) -> str:
    """'USD' → '$'; unknown codes print as 'CHF '; '' (unknown) → ''."""
    if not code:
        return ""
    return _CURRENCY_SYMBOLS.get(code.upper(), f"{code.upper()} ")


def format_price(value: float | None, currency: str) -> str:
    """A per-share price: '$412.30', '₹1,450.75', 'HK$612.00', '£37.23'.

    Minor-unit quotes (London's pence) are shown in the major unit.
    """
    if value is None:
        return "—"
    major, divisor = major_unit(currency)
    return f"{currency_symbol(major)}{value / divisor:,.2f}"


def format_amount(value: float | None, currency: str) -> str:
    """A whole-currency amount without scaling: '₹25,000', '$1,200'."""
    if value is None:
        return "—"
    return f"{currency_symbol(currency)}{value:,.0f}"


def format_money(value: float | None, currency: str) -> str:
    """A large amount: '₹18,27,979 Cr' style for INR, '$3.12T' elsewhere."""
    if value is None:
        return "—"
    symbol = currency_symbol(currency)
    if currency.upper() == "INR":
        return f"{symbol}{value / RUPEES_PER_CRORE:,.0f} Cr"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    for threshold, suffix in _SCALES:
        if magnitude >= threshold:
            return f"{sign}{symbol}{magnitude / threshold:,.2f}{suffix}"
    return f"{sign}{symbol}{magnitude:,.0f}"


# One-line explanation shown under the verdict whenever a gate fired.
GATE_LABELS: dict[Gate, str] = {
    Gate.INTEGRITY_VETO: (
        "GATE: integrity veto — composite capped at the top of the AVOID band"
    ),
}


def make_console(*, quiet: bool = False) -> Console:
    if quiet:
        import io
        return Console(file=io.StringIO(), force_terminal=False)
    return Console()


def verdict_text(verdict: Verdict | str, confidence: float) -> Text:
    """Render a verdict with its conviction colour."""
    # Accept raw strings at the boundary (judgment JSON, tests) and
    # coerce back to the enum so downstream is fully typed.
    if not isinstance(verdict, Verdict):
        try:
            verdict = Verdict(verdict)
        except ValueError:
            return Text(f"{verdict}  (confidence {confidence:.0%})", style="white")
    style = VERDICT_STYLES[verdict]
    return Text(f"{verdict.value}  (confidence {confidence:.0%})", style=style)


def evidence_table(evidence: list[str]) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column()
    if not evidence:
        table.add_row("(no evidence)")
    else:
        for bullet in evidence:
            table.add_row(f"• {bullet}")
    return table


def section_panel(
    title: str, weight_pct: int, score: float, body: RenderableType
) -> Panel:
    header = f"{title} ({weight_pct}%) — score {score:.1f}"
    return Panel(body, title=header, title_align="left", border_style="dim")

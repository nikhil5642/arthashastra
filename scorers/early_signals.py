"""Early signals — what changed since the audited numbers, named before it shows in a score.

The fundamentals pillar scores the latest full fiscal year, which can be
most of a year old. This layer reads everything fresher or deeper that the
snapshot carries — the four newest quarters, the latest balance sheet
against the one a year back, the ownership tape, the margin history — and
names each thing worth knowing, as a CONCERN or a STRENGTH, with the
numbers behind it.

    earnings quality   profit padded by one-offs · margins at a cyclical peak
    cash               free cash flow falling / rising · capex outrunning cash
                       flow · heavy stock-based pay
    momentum           revenue shrinking / slowing / accelerating · operating
                       margin compressing / expanding · profit vs sales
    balance sheet      debt rising / falling · negative equity · receivables
                       or inventory outpacing sales · a jump in goodwill
    ownership          dilution / buybacks · insider and foreign-institution
                       flows · retail crowding in
    lenders            asset quality worsening / improving
    news               a burst of governance stories · a credit-rating action
                       (counted from headlines, before anyone has judged them)
    tape and data      a deep price drawdown · stale statements · a quote
                       whose price and statements don't reconcile · a listing
                       under a year old

Rules every detector follows:
  - A signal needs the numbers. Missing data produces no signal — never a
    strength ("unknown is not good"), and never a concern either.
  - A signal states facts, not a verdict. Scores are not touched here: the
    valuation pillar already prices on repeatable earnings, and Claude
    weighs the rest in the qualitative pillars (see `/analyse-stock`).
  - Thresholds are universal and currency-free, like every other curve.
  - A strength paid for out of flagged profit is context, not a material
    strength. When the profit is padded by one-offs or sits above the
    company's own cycle, falling debt, rising free cash flow and widening
    margins are that same profit seen again — deleveraging at the top of a
    cycle is what cyclicals do before it turns. They stay in the report at
    INFO, with a note.

Severity: URGENT changes how the headline numbers should be read (read it
before trusting the verdict), ALERT is material, INFO is context.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from data_sources.fundamentals_source import (
    BalanceSheetSnapshot,
    StatementWindow,
    change_by_date,
    is_bank_snapshot,
)
from data_sources.news_source import NewsCategory, NewsDigest
from scorers.fundamentals_scorer.industrial import has_negative_equity
from scorers.risk_profile import risk_profile
from scorers.valuation_scorer import DOUBT_PE, SustainableEarnings, sustainable_earnings
from signals.signal_bus import Severity

# ── Thresholds ───────────────────────────────────────────────────────────────

# Earnings quality: share of reported profit that is core.
ONE_OFF_ALERT_CORE_RATIO = 0.85
ONE_OFF_URGENT_CORE_RATIO = 0.60
PEAK_MARGIN_URGENT_MULTIPLE = 3.0  # core margin ÷ own median
PEAK_MARGIN_MIN_HAIRCUT = 0.10  # below this the valuation barely moves — not worth a flag

# Cash: trailing twelve months against the latest fiscal year.
FREE_CASH_FLOW_SWING = 0.40
CAPEX_SHARE_OF_CASH_FLOW = 0.85
STOCK_PAY_ALERT_SHARE_OF_REVENUE = 0.10
STOCK_PAY_URGENT_SHARE_OF_REVENUE = 0.20

# Momentum: the latest quarter against the same quarter a year earlier.
REVENUE_SHRINKING_PCT = -2.0
REVENUE_SHRINKING_URGENT_PCT = -10.0
REVENUE_PACE_GAP_PTS = 8.0  # latest-quarter growth vs the 3-year CAGR
GROWER_MIN_CAGR_PCT = 10.0
MARGIN_SHIFT_PTS = 3.0
MARGIN_SHIFT_URGENT_PTS = 8.0
PROFIT_DROP_PCT = -15.0
PROFIT_RISE_PCT = 15.0
SALES_GROWING_PCT = 5.0
PROFIT_LEAD_PTS = 10.0

# Balance sheet: the latest against the one a year earlier.
DEBT_RISE_PCT = 30.0
DEBT_RISE_URGENT_PCT = 100.0
DEBT_FALL_PCT = -25.0
MATERIAL_DEBT_TO_EQUITY = 0.20
HEAVY_DEBT_TO_EQUITY = 0.50
WORKING_CAPITAL_RISE_PCT = 20.0
# A balance "outpaces" sales when the days of sales it represents grow — a
# ratio, not a gap in points: 127% against 106% sales growth is days +10%
# (in step), 25% against 5% is days +19% (customers paying later).
WORKING_CAPITAL_DAYS_RISE_PCT = 15.0
# Receivables or inventory worth under 15 days of business are too small
# for their growth to mean anything (a software company's stock room).
MATERIAL_WORKING_CAPITAL_DAYS = 15.0
GOODWILL_RISE_PCT = 50.0
MATERIAL_GOODWILL_TO_EQUITY = 0.05
HEAVY_GOODWILL_TO_EQUITY = 0.20

# Ownership.
DILUTION_PCT = 3.0
DILUTION_URGENT_PCT = 10.0
BUYBACK_PCT = -2.0
INSIDER_SHIFT_PTS = 1.0
INSIDER_SELLING_URGENT_PTS = -3.0
FOREIGN_SHIFT_PTS = 2.0
FOREIGN_SELLING_ALERT_PTS = -5.0
RETAIL_CROWDING_PCT = 20.0
RETAIL_CROWDING_FOREIGN_PTS = -1.0

# Lenders.
NPA_WORSENING_PTS = 0.25
NPA_WORSENING_URGENT_PTS = 1.0
NPA_IMPROVING_PTS = -0.5

# News radar: stories in the digest's window (a week). A burst is governance
# taking over the company's news — several stories AND a real share of the
# week's coverage — or one story many outlets carried. A megacap draws 80
# stories a week, a few of which always say "antitrust" or "probe"; that is
# everyday noise, and the share test is what tells the two apart.
GOVERNANCE_STORIES = 3
GOVERNANCE_SHARE = 0.15
GOVERNANCE_URGENT_STORIES = 8
GOVERNANCE_URGENT_SHARE = 0.30
# A company a handful of outlets write about can never produce eight stories
# on anything. For it the same event is most of the little that is written:
# story counts scale with how widely a company is covered, the share does not.
THIN_COVERAGE_STORIES = 2
THIN_COVERAGE_SHARE = 0.50
THIN_COVERAGE_URGENT_STORIES = 4
THIN_COVERAGE_URGENT_SHARE = 0.60
WIDE_COVERAGE_OUTLETS = 5
WIDE_COVERAGE_URGENT_OUTLETS = 10

# Tape and data.
DRAWDOWN_INFO_PCT = 30.0
MIN_YEARS_FOR_RECORD = 4  # fewer fiscal years than this is a short record, said elsewhere
DRAWDOWN_ALERT_PCT = 50.0
# A fiscal year's audited numbers normally land within ~3 months of
# year-end, so anything older than 12 + 3 months means a missed filing,
# a delisted / suspended company, or a source that stopped updating.
STALE_ANNUAL_MONTHS = 15
# Under a year of trading: every price-based measure covers only that stretch,
# and the insiders' post-listing lock-up has usually not run out yet.
RECENT_LISTING_YEARS = 1.0


# ── Types ────────────────────────────────────────────────────────────────────


class Tone(StrEnum):
    CONCERN = "concern"
    STRENGTH = "strength"


class EarlySignalKind(StrEnum):
    # Earnings quality
    ONE_OFF_PROFIT = "one_off_profit"
    PEAK_MARGIN = "peak_margin"
    # Cash
    FREE_CASH_FLOW_FALLING = "free_cash_flow_falling"
    FREE_CASH_FLOW_RISING = "free_cash_flow_rising"
    CAPEX_OUTRUNS_CASH_FLOW = "capex_outruns_cash_flow"
    STOCK_PAY_HEAVY = "stock_pay_heavy"
    # Momentum
    REVENUE_SHRINKING = "revenue_shrinking"
    REVENUE_DECELERATING = "revenue_decelerating"
    REVENUE_ACCELERATING = "revenue_accelerating"
    MARGIN_COMPRESSING = "margin_compressing"
    MARGIN_EXPANDING = "margin_expanding"
    PROFIT_FALLING_AS_SALES_GROW = "profit_falling_as_sales_grow"
    PROFIT_OUTPACING_SALES = "profit_outpacing_sales"
    # Balance sheet
    DEBT_RISING = "debt_rising"
    DEBT_FALLING = "debt_falling"
    NEGATIVE_EQUITY = "negative_equity"
    RECEIVABLES_OUTPACING_SALES = "receivables_outpacing_sales"
    INVENTORY_OUTPACING_SALES = "inventory_outpacing_sales"
    GOODWILL_JUMP = "goodwill_jump"
    # Ownership
    SHARE_DILUTION = "share_dilution"
    SHARE_BUYBACK = "share_buyback"
    INSIDER_SELLING = "insider_selling"
    INSIDER_BUYING = "insider_buying"
    FOREIGN_INSTITUTIONS_SELLING = "foreign_institutions_selling"
    FOREIGN_INSTITUTIONS_BUYING = "foreign_institutions_buying"
    RETAIL_CROWDING = "retail_crowding"
    # Lenders
    ASSET_QUALITY_WORSENING = "asset_quality_worsening"
    ASSET_QUALITY_IMPROVING = "asset_quality_improving"
    # News radar
    GOVERNANCE_NEWS = "governance_news"
    CREDIT_RATING_NEWS = "credit_rating_news"
    # Tape and data
    PRICE_DRAWDOWN = "price_drawdown"
    PRICED_FOR_DOUBT = "priced_for_doubt"
    UNPROVEN_PROFIT = "unproven_profit"
    STALE_STATEMENTS = "stale_statements"
    UNRELIABLE_QUOTE = "unreliable_quote"
    RECENTLY_LISTED = "recently_listed"


@dataclass(frozen=True, slots=True)
class EarlySignal:
    """One named observation: what it is, which way it cuts, how much it matters."""

    kind: EarlySignalKind
    tone: Tone
    severity: Severity
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "tone": self.tone.value,
            "severity": self.severity.value,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EarlySignal | None:
        """Parse one cached row; None when it doesn't fit (an unknown kind, a missing message)."""
        message = data.get("message")
        if not isinstance(message, str) or not message:
            return None
        try:
            return cls(
                kind=EarlySignalKind(str(data.get("kind"))),
                tone=Tone(str(data.get("tone"))),
                severity=Severity(str(data.get("severity"))),
                message=message,
            )
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class _Facts:
    """What every detector reads: the snapshot plus the judgments shared with the scorers."""

    snap: BalanceSheetSnapshot
    is_bank: bool
    earnings: SustainableEarnings
    news: NewsDigest | None  # None when the news feed wasn't read
    today: date


_Detector = Callable[[_Facts], EarlySignal | None]

# The two concerns that say the reported profit may not repeat, and the
# strengths that are paid for out of that profit.
_PROFIT_MAY_NOT_REPEAT: frozenset[EarlySignalKind] = frozenset(
    {EarlySignalKind.ONE_OFF_PROFIT, EarlySignalKind.PEAK_MARGIN}
)
_PAID_FOR_OUT_OF_PROFIT: frozenset[EarlySignalKind] = frozenset(
    {
        EarlySignalKind.FREE_CASH_FLOW_RISING,
        EarlySignalKind.MARGIN_EXPANDING,
        EarlySignalKind.PROFIT_OUTPACING_SALES,
        EarlySignalKind.DEBT_FALLING,
    }
)
_PROFIT_MAY_NOT_REPEAT_NOTE = (
    "Read it with the earnings concern above: it is paid for out of profit that may not repeat."
)

_TONE_ORDER: dict[Tone, int] = {Tone.CONCERN: 0, Tone.STRENGTH: 1}


# ── Public entry points ──────────────────────────────────────────────────────


def detect_early_signals(
    snap: BalanceSheetSnapshot,
    *,
    news: NewsDigest | None = None,
    today: date | None = None,
) -> list[EarlySignal]:
    """Every signal the snapshot (and the news digest, if given) supports.

    Concerns come first, most severe first.
    """
    facts = _Facts(
        snap=snap,
        is_bank=is_bank_snapshot(snap),
        earnings=sustainable_earnings(snap),
        news=news,
        today=today or date.today(),
    )
    found = [signal for detect in _DETECTORS if (signal := detect(facts)) is not None]
    found = _discount_strengths_paid_for_out_of_flagged_profit(found)
    return sorted(found, key=lambda s: (_TONE_ORDER[s.tone], -s.severity.rank))


def _discount_strengths_paid_for_out_of_flagged_profit(
    signals: list[EarlySignal],
) -> list[EarlySignal]:
    """With an earnings-quality concern present, profit-funded strengths become INFO."""
    if not any(signal.kind in _PROFIT_MAY_NOT_REPEAT for signal in signals):
        return signals
    return [
        replace(
            signal,
            severity=Severity.INFO,
            message=f"{signal.message} {_PROFIT_MAY_NOT_REPEAT_NOTE}",
        )
        if signal.kind in _PAID_FOR_OUT_OF_PROFIT
        else signal
        for signal in signals
    ]


def of_tone(signals: Iterable[EarlySignal], tone: Tone) -> list[EarlySignal]:
    return [s for s in signals if s.tone is tone]


def tally(signals: Sequence[EarlySignal]) -> str:
    """'3 concerns (1 urgent) · 2 strengths', or '' when there is nothing."""
    concerns = of_tone(signals, Tone.CONCERN)
    strengths = of_tone(signals, Tone.STRENGTH)
    parts: list[str] = []
    if concerns:
        urgent = sum(1 for s in concerns if s.severity is Severity.URGENT)
        parts.append(
            f"{len(concerns)} concern{'s' if len(concerns) != 1 else ''}"
            + (f" ({urgent} urgent)" if urgent else "")
        )
    if strengths:
        parts.append(f"{len(strengths)} strength{'s' if len(strengths) != 1 else ''}")
    return " · ".join(parts)


def months_since(iso_date: str, *, today: date | None = None) -> int | None:
    """Whole months from an ISO date to today; None when it doesn't parse."""
    try:
        then = date.fromisoformat(iso_date)
    except ValueError:
        return None
    return max(0, round(((today or date.today()) - then).days / 30.44))


# ── Earnings quality ─────────────────────────────────────────────────────────


def _one_off_profit(facts: _Facts) -> EarlySignal | None:
    core_ratio = facts.earnings.core_ratio
    if core_ratio >= ONE_OFF_ALERT_CORE_RATIO:
        return None
    pe = facts.snap.pe_ratio
    reading = (
        f"on core earnings the P/E is {pe / core_ratio:.1f}, not {pe:.1f}"
        if pe is not None and core_ratio > 0
        else "the reported profit overstates what the business earns"
    )
    return _concern(
        EarlySignalKind.ONE_OFF_PROFIT,
        Severity.URGENT if core_ratio < ONE_OFF_URGENT_CORE_RATIO else Severity.ALERT,
        f"{(1 - core_ratio) * 100:.0f}% of the profit over the {_earnings_window(facts.snap)} "
        f"came from unusual items (investment gains, one-off credits) — {reading}.",
    )


def _peak_margin(facts: _Facts) -> EarlySignal | None:
    earnings = facts.earnings
    if earnings.core_margin is None or earnings.typical_margin is None:
        return None
    if 1 - earnings.cycle_ratio < PEAK_MARGIN_MIN_HAIRCUT:
        return None
    if earnings.typical_margin <= 0:
        return _concern(
            EarlySignalKind.PEAK_MARGIN,
            Severity.ALERT,
            f"The business typically lost money (median net margin {earnings.typical_margin:.1f}% "
            f"over {earnings.margin_years} fiscal years) — today's {earnings.core_margin:.1f}% "
            f"margin is not yet proven through a cycle.",
        )
    multiple = earnings.core_margin / earnings.typical_margin
    pe = facts.snap.pe_ratio
    reading = (
        f"; on mid-cycle earnings the P/E is {pe / earnings.ratio:.1f}, not {pe:.1f}"
        if pe is not None and earnings.ratio > 0
        else ""
    )
    return _concern(
        EarlySignalKind.PEAK_MARGIN,
        Severity.URGENT if multiple >= PEAK_MARGIN_URGENT_MULTIPLE else Severity.ALERT,
        f"Net margin {earnings.core_margin:.1f}% is {multiple:.1f}× its "
        f"{earnings.margin_years}-year median ({earnings.typical_margin:.1f}%) — earnings may be "
        f"at a cyclical peak{reading}.",
    )


# ── Cash ─────────────────────────────────────────────────────────────────────


def _free_cash_flow(facts: _Facts) -> EarlySignal | None:
    """Trailing twelve months against the latest fiscal year — only when the quarters add news."""
    snap = facts.snap
    trailing, fiscal = snap.ttm_free_cash_flow, snap.free_cash_flow
    if facts.is_bank or not snap.ttm_adds_news or trailing is None or fiscal is None:
        return None
    window = f"the trailing twelve months (to {snap.ttm_period_end})"
    if trailing < 0:
        if fiscal > 0:
            return _concern(
                EarlySignalKind.FREE_CASH_FLOW_FALLING,
                Severity.URGENT,
                f"Free cash flow turned negative over {window}, after a positive latest fiscal year.",
            )
        return _concern(
            EarlySignalKind.FREE_CASH_FLOW_FALLING,
            Severity.ALERT,
            f"Free cash flow stayed negative over {window} — the business is still consuming cash.",
        )
    if fiscal <= 0:
        return _strength(
            EarlySignalKind.FREE_CASH_FLOW_RISING,
            Severity.ALERT,
            f"Free cash flow turned positive over {window}, after a negative latest fiscal year.",
        )
    change = trailing / fiscal - 1
    if change <= -FREE_CASH_FLOW_SWING:
        return _concern(
            EarlySignalKind.FREE_CASH_FLOW_FALLING,
            Severity.ALERT,
            f"Free cash flow over {window} is {-change * 100:.0f}% below the latest fiscal year's.",
        )
    if change >= FREE_CASH_FLOW_SWING:
        return _strength(
            EarlySignalKind.FREE_CASH_FLOW_RISING,
            Severity.ALERT,
            f"Free cash flow over {window} is {change * 100:.0f}% above the latest fiscal year's.",
        )
    return None


def _capex_outruns_cash_flow(facts: _Facts) -> EarlySignal | None:
    if facts.is_bank:
        return None
    flows = _same_window(facts.snap, "capex", "operating_cash_flow")
    if flows is None:
        return None
    (capex, operating_cash_flow), window = flows
    if operating_cash_flow <= 0 or capex / operating_cash_flow < CAPEX_SHARE_OF_CASH_FLOW:
        return None
    return _concern(
        EarlySignalKind.CAPEX_OUTRUNS_CASH_FLOW,
        Severity.ALERT,
        f"Capital spending took {capex / operating_cash_flow * 100:.0f}% of operating cash flow "
        f"over the {window.label} — little is left for shareholders without new debt or equity.",
    )


def _stock_pay_heavy(facts: _Facts) -> EarlySignal | None:
    if facts.is_bank:
        return None
    flows = _same_window(facts.snap, "stock_based_compensation", "revenue")
    if flows is None:
        return None
    (stock_pay, revenue), window = flows
    if revenue <= 0 or stock_pay / revenue < STOCK_PAY_ALERT_SHARE_OF_REVENUE:
        return None
    share = stock_pay / revenue
    free_cash_flow = _flow(facts.snap, "free_cash_flow", window)
    exceeds = (
        " — and it exceeds free cash flow, which counts none of it"
        if free_cash_flow is not None and 0 < free_cash_flow <= stock_pay
        else " — a real cost that cash-flow figures leave out"
    )
    return _concern(
        EarlySignalKind.STOCK_PAY_HEAVY,
        Severity.URGENT if share >= STOCK_PAY_URGENT_SHARE_OF_REVENUE else Severity.ALERT,
        f"Stock-based pay was {share * 100:.0f}% of revenue over the {window.label}{exceeds}.",
    )


# ── Momentum ─────────────────────────────────────────────────────────────────


def _revenue_momentum(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    growth, pace = snap.latest_yoy_revenue_growth, snap.rev_3y_cagr
    # A lender's "revenue" is interest income: it moves with rates and
    # mergers, which is why the bank rubric leaves revenue growth out too.
    if facts.is_bank or growth is None:
        return None
    if growth <= REVENUE_SHRINKING_PCT:
        return _concern(
            EarlySignalKind.REVENUE_SHRINKING,
            Severity.URGENT if growth <= REVENUE_SHRINKING_URGENT_PCT else Severity.ALERT,
            f"Revenue fell {-growth:.0f}% year on year in the latest quarter.",
        )
    if pace is None:
        return None
    if pace >= GROWER_MIN_CAGR_PCT and growth <= pace - REVENUE_PACE_GAP_PTS:
        return _concern(
            EarlySignalKind.REVENUE_DECELERATING,
            Severity.ALERT,
            f"Revenue growth slowed to {growth:+.0f}% year on year in the latest quarter, "
            f"from a {pace:.0f}% three-year pace.",
        )
    if growth >= GROWER_MIN_CAGR_PCT and growth >= pace + REVENUE_PACE_GAP_PTS:
        return _strength(
            EarlySignalKind.REVENUE_ACCELERATING,
            Severity.ALERT,
            f"Revenue growth picked up to {growth:+.0f}% year on year in the latest quarter, "
            f"against a {pace:.0f}% three-year pace.",
        )
    return None


def _operating_margin_shift(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    if facts.is_bank:
        return None
    pair = change_by_date(snap.quarterly_operating_margin, snap.quarterly_dates)
    if pair is None:
        return None
    now, year_ago = pair
    shift = now - year_ago
    if abs(shift) < MARGIN_SHIFT_PTS:
        return None
    severity = Severity.URGENT if shift <= -MARGIN_SHIFT_URGENT_PTS else Severity.ALERT
    if shift < 0:
        return _concern(
            EarlySignalKind.MARGIN_COMPRESSING,
            severity,
            f"Operating margin fell to {now:.1f}% in the latest quarter from "
            f"{year_ago:.1f}% a year earlier.",
        )
    return _strength(
        EarlySignalKind.MARGIN_EXPANDING,
        Severity.ALERT,
        f"Operating margin rose to {now:.1f}% in the latest quarter from "
        f"{year_ago:.1f}% a year earlier.",
    )


def _profit_against_sales(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    profit, sales = snap.latest_yoy_pat_growth, snap.latest_yoy_revenue_growth
    if facts.is_bank or profit is None or sales is None or sales < SALES_GROWING_PCT:
        return None
    if profit <= PROFIT_DROP_PCT:
        return _concern(
            EarlySignalKind.PROFIT_FALLING_AS_SALES_GROW,
            Severity.ALERT,
            f"Net profit fell {-profit:.0f}% year on year in the latest quarter "
            f"while revenue grew {sales:.0f}%.",
        )
    # Operating leverage shows in the operating margin. A profit jump beside a
    # margin that did not widen is a weak year-ago quarter (a one-off charge
    # then), and one made of one-off gains now is not leverage either.
    margins = change_by_date(snap.quarterly_operating_margin, snap.quarterly_dates)
    scaling = margins is not None and margins[0] > margins[1]
    genuine = facts.earnings.core_ratio >= ONE_OFF_ALERT_CORE_RATIO
    if scaling and genuine and profit >= PROFIT_RISE_PCT and profit >= sales + PROFIT_LEAD_PTS:
        return _strength(
            EarlySignalKind.PROFIT_OUTPACING_SALES,
            Severity.INFO,
            f"Net profit grew {profit:.0f}% year on year in the latest quarter on "
            f"{sales:.0f}% revenue growth — profit is scaling faster than sales.",
        )
    return None


# ── Balance sheet ────────────────────────────────────────────────────────────


def _debt_shift(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    change, now, year_ago = snap.debt_change_1y_pct, snap.latest_debt_to_equity, snap.debt_to_equity_1y_ago
    if facts.is_bank or change is None or now is None or year_ago is None:
        return None
    path = f"debt / equity {year_ago:.2f} → {now:.2f}"
    if change >= DEBT_RISE_PCT and now >= MATERIAL_DEBT_TO_EQUITY:
        heavy = change >= DEBT_RISE_URGENT_PCT and now >= HEAVY_DEBT_TO_EQUITY
        return _concern(
            EarlySignalKind.DEBT_RISING,
            Severity.URGENT if heavy else Severity.ALERT,
            f"Total debt rose {change:.0f}% in a year ({path}).",
        )
    if change <= DEBT_FALL_PCT and year_ago >= MATERIAL_DEBT_TO_EQUITY:
        return _strength(
            EarlySignalKind.DEBT_FALLING,
            Severity.ALERT,
            f"Total debt fell {-change:.0f}% in a year ({path}).",
        )
    return None


def _negative_equity(facts: _Facts) -> EarlySignal | None:
    if not has_negative_equity(facts.snap):
        return None
    profit, _ = facts.snap.trailing("net_income")
    losing = profit is not None and profit < 0
    return _concern(
        EarlySignalKind.NEGATIVE_EQUITY,
        Severity.URGENT if losing else Severity.ALERT,
        "Shareholders' equity is negative — liabilities exceed assets"
        + (", and the business is loss-making." if losing else "."),
    )


def _receivables_outpacing_sales(facts: _Facts) -> EarlySignal | None:
    change = facts.snap.receivables_change_1y_pct
    sales = _sales_growth_at_balance_sheet(facts)
    if not _material_days(facts.snap.debtor_days):
        return None
    if change is None or sales is None or not _outpaces(change, sales):
        return None
    return _concern(
        EarlySignalKind.RECEIVABLES_OUTPACING_SALES,
        Severity.ALERT,
        f"Receivables grew {change:.0f}% in a year against {sales:+.0f}% revenue growth — "
        f"customers are paying later, or sales are being pulled forward.",
    )


def _inventory_outpacing_sales(facts: _Facts) -> EarlySignal | None:
    change = facts.snap.inventory_change_1y_pct
    sales = _sales_growth_at_balance_sheet(facts)
    if not _material_days(facts.snap.inventory_days):
        return None
    if change is None or sales is None or not _outpaces(change, sales):
        return None
    return _concern(
        EarlySignalKind.INVENTORY_OUTPACING_SALES,
        Severity.ALERT,
        f"Inventory grew {change:.0f}% in a year against {sales:+.0f}% revenue growth — "
        f"stock is building faster than it sells.",
    )


def _goodwill_jump(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    change, share = snap.goodwill_change_1y_pct, snap.latest_goodwill_to_equity
    if change is None or share is None:
        return None
    if change < GOODWILL_RISE_PCT or share < MATERIAL_GOODWILL_TO_EQUITY:
        return None
    return _concern(
        EarlySignalKind.GOODWILL_JUMP,
        Severity.ALERT if share >= HEAVY_GOODWILL_TO_EQUITY else Severity.INFO,
        f"Goodwill and intangibles grew {change:.0f}% in a year, to {share * 100:.0f}% of "
        f"equity — a sizeable acquisition still to be integrated.",
    )


# ── Ownership ────────────────────────────────────────────────────────────────


def _share_count_shift(facts: _Facts) -> EarlySignal | None:
    change = facts.snap.share_count_change_1y_pct
    if change is None:
        return None
    if change >= DILUTION_PCT:
        return _concern(
            EarlySignalKind.SHARE_DILUTION,
            Severity.URGENT if change >= DILUTION_URGENT_PCT else Severity.ALERT,
            f"Share count grew {change:.1f}% in a year — existing holders are being diluted.",
        )
    if change <= BUYBACK_PCT:
        return _strength(
            EarlySignalKind.SHARE_BUYBACK,
            Severity.ALERT,
            f"Share count fell {-change:.1f}% in a year — buybacks are shrinking the share base.",
        )
    return None


def _insider_flow(facts: _Facts) -> EarlySignal | None:
    shift = facts.snap.insider_delta_1y_pct_points
    if shift is None or abs(shift) < INSIDER_SHIFT_PTS:
        return None
    if shift < 0:
        return _concern(
            EarlySignalKind.INSIDER_SELLING,
            Severity.URGENT if shift <= INSIDER_SELLING_URGENT_PTS else Severity.ALERT,
            f"Insider / promoter stake fell {-shift:.2f} points in a year.",
        )
    return _strength(
        EarlySignalKind.INSIDER_BUYING,
        Severity.ALERT,
        f"Insider / promoter stake rose {shift:.2f} points in a year.",
    )


def _foreign_institution_flow(facts: _Facts) -> EarlySignal | None:
    shift = facts.snap.fii_delta_1y_pct_points
    if shift is None or abs(shift) < FOREIGN_SHIFT_PTS:
        return None
    if shift < 0:
        return _concern(
            EarlySignalKind.FOREIGN_INSTITUTIONS_SELLING,
            Severity.ALERT if shift <= FOREIGN_SELLING_ALERT_PTS else Severity.INFO,
            f"Foreign institutions cut their holding by {-shift:.2f} points in a year.",
        )
    return _strength(
        EarlySignalKind.FOREIGN_INSTITUTIONS_BUYING,
        Severity.INFO,
        f"Foreign institutions raised their holding by {shift:.2f} points in a year.",
    )


def _retail_crowding(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    holders, foreign = snap.num_shareholders_delta_1y_pct, snap.fii_delta_1y_pct_points
    if holders is None or foreign is None:
        return None
    if holders < RETAIL_CROWDING_PCT or foreign > RETAIL_CROWDING_FOREIGN_PTS:
        return None
    return _concern(
        EarlySignalKind.RETAIL_CROWDING,
        Severity.ALERT,
        f"Retail shareholder count is up {holders:.0f}% in a year while foreign institutions "
        f"cut {-foreign:.2f} points — retail is absorbing institutional selling.",
    )


# ── Lenders ──────────────────────────────────────────────────────────────────


def _asset_quality_shift(facts: _Facts) -> EarlySignal | None:
    snap = facts.snap
    shift, level = snap.gross_npa_delta_1y_pct_points, snap.gross_npa_pct
    if shift is None or level is None:
        return None
    if shift >= NPA_WORSENING_PTS:
        return _concern(
            EarlySignalKind.ASSET_QUALITY_WORSENING,
            Severity.URGENT if shift >= NPA_WORSENING_URGENT_PTS else Severity.ALERT,
            f"Gross NPA rose {shift:.2f} points in a year, to {level:.2f}% of advances.",
        )
    if shift <= NPA_IMPROVING_PTS:
        return _strength(
            EarlySignalKind.ASSET_QUALITY_IMPROVING,
            Severity.ALERT,
            f"Gross NPA fell {-shift:.2f} points in a year, to {level:.2f}% of advances.",
        )
    return None


# ── News radar ───────────────────────────────────────────────────────────────


def _governance_news(facts: _Facts) -> EarlySignal | None:
    news = facts.news
    entry = news.categories.get(NewsCategory.GOVERNANCE) if news else None
    if news is None or entry is None:
        return None
    share = entry.stories / news.stories if news.stories else 0.0

    def takes_over(stories: int, of_the_week: float) -> bool:
        return entry.stories >= stories and share >= of_the_week

    burst = (
        takes_over(GOVERNANCE_STORIES, GOVERNANCE_SHARE)
        or takes_over(THIN_COVERAGE_STORIES, THIN_COVERAGE_SHARE)
        or entry.widest_coverage >= WIDE_COVERAGE_OUTLETS
    )
    if not burst:
        return None
    urgent = (
        takes_over(GOVERNANCE_URGENT_STORIES, GOVERNANCE_URGENT_SHARE)
        or takes_over(THIN_COVERAGE_URGENT_STORIES, THIN_COVERAGE_URGENT_SHARE)
        or entry.widest_coverage >= WIDE_COVERAGE_URGENT_OUTLETS
    )
    return _concern(
        EarlySignalKind.GOVERNANCE_NEWS,
        Severity.URGENT if urgent else Severity.ALERT,
        f"{entry.stories} of the last {news.window_days} days' {news.stories} "
        f"news stories are governance-tagged — e.g. “{entry.widest_headline}”. "
        f"Unjudged: read them before trusting the leadership pillar.",
    )


def _credit_rating_news(facts: _Facts) -> EarlySignal | None:
    news = facts.news
    entry = news.categories.get(NewsCategory.RATING) if news else None
    if news is None or entry is None:
        return None
    return _concern(
        EarlySignalKind.CREDIT_RATING_NEWS,
        Severity.ALERT,
        f"Credit-rating news in the last {news.window_days} days — "
        f"“{entry.widest_headline}”. Check which way the action went.",
    )


# ── Tape and data ────────────────────────────────────────────────────────────


def _unproven_profit(facts: _Facts) -> EarlySignal | None:
    """Profitable now, but in loss for half or more of the years on record.

    The fundamentals rubric reads the latest audited year, so one good year
    scores like ten. The record says how much of that has been shown before.
    """
    record = risk_profile(facts.snap)
    profit = facts.snap.net_income
    if profit is None or profit <= 0 or record.fiscal_years < MIN_YEARS_FOR_RECORD:
        return None
    if record.loss_years * 2 < record.fiscal_years:
        return None
    return _concern(
        EarlySignalKind.UNPROVEN_PROFIT,
        Severity.ALERT,
        f"Profitable in the latest year, but {record.loss_years} of the {record.fiscal_years} "
        f"fiscal years on record ended in a loss. The fundamentals score reads the latest year "
        f"only — this profit has not yet been held through a cycle.",
    )


def _priced_for_doubt(facts: _Facts) -> EarlySignal | None:
    """A profitable company at a handful of times earnings: the market doubts something."""
    pe = facts.snap.pe_ratio
    if pe is None or pe <= 0 or pe >= DOUBT_PE:
        return None
    return _concern(
        EarlySignalKind.PRICED_FOR_DOUBT,
        Severity.ALERT,
        f"The price is {pe:.1f}× earnings. A profit this cheap usually means the market doubts "
        f"it will last, doubts the accounts, or cannot get its money out — or the data is "
        f"wrong. The valuation score is capped until the reason is known: find it first.",
    )


def _price_drawdown(facts: _Facts) -> EarlySignal | None:
    price, high = facts.snap.current_price, facts.snap.high_52w
    if price is None or not high or high <= 0:
        return None
    drawdown = (1 - price / high) * 100
    if drawdown < DRAWDOWN_INFO_PCT:
        return None
    return _concern(
        EarlySignalKind.PRICE_DRAWDOWN,
        Severity.ALERT if drawdown >= DRAWDOWN_ALERT_PCT else Severity.INFO,
        f"The price is {drawdown:.0f}% below its 52-week high — find out what the market is "
        f"reacting to before trusting the statements.",
    )


def _stale_statements(facts: _Facts) -> EarlySignal | None:
    months = months_since(facts.snap.latest_annual_period, today=facts.today)
    if months is None or months <= STALE_ANNUAL_MONTHS:
        return None
    return _concern(
        EarlySignalKind.STALE_STATEMENTS,
        Severity.ALERT,
        f"The latest annual statements are {months} months old — the scores may predate "
        f"a material change.",
    )


def _recently_listed(facts: _Facts) -> EarlySignal | None:
    years = facts.snap.price_history_years
    if years is None or years >= RECENT_LISTING_YEARS:
        return None
    months = max(1, round(years * 12))
    return _concern(
        EarlySignalKind.RECENTLY_LISTED,
        Severity.ALERT,
        f"Listed about {months} month{'s' if months != 1 else ''} ago: the 52-week range, the "
        f"measured volatility and the worst fall cover only that period, and there is no P/E "
        f"history to compare with. Insiders' lock-up periods usually end within the first "
        f"year — check the prospectus for the date.",
    )


def _unreliable_quote(facts: _Facts) -> EarlySignal | None:
    if not facts.snap.quote_unreliable:
        return None
    return _concern(
        EarlySignalKind.UNRELIABLE_QUOTE,
        Severity.URGENT,
        "This quote's price and the statements don't reconcile — it may be a depositary "
        "receipt, or carry a mislabelled statement currency. Valuation is unscored: check "
        "the company's primary listing.",
    )


_DETECTORS: tuple[_Detector, ...] = (
    _unreliable_quote,
    _one_off_profit,
    _peak_margin,
    _free_cash_flow,
    _capex_outruns_cash_flow,
    _stock_pay_heavy,
    _revenue_momentum,
    _operating_margin_shift,
    _profit_against_sales,
    _debt_shift,
    _negative_equity,
    _receivables_outpacing_sales,
    _inventory_outpacing_sales,
    _goodwill_jump,
    _share_count_shift,
    _insider_flow,
    _foreign_institution_flow,
    _retail_crowding,
    _asset_quality_shift,
    _governance_news,
    _credit_rating_news,
    _unproven_profit,
    _priced_for_doubt,
    _price_drawdown,
    _stale_statements,
    _recently_listed,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _concern(kind: EarlySignalKind, severity: Severity, message: str) -> EarlySignal:
    return EarlySignal(kind=kind, tone=Tone.CONCERN, severity=severity, message=message)


def _strength(kind: EarlySignalKind, severity: Severity, message: str) -> EarlySignal:
    return EarlySignal(kind=kind, tone=Tone.STRENGTH, severity=severity, message=message)


def _earnings_window(snap: BalanceSheetSnapshot) -> str:
    """The twelve months the core-earnings ratio covers, in words."""
    if snap.earnings_window is not StatementWindow.FISCAL_YEAR and (
        snap.core_earnings_ratio_ttm is not None and snap.ttm_is_current
    ):
        return StatementWindow.TTM.label
    return StatementWindow.FISCAL_YEAR.label


def _flow(snap: BalanceSheetSnapshot, name: str, window: StatementWindow) -> float | None:
    """A flow line for exactly `window` — never the other one as a stand-in."""
    value = getattr(snap, f"ttm_{name}" if window is StatementWindow.TTM else name)
    return value if isinstance(value, (int, float)) else None


def _same_window(
    snap: BalanceSheetSnapshot, first: str, second: str
) -> tuple[tuple[float, float], StatementWindow] | None:
    """Two flow lines over the same twelve months: trailing if both exist, else fiscal year."""
    windows = (StatementWindow.TTM, StatementWindow.FISCAL_YEAR) if snap.ttm_is_current else (
        StatementWindow.FISCAL_YEAR,
    )
    for window in windows:
        a, b = _flow(snap, first, window), _flow(snap, second, window)
        if a is not None and b is not None:
            return (a, b), window
    return None


def _sales_growth_at_balance_sheet(facts: _Facts) -> float | None:
    """Latest-quarter revenue growth, if that quarter ends on the balance sheet's date."""
    snap = facts.snap
    if facts.is_bank or not snap.quarterly_dates:
        return None
    if snap.quarterly_dates[0] != snap.balance_sheet_date:
        return None
    return snap.latest_yoy_revenue_growth


def _material_days(days: float | None) -> bool:
    """Is this working-capital line big enough to matter? Unknown size means no."""
    return days is not None and days >= MATERIAL_WORKING_CAPITAL_DAYS


def _outpaces(change_pct: float, sales_growth_pct: float) -> bool:
    """A balance that grew materially, and faster than the sales it sits against."""
    sales_factor = 1 + sales_growth_pct / 100
    if change_pct < WORKING_CAPITAL_RISE_PCT or sales_factor <= 0:
        return False
    days_growth_pct = ((1 + change_pct / 100) / sales_factor - 1) * 100
    return days_growth_pct >= WORKING_CAPITAL_DAYS_RISE_PCT

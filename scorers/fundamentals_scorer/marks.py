"""Marks — the fundamentals rubric's own thresholds, as questions a screener can answer.

Nobody can run the rubric on 80,000 listings, but a data provider can be
asked "which of these have a return on equity of at least 15%?" across all
of them at once. For each rubric input a screener can filter on, a `Mark`
holds the two values worth asking about: where the input's scoring curve
reaches 80 out of 100 (`good`) and where it tops out (`excellent`). For
growth they are the veto line and the pace the curve scores 70.

A company's *preview* is the share of its marks it clears, weighted like
the rubric (`fundamental_engine.preview`). It is how a search stays fair
to every size of company: the shortcut it replaces — score the largest
few — never looks at four companies in five.

Why this shape, measured on two industries scored in full (113 chip makers
and 90 aerospace and defence companies, November 2026):
  - The largest twelve held 3 of the ten best chip makers; five of the six
    best were not large caps.
  - No single ratio, in any order, found the best better than chance.
  - Requiring every mark at once lost four of the ten best in aerospace:
    the rubric averages, so a company can miss one mark and still score 95.
  - Counting marks found 9 of the ten best among the first 25 in both
    industries, from every size band.

A preview promises nothing about the score — the rubric also reads
coverage, cash conversion and ownership, on audited fiscal years, where a
screener sees trailing figures. It only chooses who gets scored.

Lenders have marks of their own: a bank's good return on assets is 1.5%,
not 8%, and debt / equity and a current ratio don't describe its balance
sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

from data_sources.universe_source import Measure


@dataclass(frozen=True, slots=True)
class Mark:
    """One rubric input, with the two levels a preview asks about."""

    measure: Measure
    weight: float  # the input's share of the rubric, in points out of 100
    good: float
    excellent: float
    higher_is_better: bool = True


# Read off the curves in `industrial.py`. Profitability is 20 points over
# three returns; the balance sheet is 35, of which D/E is 30% and the current
# and quick ratios 15% each; growth is 15 over revenue and profit.
INDUSTRIAL_MARKS: tuple[Mark, ...] = (
    Mark(Measure.RETURN_ON_EQUITY, weight=20 / 3, good=15.0, excellent=20.0),
    Mark(Measure.RETURN_ON_CAPITAL, weight=20 / 3, good=15.0, excellent=20.0),
    Mark(Measure.RETURN_ON_ASSETS, weight=20 / 3, good=8.0, excellent=12.0),
    Mark(Measure.DEBT_TO_EQUITY, weight=35 * 0.30, good=0.5, excellent=0.3, higher_is_better=False),
    Mark(Measure.CURRENT_RATIO, weight=35 * 0.15, good=1.5, excellent=2.0),
    Mark(Measure.QUICK_RATIO, weight=35 * 0.15, good=1.0, excellent=1.5),
    Mark(Measure.REVENUE_GROWTH, weight=15 / 2, good=7.0, excellent=12.0),
    Mark(Measure.PROFIT_GROWTH, weight=15 / 2, good=7.0, excellent=12.0),
)

# Read off the curves in `bank.py`: profitability is 30 points (ROE and ROA
# here; net interest margin can't be screened), growth is 20 on profit alone.
LENDER_MARKS: tuple[Mark, ...] = (
    Mark(Measure.RETURN_ON_EQUITY, weight=15.0, good=15.0, excellent=18.0),
    Mark(Measure.RETURN_ON_ASSETS, weight=15.0, good=1.5, excellent=1.8),
    Mark(Measure.PROFIT_GROWTH, weight=20.0, good=7.0, excellent=12.0),
)

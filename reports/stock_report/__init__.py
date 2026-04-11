"""`analyse <TICKER>` report renderer — Rich terminal + Markdown mirror.

Public API:
    render_stock_report(snap, score, *, output_dir=None, console=None)

Internal layout:
    render.py              — public entry + markdown writer
    header.py              — header panel + valuation + analyst bundle
    financial_profile.py   — Financial Profile panel dispatcher + variants
    trends.py              — multi-year, shareholding, quarterly trend rows

Callers import from `reports.stock_report` (this package) — submodules
are internal and only the renderer, the header helpers, and the profile
rows are reached into by the markdown path.
"""

from reports.stock_report.render import PILLAR_WEIGHTS_PCT, render_stock_report

__all__ = ["PILLAR_WEIGHTS_PCT", "render_stock_report"]

"""CLI command handlers.

These are vertical-slice orchestrators — each module owns one CLI
sub-command end-to-end: load cached data, filter, emit signals, and
render the result. They sit one layer above pure presentation
(`reports/`) and pure scoring (`scorers/`) and are what `__main__.py`
dispatches to.
"""

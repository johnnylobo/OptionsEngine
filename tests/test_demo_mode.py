from __future__ import annotations

from options_income_engine.demo import demo_holdings


def test_demo_holdings_include_core_easy_mode_names() -> None:
    holdings = demo_holdings()
    tickers = {holding.ticker for holding in holdings}

    assert {"TQQQ", "NVDA", "GOOG", "AMZN", "MU", "AEHR", "CRWD", "IREN"}.issubset(tickers)
    assert all(holding.shares > 0 for holding in holdings)

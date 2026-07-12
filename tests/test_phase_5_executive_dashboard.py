from __future__ import annotations

from dataclasses import replace
from datetime import date

from options_income_engine.dashboard import (
    aggregate_risk_alerts,
    build_dashboard_summary,
    calculate_income_forecasts,
    filter_candidates,
    select_best_opportunities,
)
from options_income_engine.models import Candidate, HoldingExposure, PortfolioSummary


EXPIRATION = date(2026, 7, 17)


BASE_CANDIDATE = Candidate(
    rank=0,
    ticker="AAA",
    strategy="Covered Call",
    expiration=EXPIRATION,
    strike=100.0,
    current_price=95.0,
    bid=1.0,
    ask=1.1,
    mid=1.05,
    delta=0.07,
    iv_rank=0.50,
    assignment_probability=0.07,
    premium_efficiency_score=0.50,
    premium_per_contract=105.0,
    total_premium=100.0,
    shares_covered=100,
    cash_required=0.0,
    capital_at_risk=10_000.0,
    assignment_outcome="May sell 100 shares at $100.00 strike.",
    effective_entry_price=None,
    percent_otm=0.05,
    weekly_yield=0.01,
    annualized_yield=0.52,
    liquidity_warning="",
    earnings_warning="",
    recommendation="Sell",
    suggested_limit_price=1.05,
    tier="Custom",
    category="Core Compounder",
    own_more_score=3,
    happy_to_sell_score=3,
    max_contracts=None,
    profile_notes="",
    preference_adjustment=1.0,
    current_ticker_exposure=10_000.0,
    current_category_exposure=20_000.0,
    current_ticker_weight=0.20,
    current_category_weight=0.40,
    additional_exposure_if_assigned=0.0,
    maximum_exposure_after_assignment=10_000.0,
    post_assignment_ticker_weight=0.20,
    post_assignment_category_weight=0.40,
    cash_used_if_assigned=0.0,
    shares_remaining_if_called_away=0,
    market_value_at_risk_if_called_away=9_500.0,
    category_exposure_change_if_assigned=0.0,
    portfolio_risk_alerts="",
    portfolio_risk_adjustment=1.0,
    score=80.0,
    contracts=1,
)


def candidate(**overrides) -> Candidate:
    values = dict(overrides)
    if values.get("strategy") == "Cash-Secured Put":
        values.setdefault("delta", -0.07)
        values.setdefault("shares_covered", 0)
        values.setdefault("cash_required", 5_000.0)
        values.setdefault("cash_used_if_assigned", values["cash_required"])
        values.setdefault("effective_entry_price", 98.95)
        values.setdefault("assignment_outcome", "May buy 100 shares at $100.00 strike; effective entry $98.95.")
        values.setdefault("shares_remaining_if_called_away", 100)
        values.setdefault("market_value_at_risk_if_called_away", 0.0)
    return replace(BASE_CANDIDATE, **values)


def portfolio() -> PortfolioSummary:
    largest = HoldingExposure("AAA", 100, 100.0, 10_000.0, "Core Compounder", 0.25)
    return PortfolioSummary(
        total_portfolio_market_value=30_000.0,
        cash_balance=10_000.0,
        total_account_value=40_000.0,
        holdings=[largest],
        category_exposure={"Core Compounder": 0.45, "Cash": 0.25},
        ticker_exposure={"AAA": 0.25},
        top_ticker_exposures=[largest],
        largest_single_name=largest,
        largest_category=("Core Compounder", 0.45),
    )


def test_dashboard_summary_metrics() -> None:
    candidates = [
        candidate(ticker="AAA", strategy="Covered Call", recommendation="Sell", premium_efficiency_score=0.40, total_premium=120.0),
        candidate(ticker="BBB", strategy="Cash-Secured Put", recommendation="Sell", premium_efficiency_score=0.35, total_premium=90.0, cash_required=9_500.0),
        candidate(ticker="CCC", strategy="Cash-Secured Put", recommendation="Maybe", premium_efficiency_score=0.25, total_premium=50.0, cash_required=4_500.0),
    ]

    summary = build_dashboard_summary(candidates, portfolio())

    assert summary.total_portfolio_value == 40_000.0
    assert summary.cash_available == 10_000.0
    assert summary.candidate_count == 3
    assert summary.sell_recommendation_count == 2
    assert summary.best_covered_call_efficiency == 0.40
    assert summary.best_cash_secured_put_efficiency == 0.35
    assert summary.sell_rated_premium_available == 210.0
    assert summary.sell_rated_put_cash_required == 9_500.0


def test_best_opportunity_selection_prefers_recommendation_then_efficiency() -> None:
    candidates = [
        candidate(ticker="AAA", strategy="Covered Call", recommendation="Maybe", premium_efficiency_score=0.90),
        candidate(ticker="BBB", strategy="Covered Call", recommendation="Sell", premium_efficiency_score=0.50),
        candidate(ticker="CCC", strategy="Cash-Secured Put", recommendation="Sell", premium_efficiency_score=0.60),
    ]

    best = select_best_opportunities(candidates)

    assert best.best_covered_call and best.best_covered_call.ticker == "BBB"
    assert best.best_cash_secured_put and best.best_cash_secured_put.ticker == "CCC"
    assert best.best_overall_trade and best.best_overall_trade.ticker == "CCC"


def test_income_forecast_calculations() -> None:
    candidates = [
        candidate(ticker="AAA", strategy="Covered Call", recommendation="Sell", premium_efficiency_score=0.50, total_premium=100.0, assignment_probability=0.05, shares_covered=100),
        candidate(ticker="BBB", strategy="Cash-Secured Put", recommendation="Sell", premium_efficiency_score=0.40, total_premium=80.0, assignment_probability=0.10, cash_required=5_000.0),
        candidate(ticker="CCC", strategy="Covered Call", recommendation="Sell", premium_efficiency_score=0.30, total_premium=60.0, assignment_probability=0.15, shares_covered=200),
        candidate(ticker="DDD", strategy="Cash-Secured Put", recommendation="Sell", premium_efficiency_score=0.20, total_premium=40.0, assignment_probability=0.20, cash_required=3_000.0),
        candidate(ticker="EEE", strategy="Cash-Secured Put", recommendation="Maybe", premium_efficiency_score=0.10, total_premium=20.0, assignment_probability=0.25, cash_required=2_000.0),
    ]

    forecasts = calculate_income_forecasts(candidates)

    assert forecasts[0].label == "Conservative"
    assert forecasts[0].total_premium == 240.0
    assert forecasts[0].cash_required_for_puts == 5_000.0
    assert forecasts[0].shares_covered_for_calls == 300
    assert forecasts[0].average_assignment_probability == 0.10
    assert forecasts[1].label == "Expected"
    assert forecasts[1].total_premium == 280.0
    assert forecasts[1].cash_required_for_puts == 8_000.0
    assert forecasts[2].label == "Aggressive"
    assert forecasts[2].total_premium == 300.0
    assert forecasts[2].cash_required_for_puts == 10_000.0


def test_risk_alert_aggregation() -> None:
    candidates = [
        candidate(
            ticker="AAA",
            strategy="Covered Call",
            recommendation="Sell",
            liquidity_warning="Wide spread 35%",
            portfolio_risk_alerts="Selling call would materially reduce exposure to a core compounder",
        )
    ]

    alerts = aggregate_risk_alerts(candidates, portfolio())

    assert "High single-name exposure: AAA 25.0%" in alerts
    assert "High category concentration: Core Compounder 45.0%" in alerts
    assert "AAA: Wide spread 35%" in alerts
    assert "AAA: Selling call would materially reduce exposure to a core compounder" in alerts


def test_candidate_filtering() -> None:
    candidates = [
        candidate(ticker="AAA", strategy="Covered Call", recommendation="Sell", premium_efficiency_score=0.50),
        candidate(ticker="BBB", strategy="Cash-Secured Put", recommendation="Maybe", premium_efficiency_score=0.25),
        candidate(ticker="AAB", strategy="Cash-Secured Put", recommendation="Skip", premium_efficiency_score=0.10),
    ]

    assert [item.ticker for item in filter_candidates(candidates, strategy="Covered Calls")] == ["AAA"]
    assert [item.ticker for item in filter_candidates(candidates, recommendation="Maybe")] == ["BBB"]
    assert [item.ticker for item in filter_candidates(candidates, ticker_search="AA")] == ["AAA", "AAB"]
    assert [item.ticker for item in filter_candidates(candidates, min_premium_efficiency_score=0.20)] == ["AAA", "BBB"]

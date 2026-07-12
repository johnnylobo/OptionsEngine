from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Candidate, PortfolioSummary, Recommendation


@dataclass(frozen=True)
class DashboardSummary:
    total_portfolio_value: float
    cash_available: float
    candidate_count: int
    sell_recommendation_count: int
    best_covered_call_efficiency: float
    best_cash_secured_put_efficiency: float
    sell_rated_premium_available: float
    sell_rated_put_cash_required: float


@dataclass(frozen=True)
class BestOpportunities:
    best_covered_call: Optional[Candidate]
    best_cash_secured_put: Optional[Candidate]
    best_overall_trade: Optional[Candidate]


@dataclass(frozen=True)
class IncomeForecast:
    label: str
    total_premium: float
    cash_required_for_puts: float
    shares_covered_for_calls: int
    average_assignment_probability: float


def build_dashboard_summary(candidates: list[Candidate], portfolio: PortfolioSummary) -> DashboardSummary:
    sell_candidates = _recommendation_candidates(candidates, {"Sell"})
    covered_calls = [candidate for candidate in candidates if candidate.strategy == "Covered Call"]
    cash_secured_puts = [candidate for candidate in candidates if candidate.strategy == "Cash-Secured Put"]
    sell_puts = [candidate for candidate in sell_candidates if candidate.strategy == "Cash-Secured Put"]

    return DashboardSummary(
        total_portfolio_value=portfolio.total_account_value,
        cash_available=portfolio.cash_balance,
        candidate_count=len(candidates),
        sell_recommendation_count=len(sell_candidates),
        best_covered_call_efficiency=_best_efficiency(covered_calls),
        best_cash_secured_put_efficiency=_best_efficiency(cash_secured_puts),
        sell_rated_premium_available=round(sum(candidate.total_premium for candidate in sell_candidates), 2),
        sell_rated_put_cash_required=round(sum(candidate.cash_required for candidate in sell_puts), 2),
    )


def select_best_opportunities(candidates: list[Candidate]) -> BestOpportunities:
    return BestOpportunities(
        best_covered_call=_best_candidate([candidate for candidate in candidates if candidate.strategy == "Covered Call"]),
        best_cash_secured_put=_best_candidate(
            [candidate for candidate in candidates if candidate.strategy == "Cash-Secured Put"]
        ),
        best_overall_trade=_best_candidate(candidates),
    )


def calculate_income_forecasts(candidates: list[Candidate]) -> list[IncomeForecast]:
    sell_candidates = _sorted_by_efficiency(_recommendation_candidates(candidates, {"Sell"}))
    maybe_candidates = _recommendation_candidates(candidates, {"Maybe"})

    return [
        _forecast("Conservative", sell_candidates[:3]),
        _forecast("Expected", sell_candidates),
        _forecast("Aggressive", _sorted_by_efficiency(sell_candidates + maybe_candidates)),
    ]


def aggregate_risk_alerts(candidates: list[Candidate], portfolio: PortfolioSummary) -> list[str]:
    alerts: set[str] = set()

    if portfolio.largest_single_name and portfolio.largest_single_name.portfolio_weight > 0.20:
        alerts.add(
            f"High single-name exposure: {portfolio.largest_single_name.ticker} "
            f"{portfolio.largest_single_name.portfolio_weight:.1%}"
        )
    if portfolio.largest_category and portfolio.largest_category[1] > 0.40:
        alerts.add(f"High category concentration: {portfolio.largest_category[0]} {portfolio.largest_category[1]:.1%}")

    for candidate in candidates:
        for alert in _split_alerts(candidate.portfolio_risk_alerts):
            alerts.add(f"{candidate.ticker}: {alert}")
        if candidate.liquidity_warning:
            for alert in _split_alerts(candidate.liquidity_warning):
                alerts.add(f"{candidate.ticker}: {alert}")
        if candidate.earnings_warning:
            alerts.add(f"{candidate.ticker}: {candidate.earnings_warning}")

    return sorted(alerts)


def filter_candidates(
    candidates: list[Candidate],
    *,
    strategy: str = "All",
    recommendation: str = "All",
    ticker_search: str = "",
    min_premium_efficiency_score: float = 0.0,
) -> list[Candidate]:
    ticker_query = ticker_search.strip().upper()
    filtered = candidates

    if strategy == "Covered Calls":
        filtered = [candidate for candidate in filtered if candidate.strategy == "Covered Call"]
    elif strategy == "Cash-Secured Puts":
        filtered = [candidate for candidate in filtered if candidate.strategy == "Cash-Secured Put"]

    if recommendation != "All":
        filtered = [candidate for candidate in filtered if candidate.recommendation == recommendation]

    if ticker_query:
        filtered = [candidate for candidate in filtered if ticker_query in candidate.ticker]

    return [
        candidate
        for candidate in filtered
        if candidate.premium_efficiency_score >= min_premium_efficiency_score
    ]


def explain_candidate(candidate: Candidate) -> str:
    parts = [
        f"premium efficiency {candidate.premium_efficiency_score:.4f}",
        f"${candidate.total_premium:,.2f} premium",
        f"{candidate.assignment_probability:.1%} assignment probability",
    ]
    if candidate.portfolio_risk_alerts:
        parts.append("portfolio alerts present")
    else:
        parts.append("no portfolio alerts")
    return f"Ranks highly on {', '.join(parts)}."


def _best_candidate(candidates: list[Candidate]) -> Optional[Candidate]:
    ranked = _sorted_by_recommendation_then_efficiency(candidates)
    return ranked[0] if ranked else None


def _best_efficiency(candidates: list[Candidate]) -> float:
    if not candidates:
        return 0.0
    return round(max(candidate.premium_efficiency_score for candidate in candidates), 6)


def _forecast(label: str, candidates: list[Candidate]) -> IncomeForecast:
    average_assignment_probability = (
        sum(candidate.assignment_probability for candidate in candidates) / len(candidates)
        if candidates
        else 0.0
    )
    return IncomeForecast(
        label=label,
        total_premium=round(sum(candidate.total_premium for candidate in candidates), 2),
        cash_required_for_puts=round(
            sum(candidate.cash_required for candidate in candidates if candidate.strategy == "Cash-Secured Put"),
            2,
        ),
        shares_covered_for_calls=sum(
            candidate.shares_covered for candidate in candidates if candidate.strategy == "Covered Call"
        ),
        average_assignment_probability=round(average_assignment_probability, 4),
    )


def _recommendation_candidates(candidates: list[Candidate], recommendations: set[Recommendation]) -> list[Candidate]:
    return [candidate for candidate in candidates if candidate.recommendation in recommendations]


def _sorted_by_efficiency(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda candidate: candidate.premium_efficiency_score, reverse=True)


def _sorted_by_recommendation_then_efficiency(candidates: list[Candidate]) -> list[Candidate]:
    priority = {"Sell": 2, "Maybe": 1, "Skip": 0}
    return sorted(
        candidates,
        key=lambda candidate: (priority[candidate.recommendation], candidate.premium_efficiency_score),
        reverse=True,
    )


def _split_alerts(alerts: str) -> list[str]:
    return [alert.strip() for alert in alerts.split(";") if alert.strip()]

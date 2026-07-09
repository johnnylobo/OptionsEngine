from __future__ import annotations

from .holdings import holdings_to_share_map
from .models import Category, Holding, HoldingExposure, OptionExposure, PortfolioSummary, Strategy, TickerProfile
from .preferences import get_ticker_profile
from .providers import MarketDataProvider


CATEGORIES: tuple[Category, ...] = (
    "Core Compounder",
    "Semiconductor",
    "AI Infrastructure",
    "Cybersecurity",
    "Crypto Infrastructure",
    "Space",
    "Energy",
    "Cash",
    "Other",
)


def normalize_category(category: str) -> Category:
    normalized = category.strip().lower()
    aliases = {
        "core compounders": "Core Compounder",
        "core compounder": "Core Compounder",
        "semiconductor": "Semiconductor",
        "semiconductors": "Semiconductor",
        "ai infrastructure": "AI Infrastructure",
        "cybersecurity": "Cybersecurity",
        "crypto infrastructure": "Crypto Infrastructure",
        "space": "Space",
        "energy": "Energy",
        "cash": "Cash",
        "volatility harvest / thematic": "Other",
        "unprofiled": "Other",
        "uncategorized": "Other",
        "other": "Other",
    }
    return aliases.get(normalized, "Other")  # type: ignore[return-value]


def build_portfolio_summary(
    *,
    holdings: list[Holding],
    provider: MarketDataProvider,
    profiles: dict[str, TickerProfile],
    cash_balance: float,
) -> PortfolioSummary:
    share_map = holdings_to_share_map(holdings)
    raw_holdings: list[tuple[str, int, float, float, Category]] = []
    total_market_value = 0.0

    for ticker in sorted(share_map):
        shares = share_map[ticker]
        snapshot = provider.get_equity_snapshot(ticker)
        price = float(snapshot.price)
        market_value = round(shares * price, 2)
        profile = get_ticker_profile(ticker, profiles)
        category = normalize_category(profile.category)
        raw_holdings.append((ticker, shares, price, market_value, category))
        total_market_value += market_value

    total_market_value = round(total_market_value, 2)
    total_account_value = round(total_market_value + cash_balance, 2)
    denominator = total_account_value if total_account_value > 0 else 1

    holdings_exposure = [
        HoldingExposure(
            ticker=ticker,
            shares=shares,
            current_price=round(price, 2),
            market_value=market_value,
            category=category,
            portfolio_weight=round(market_value / denominator, 4),
        )
        for ticker, shares, price, market_value, category in raw_holdings
    ]

    category_exposure: dict[str, float] = {"Cash": round(cash_balance / denominator, 4)}
    ticker_exposure: dict[str, float] = {}
    for holding in holdings_exposure:
        category_exposure[holding.category] = category_exposure.get(holding.category, 0.0) + holding.portfolio_weight
        ticker_exposure[holding.ticker] = holding.portfolio_weight

    category_exposure = {category: round(weight, 4) for category, weight in category_exposure.items()}
    top_ticker_exposures = sorted(holdings_exposure, key=lambda item: item.portfolio_weight, reverse=True)[:10]
    largest_single_name = top_ticker_exposures[0] if top_ticker_exposures else None
    non_cash_categories = {category: weight for category, weight in category_exposure.items() if category != "Cash"}
    largest_category = max(non_cash_categories.items(), key=lambda item: item[1]) if non_cash_categories else None

    return PortfolioSummary(
        total_portfolio_market_value=total_market_value,
        cash_balance=round(cash_balance, 2),
        total_account_value=total_account_value,
        holdings=holdings_exposure,
        category_exposure=category_exposure,
        ticker_exposure=ticker_exposure,
        top_ticker_exposures=top_ticker_exposures,
        largest_single_name=largest_single_name,
        largest_category=largest_category,
    )


def calculate_option_exposure(
    *,
    strategy: Strategy,
    ticker: str,
    category: Category,
    current_price: float,
    strike: float,
    contracts: int,
    owned_shares: int,
    cash_balance: float,
    portfolio: PortfolioSummary,
) -> OptionExposure:
    category = normalize_category(str(category))
    shares = contracts * 100
    total_value = portfolio.total_account_value if portfolio.total_account_value > 0 else 1
    current_ticker_exposure = round(owned_shares * current_price, 2)
    current_category_weight = portfolio.category_exposure.get(category, 0.0)
    current_ticker_weight = portfolio.ticker_exposure.get(ticker, 0.0)

    if strategy == "Cash-Secured Put":
        additional_exposure = round(strike * shares, 2)
        cash_used = additional_exposure
        post_ticker_exposure = current_ticker_exposure + additional_exposure
        post_category_exposure = (current_category_weight * total_value) + additional_exposure
        shares_remaining = owned_shares
        market_value_at_risk = 0.0
    else:
        additional_exposure = 0.0
        cash_used = 0.0
        shares_remaining = max(owned_shares - shares, 0)
        market_value_at_risk = round(min(shares, owned_shares) * current_price, 2)
        post_ticker_exposure = shares_remaining * current_price
        post_category_exposure = max((current_category_weight * total_value) - market_value_at_risk, 0.0)

    post_ticker_weight = round(post_ticker_exposure / total_value, 4)
    post_category_weight = round(post_category_exposure / total_value, 4)
    category_change = round(post_category_weight - current_category_weight, 4)
    alerts = _risk_alerts(
        strategy=strategy,
        category=category,
        contracts=contracts,
        owned_shares=owned_shares,
        cash_balance=cash_balance,
        current_ticker_weight=current_ticker_weight,
        current_category_weight=current_category_weight,
        post_ticker_weight=post_ticker_weight,
        post_category_weight=post_category_weight,
        cash_used=cash_used,
    )

    return OptionExposure(
        current_ticker_exposure=round(current_ticker_exposure, 2),
        current_category_exposure=round(current_category_weight * total_value, 2),
        current_ticker_weight=round(current_ticker_weight, 4),
        current_category_weight=round(current_category_weight, 4),
        additional_exposure_if_assigned=round(additional_exposure, 2),
        maximum_exposure_after_assignment=round(max(current_ticker_exposure, post_ticker_exposure), 2),
        post_assignment_ticker_weight=post_ticker_weight,
        post_assignment_category_weight=post_category_weight,
        cash_used_if_assigned=round(cash_used, 2),
        shares_remaining_if_called_away=shares_remaining,
        market_value_at_risk_if_called_away=market_value_at_risk,
        category_exposure_change_if_assigned=category_change,
        portfolio_risk_alerts="; ".join(alerts),
        portfolio_risk_adjustment=_risk_adjustment(alerts, strategy, current_ticker_weight, current_category_weight, post_ticker_weight, post_category_weight),
    )


def neutral_option_exposure() -> OptionExposure:
    return OptionExposure(
        current_ticker_exposure=0.0,
        current_category_exposure=0.0,
        current_ticker_weight=0.0,
        current_category_weight=0.0,
        additional_exposure_if_assigned=0.0,
        maximum_exposure_after_assignment=0.0,
        post_assignment_ticker_weight=0.0,
        post_assignment_category_weight=0.0,
        cash_used_if_assigned=0.0,
        shares_remaining_if_called_away=0,
        market_value_at_risk_if_called_away=0.0,
        category_exposure_change_if_assigned=0.0,
        portfolio_risk_alerts="",
        portfolio_risk_adjustment=1.0,
    )


def _risk_alerts(
    *,
    strategy: Strategy,
    category: Category,
    contracts: int,
    owned_shares: int,
    cash_balance: float,
    current_ticker_weight: float,
    current_category_weight: float,
    post_ticker_weight: float,
    post_category_weight: float,
    cash_used: float,
) -> list[str]:
    alerts: list[str] = []
    if max(current_ticker_weight, post_ticker_weight) > 0.20:
        alerts.append("Single ticker exposure above 20%")
    if max(current_category_weight, post_category_weight) > 0.40:
        alerts.append("Category exposure above 40%")
    if strategy == "Cash-Secured Put":
        if cash_balance > 0 and cash_used / cash_balance > 0.50:
            alerts.append("Put assignment would use more than 50% of available cash")
        if current_category_weight > 0.35 and post_category_weight - current_category_weight >= 0.05:
            alerts.append("Assignment would materially increase already-high category exposure")
    else:
        shares_covered = contracts * 100
        if owned_shares > 0 and shares_covered / owned_shares > 0.50:
            alerts.append("Covered call would cover more than 50% of owned shares")
        if category == "Core Compounder" and owned_shares > 0 and shares_covered / owned_shares >= 0.25:
            alerts.append("Selling call would materially reduce exposure to a core compounder")
    return alerts


def _risk_adjustment(
    alerts: list[str],
    strategy: Strategy,
    current_ticker_weight: float,
    current_category_weight: float,
    post_ticker_weight: float,
    post_category_weight: float,
) -> float:
    adjustment = 1 - (0.12 * len(alerts))
    if strategy == "Cash-Secured Put":
        if post_ticker_weight > 0.20:
            adjustment -= 0.18
        if post_category_weight > 0.40:
            adjustment -= 0.18
    else:
        if "Selling call would materially reduce exposure to a core compounder" in alerts:
            adjustment -= 0.18
    if not alerts and post_ticker_weight <= 0.15 and post_category_weight <= 0.35:
        adjustment += 0.08
    if current_ticker_weight > 0.20 and post_ticker_weight < current_ticker_weight:
        adjustment += 0.05
    if current_category_weight > 0.40 and post_category_weight < current_category_weight:
        adjustment += 0.05
    return round(max(0.35, min(adjustment, 1.15)), 4)

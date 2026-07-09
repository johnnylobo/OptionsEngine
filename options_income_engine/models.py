from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional


Strategy = Literal["Covered Call", "Cash-Secured Put"]
Recommendation = Literal["Sell", "Maybe", "Skip"]
Category = Literal[
    "Core Compounder",
    "Semiconductor",
    "AI Infrastructure",
    "Cybersecurity",
    "Crypto Infrastructure",
    "Space",
    "Energy",
    "Cash",
    "Other",
]


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: int
    cost_basis: Optional[float] = None
    account: Optional[str] = None


@dataclass(frozen=True)
class UserConfig:
    available_cash: float
    expirations: list[date]
    delta_min: float = 0.03
    delta_max: float = 0.10
    min_weekly_yield: float = 0.003
    max_spread_pct: float = 0.25
    watchlist: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TickerProfile:
    ticker: str
    tier: str
    category: Category
    own_more_score: int
    happy_to_sell_score: int
    max_contracts: Optional[int] = None
    notes: str = ""


@dataclass(frozen=True)
class EquitySnapshot:
    ticker: str
    price: float
    next_earnings_date: Optional[date] = None


@dataclass(frozen=True)
class OptionContract:
    ticker: str
    expiration: date
    option_type: Literal["call", "put"]
    strike: float
    bid: float
    ask: float
    delta: Optional[float]
    iv_rank: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    symbol: Optional[str] = None


@dataclass(frozen=True)
class HoldingExposure:
    ticker: str
    shares: int
    current_price: float
    market_value: float
    category: Category
    portfolio_weight: float


@dataclass(frozen=True)
class PortfolioSummary:
    total_portfolio_market_value: float
    cash_balance: float
    total_account_value: float
    holdings: list[HoldingExposure]
    category_exposure: dict[str, float]
    ticker_exposure: dict[str, float]
    top_ticker_exposures: list[HoldingExposure]
    largest_single_name: Optional[HoldingExposure]
    largest_category: Optional[tuple[str, float]]


@dataclass(frozen=True)
class OptionExposure:
    current_ticker_exposure: float
    current_category_exposure: float
    current_ticker_weight: float
    current_category_weight: float
    additional_exposure_if_assigned: float
    maximum_exposure_after_assignment: float
    post_assignment_ticker_weight: float
    post_assignment_category_weight: float
    cash_used_if_assigned: float
    shares_remaining_if_called_away: int
    market_value_at_risk_if_called_away: float
    category_exposure_change_if_assigned: float
    portfolio_risk_alerts: str
    portfolio_risk_adjustment: float


@dataclass(frozen=True)
class Candidate:
    rank: int
    ticker: str
    strategy: Strategy
    expiration: date
    strike: float
    current_price: float
    bid: float
    ask: float
    mid: float
    delta: float
    iv_rank: float
    assignment_probability: float
    premium_efficiency_score: float
    premium_per_contract: float
    total_premium: float
    shares_covered: int
    cash_required: float
    capital_at_risk: float
    assignment_outcome: str
    effective_entry_price: Optional[float]
    percent_otm: float
    weekly_yield: float
    annualized_yield: float
    liquidity_warning: str
    earnings_warning: str
    recommendation: Recommendation
    suggested_limit_price: float
    tier: str
    category: str
    own_more_score: int
    happy_to_sell_score: int
    max_contracts: Optional[int]
    profile_notes: str
    preference_adjustment: float
    current_ticker_exposure: float
    current_category_exposure: float
    current_ticker_weight: float
    current_category_weight: float
    additional_exposure_if_assigned: float
    maximum_exposure_after_assignment: float
    post_assignment_ticker_weight: float
    post_assignment_category_weight: float
    cash_used_if_assigned: float
    shares_remaining_if_called_away: int
    market_value_at_risk_if_called_away: float
    category_exposure_change_if_assigned: float
    portfolio_risk_alerts: str
    portfolio_risk_adjustment: float
    score: float
    contracts: int

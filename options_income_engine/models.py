from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional


Strategy = Literal["Covered Call", "Cash-Secured Put"]
Recommendation = Literal["Sell", "Maybe", "Skip"]


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
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    symbol: Optional[str] = None


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
    assignment_probability: float
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
    score: float
    contracts: int

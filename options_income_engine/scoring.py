from __future__ import annotations

from datetime import date
from typing import Optional

from .models import Candidate, EquitySnapshot, OptionContract, Recommendation, Strategy, UserConfig
from .tiers import get_tier


def score_candidate(
    *,
    contract: OptionContract,
    strategy: Strategy,
    snapshot: EquitySnapshot,
    contracts: int,
    config: UserConfig,
) -> Optional[Candidate]:
    if contract.bid <= 0 or contract.ask <= 0:
        return None
    if contract.delta is None:
        return None

    abs_delta = abs(contract.delta)
    if abs_delta < config.delta_min or abs_delta > config.delta_max:
        return None

    current_price = snapshot.price
    if strategy == "Covered Call" and contract.strike <= current_price:
        return None
    if strategy == "Cash-Secured Put" and contract.strike >= current_price:
        return None

    mid = round((contract.bid + contract.ask) / 2, 2)
    spread_pct = (contract.ask - contract.bid) / mid if mid > 0 else 1.0
    assignment_probability = abs_delta
    iv_rank = _normalized_iv_rank(contract.iv_rank)
    premium_per_contract = mid * 100
    total_premium = premium_per_contract * contracts
    shares = contracts * 100
    cash_required = round(contract.strike * shares, 2) if strategy == "Cash-Secured Put" else 0.0
    capital_at_risk = round(
        shares * (current_price if strategy == "Covered Call" else contract.strike),
        2,
    )
    weekly_yield = total_premium / capital_at_risk if capital_at_risk > 0 else 0
    annualized_yield = weekly_yield * 52
    premium_efficiency_score = _premium_efficiency_score(
        total_premium=total_premium,
        iv_rank=iv_rank,
        assignment_probability=assignment_probability,
        capital_required=capital_at_risk,
    )
    percent_otm = (
        (contract.strike - current_price) / current_price
        if strategy == "Covered Call"
        else (current_price - contract.strike) / current_price
    )
    effective_entry_price = round(contract.strike - mid, 2) if strategy == "Cash-Secured Put" else None
    assignment_outcome = _assignment_outcome(
        strategy=strategy,
        shares=shares,
        strike=contract.strike,
        effective_entry_price=effective_entry_price,
    )
    earnings_warning = _earnings_warning(snapshot.next_earnings_date, contract.expiration)
    liquidity_warning = _liquidity_warning(contract, spread_pct, config.max_spread_pct)
    tier = get_tier(contract.ticker)

    base_score = (
        min(weekly_yield / max(config.min_weekly_yield, 0.0001), 2.5) * 26
        + min(percent_otm / 0.08, 1.5) * 18
        + (1 - min(abs_delta / max(config.delta_max, 0.0001), 1)) * 14
        + _spread_score(spread_pct, config.max_spread_pct) * 14
        + _liquidity_score(contract) * 14
        + _tier_score(tier, strategy, weekly_yield, config.min_weekly_yield) * 14
    )
    if earnings_warning:
        base_score -= 80
    if spread_pct > config.max_spread_pct:
        base_score -= 30
    if weekly_yield < config.min_weekly_yield:
        base_score -= 20

    recommendation = _recommendation(base_score, weekly_yield, spread_pct, earnings_warning, config)
    if tier.startswith("Tier 1") and strategy == "Covered Call" and weekly_yield < config.min_weekly_yield * 1.75:
        recommendation = "Skip"
        base_score -= 25

    return Candidate(
        rank=0,
        ticker=contract.ticker,
        strategy=strategy,
        expiration=contract.expiration,
        strike=round(contract.strike, 2),
        current_price=round(current_price, 2),
        bid=round(contract.bid, 2),
        ask=round(contract.ask, 2),
        mid=mid,
        delta=round(contract.delta, 4),
        iv_rank=round(iv_rank, 4),
        assignment_probability=round(assignment_probability, 4),
        premium_efficiency_score=round(premium_efficiency_score, 6),
        premium_per_contract=round(premium_per_contract, 2),
        total_premium=round(total_premium, 2),
        shares_covered=shares if strategy == "Covered Call" else 0,
        cash_required=cash_required,
        capital_at_risk=capital_at_risk,
        assignment_outcome=assignment_outcome,
        effective_entry_price=effective_entry_price,
        percent_otm=round(percent_otm, 4),
        weekly_yield=round(weekly_yield, 4),
        annualized_yield=round(annualized_yield, 4),
        liquidity_warning=liquidity_warning,
        earnings_warning=earnings_warning,
        recommendation=recommendation,
        suggested_limit_price=round(max(contract.bid, mid), 2),
        tier=tier,
        score=round(max(base_score, 0), 2),
        contracts=contracts,
    )


def _normalized_iv_rank(iv_rank: Optional[float]) -> float:
    if iv_rank is None:
        return 1.0
    if iv_rank > 1:
        iv_rank = iv_rank / 100
    return max(0.0, min(iv_rank, 1.0))


def _premium_efficiency_score(
    *,
    total_premium: float,
    iv_rank: float,
    assignment_probability: float,
    capital_required: float,
) -> float:
    probability = max(assignment_probability, 0.0001)
    capital = max(capital_required, 0.01)
    return (total_premium * iv_rank) / probability / capital


def _assignment_outcome(
    *,
    strategy: Strategy,
    shares: int,
    strike: float,
    effective_entry_price: Optional[float],
) -> str:
    if strategy == "Covered Call":
        return f"May sell {shares} shares at ${strike:.2f} strike."
    return f"May buy {shares} shares at ${strike:.2f} strike; effective entry ${effective_entry_price:.2f}."


def _earnings_warning(next_earnings: Optional[date], expiration: date) -> str:
    if next_earnings is not None and next_earnings <= expiration:
        return f"Rejected: earnings {next_earnings.isoformat()} before expiration"
    return ""


def _liquidity_warning(contract: OptionContract, spread_pct: float, max_spread_pct: float) -> str:
    warnings: list[str] = []
    if spread_pct > max_spread_pct:
        warnings.append(f"Wide spread {spread_pct:.0%}")
    if (contract.volume or 0) < 10:
        warnings.append("Low volume")
    if (contract.open_interest or 0) < 100:
        warnings.append("Low open interest")
    return "; ".join(warnings)


def _spread_score(spread_pct: float, max_spread_pct: float) -> float:
    if spread_pct <= 0:
        return 1
    return max(0, 1 - (spread_pct / max(max_spread_pct, 0.01)))


def _liquidity_score(contract: OptionContract) -> float:
    volume_score = min((contract.volume or 0) / 100, 1)
    oi_score = min((contract.open_interest or 0) / 500, 1)
    return (volume_score + oi_score) / 2


def _tier_score(tier: str, strategy: Strategy, weekly_yield: float, min_yield: float) -> float:
    if tier.startswith("Tier 1"):
        if strategy == "Covered Call":
            return 1 if weekly_yield >= min_yield * 1.75 else 0
        return 0.65
    if tier.startswith("Tier 2"):
        return 1
    return 0.75


def _recommendation(
    score: float,
    weekly_yield: float,
    spread_pct: float,
    earnings_warning: str,
    config: UserConfig,
) -> Recommendation:
    if earnings_warning or spread_pct > config.max_spread_pct or weekly_yield < config.min_weekly_yield:
        return "Skip"
    if score >= 72:
        return "Sell"
    if score >= 52:
        return "Maybe"
    return "Skip"

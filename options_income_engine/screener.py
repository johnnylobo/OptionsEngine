from __future__ import annotations

from .holdings import holdings_to_share_map
from typing import Optional

from .models import Candidate, Holding, TickerProfile, UserConfig
from .preferences import get_ticker_profile, load_ticker_profiles
from .providers import MarketDataProvider
from .scoring import score_candidate
from .tiers import normalize_ticker


def select_strategy_universe(
    holdings: list[Holding],
    watchlist: list[str],
) -> tuple[set[str], set[str]]:
    share_map = holdings_to_share_map(holdings)
    covered_call_tickers = {ticker for ticker, shares in share_map.items() if shares >= 100}
    put_tickers = set(share_map) | {normalize_ticker(ticker) for ticker in watchlist if ticker.strip()}
    return covered_call_tickers, put_tickers


def screen_income_candidates(
    *,
    holdings: list[Holding],
    config: UserConfig,
    provider: MarketDataProvider,
    profiles: Optional[dict[str, TickerProfile]] = None,
) -> list[Candidate]:
    share_map = holdings_to_share_map(holdings)
    covered_call_tickers, put_tickers = select_strategy_universe(holdings, config.watchlist)
    symbols = sorted(covered_call_tickers | put_tickers)
    profiles = profiles if profiles is not None else load_ticker_profiles()
    candidates: list[Candidate] = []

    for ticker in symbols:
        profile = get_ticker_profile(ticker, profiles)
        snapshot = provider.get_equity_snapshot(ticker)
        owned_contracts = max(share_map.get(ticker, 0) // 100, 0)
        owned_contracts = _apply_max_contracts(owned_contracts, profile)

        for expiration in config.expirations:
            chain = provider.get_options_chain(ticker, expiration)

            if ticker in covered_call_tickers and owned_contracts > 0:
                for contract in chain:
                    if contract.option_type != "call":
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Covered Call",
                        snapshot=snapshot,
                        contracts=owned_contracts,
                        config=config,
                        profile=profile,
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)

            if ticker in put_tickers:
                for contract in chain:
                    if contract.option_type != "put":
                        continue
                    max_contracts = int(config.available_cash // (contract.strike * 100))
                    max_contracts = _apply_max_contracts(max_contracts, profile)
                    if max_contracts < 1:
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Cash-Secured Put",
                        snapshot=snapshot,
                        contracts=max_contracts,
                        config=config,
                        profile=profile,
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)

    candidates.sort(key=lambda item: item.premium_efficiency_score, reverse=True)
    ranked: list[Candidate] = []
    for index, candidate in enumerate(candidates, start=1):
        ranked.append(candidate.__class__(**{**candidate.__dict__, "rank": index}))
    return ranked


def _apply_max_contracts(contracts: int, profile: TickerProfile) -> int:
    if profile.max_contracts is None:
        return contracts
    return min(contracts, profile.max_contracts)

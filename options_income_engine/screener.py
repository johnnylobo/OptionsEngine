from __future__ import annotations

from .holdings import holdings_to_share_map
from .models import Candidate, Holding, UserConfig
from .providers import MarketDataProvider
from .scoring import score_candidate
from .tiers import normalize_ticker


def screen_income_candidates(
    *,
    holdings: list[Holding],
    config: UserConfig,
    provider: MarketDataProvider,
) -> list[Candidate]:
    share_map = holdings_to_share_map(holdings)
    symbols = sorted(set(share_map) | {normalize_ticker(t) for t in config.watchlist if t.strip()})
    candidates: list[Candidate] = []

    for ticker in symbols:
        snapshot = provider.get_equity_snapshot(ticker)
        owned_contracts = max(share_map.get(ticker, 0) // 100, 0)

        for expiration in config.expirations:
            chain = provider.get_options_chain(ticker, expiration)

            if owned_contracts > 0:
                for contract in chain:
                    if contract.option_type != "call":
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Covered Call",
                        snapshot=snapshot,
                        contracts=owned_contracts,
                        config=config,
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)

            if ticker in {normalize_ticker(t) for t in config.watchlist}:
                for contract in chain:
                    if contract.option_type != "put":
                        continue
                    max_contracts = int(config.available_cash // (contract.strike * 100))
                    if max_contracts < 1:
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Cash-Secured Put",
                        snapshot=snapshot,
                        contracts=max_contracts,
                        config=config,
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)

    candidates.sort(key=lambda item: (item.recommendation == "Sell", item.score), reverse=True)
    ranked: list[Candidate] = []
    for index, candidate in enumerate(candidates, start=1):
        ranked.append(candidate.__class__(**{**candidate.__dict__, "rank": index}))
    return ranked

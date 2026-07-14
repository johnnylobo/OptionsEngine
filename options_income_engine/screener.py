from __future__ import annotations

from dataclasses import dataclass, field
from .holdings import holdings_to_share_map
from typing import Callable, Optional

from .models import Candidate, EquitySnapshot, Holding, OptionContract, PortfolioSummary, Strategy, TickerProfile, UserConfig
from .portfolio import build_portfolio_summary, calculate_option_exposure
from .preferences import get_ticker_profile, load_ticker_profiles
from .providers import MarketDataError, MarketDataProvider
from .scoring import score_candidate
from .tiers import normalize_ticker


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ScreeningDiagnostics:
    holdings_scanned: int = 0
    tickers_scanned: int = 0
    supported_by_provider: int = 0
    unsupported_tickers: list[str] = field(default_factory=list)
    rejected_by_expiration: int = 0
    rejected_by_delta: int = 0
    rejected_by_premium_yield: int = 0
    rejected_by_spread_liquidity: int = 0
    rejected_by_stale_or_missing_data: int = 0
    contracts_screened: int = 0
    candidates_returned: int = 0
    provider_errors: list[str] = field(default_factory=list)

    @property
    def unsupported_by_provider(self) -> int:
        return len(self.unsupported_tickers)


@dataclass
class ScreeningResult:
    candidates: list[Candidate]
    diagnostics: ScreeningDiagnostics
    portfolio: Optional[PortfolioSummary] = None


DEMO_REAL_PORTFOLIO_MESSAGE = (
    "Demo market data supports only the demo universe and should not be used for real portfolio screening. "
    "Use Demo Portfolio or connect Massive/Tradier."
)


def select_strategy_universe(
    holdings: list[Holding],
    watchlist: list[str],
) -> tuple[set[str], set[str]]:
    share_map = holdings_to_share_map(holdings)
    covered_call_tickers = {ticker for ticker, shares in share_map.items() if shares >= 100}
    put_tickers = set(share_map) | {normalize_ticker(ticker) for ticker in watchlist if ticker.strip()}
    return covered_call_tickers, put_tickers


def diagnose_screening_universe(
    *,
    holdings: list[Holding],
    config: UserConfig,
    provider: MarketDataProvider,
) -> ScreeningDiagnostics:
    covered_call_tickers, put_tickers = select_strategy_universe(holdings, config.watchlist)
    symbols = sorted(covered_call_tickers | put_tickers)
    diagnostics = ScreeningDiagnostics(holdings_scanned=len(holdings), tickers_scanned=len(symbols))
    for symbol in symbols:
        if provider.supports_ticker(symbol):
            diagnostics.supported_by_provider += 1
        else:
            diagnostics.unsupported_tickers.append(symbol)
    return diagnostics


def screen_income_candidates(
    *,
    holdings: list[Holding],
    config: UserConfig,
    provider: MarketDataProvider,
    profiles: Optional[dict[str, TickerProfile]] = None,
) -> list[Candidate]:
    return screen_income_candidates_with_diagnostics(
        holdings=holdings,
        config=config,
        provider=provider,
        profiles=profiles,
    ).candidates


def screen_income_candidates_with_diagnostics(
    *,
    holdings: list[Holding],
    config: UserConfig,
    provider: MarketDataProvider,
    profiles: Optional[dict[str, TickerProfile]] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ScreeningResult:
    share_map = holdings_to_share_map(holdings)
    covered_call_tickers, put_tickers = select_strategy_universe(holdings, config.watchlist)
    symbols = sorted(covered_call_tickers | put_tickers)
    diagnostics = diagnose_screening_universe(holdings=holdings, config=config, provider=provider)
    supported_symbols = [symbol for symbol in symbols if provider.supports_ticker(symbol)]
    profiles = profiles if profiles is not None else load_ticker_profiles()
    supported_holding_tickers = {symbol for symbol in supported_symbols}
    portfolio_holdings = [holding for holding in holdings if normalize_ticker(holding.ticker) in supported_holding_tickers]
    try:
        portfolio = build_portfolio_summary(
            holdings=portfolio_holdings,
            provider=provider,
            profiles=profiles,
            cash_balance=config.available_cash,
        )
    except MarketDataError as exc:
        diagnostics.provider_errors.append(str(exc))
        return ScreeningResult(candidates=[], diagnostics=diagnostics)

    candidates: list[Candidate] = []

    for index, ticker in enumerate(supported_symbols, start=1):
        if progress_callback is not None:
            progress_callback(index, len(supported_symbols), ticker)
        profile = get_ticker_profile(ticker, profiles)
        try:
            equity_quote = provider.get_quote(ticker)
        except MarketDataError as exc:
            diagnostics.provider_errors.append(f"{ticker}: {exc}")
            diagnostics.rejected_by_stale_or_missing_data += 1
            continue
        snapshot = equity_quote.to_snapshot()
        owned_contracts = max(share_map.get(ticker, 0) // 100, 0)
        owned_contracts = _apply_max_contracts(owned_contracts, profile)

        for expiration in config.expirations:
            try:
                chain = provider.get_option_chain(ticker, expiration)
            except MarketDataError as exc:
                diagnostics.provider_errors.append(f"{ticker} {expiration.isoformat()}: {exc}")
                diagnostics.rejected_by_stale_or_missing_data += 1
                continue
            if not chain.contracts:
                diagnostics.rejected_by_expiration += 1
                continue

            if ticker in covered_call_tickers and owned_contracts > 0:
                for contract in chain.contracts:
                    if contract.option_type != "call":
                        continue
                    diagnostics.contracts_screened += 1
                    rejection_bucket = _diagnose_rejection(
                        contract=contract,
                        strategy="Covered Call",
                        snapshot=snapshot,
                        contracts=owned_contracts,
                        config=config,
                    )
                    if rejection_bucket:
                        _increment_rejection(diagnostics, rejection_bucket)
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Covered Call",
                        snapshot=snapshot,
                        contracts=owned_contracts,
                        config=config,
                        profile=profile,
                        option_exposure=calculate_option_exposure(
                            strategy="Covered Call",
                            ticker=ticker,
                            category=profile.category,
                            current_price=snapshot.price,
                            strike=contract.strike,
                            contracts=owned_contracts,
                            owned_shares=share_map.get(ticker, 0),
                            cash_balance=config.available_cash,
                            portfolio=portfolio,
                        ),
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)
                        _count_candidate_filter_warnings(candidate, diagnostics, config)
                    elif candidate is None:
                        diagnostics.rejected_by_stale_or_missing_data += 1

            if ticker in put_tickers:
                for contract in chain.contracts:
                    if contract.option_type != "put":
                        continue
                    max_contracts = int(config.available_cash // (contract.strike * 100))
                    max_contracts = _apply_max_contracts(max_contracts, profile)
                    if max_contracts < 1:
                        diagnostics.rejected_by_premium_yield += 1
                        continue
                    diagnostics.contracts_screened += 1
                    rejection_bucket = _diagnose_rejection(
                        contract=contract,
                        strategy="Cash-Secured Put",
                        snapshot=snapshot,
                        contracts=max_contracts,
                        config=config,
                    )
                    if rejection_bucket:
                        _increment_rejection(diagnostics, rejection_bucket)
                        continue
                    candidate = score_candidate(
                        contract=contract,
                        strategy="Cash-Secured Put",
                        snapshot=snapshot,
                        contracts=max_contracts,
                        config=config,
                        profile=profile,
                        option_exposure=calculate_option_exposure(
                            strategy="Cash-Secured Put",
                            ticker=ticker,
                            category=profile.category,
                            current_price=snapshot.price,
                            strike=contract.strike,
                            contracts=max_contracts,
                            owned_shares=share_map.get(ticker, 0),
                            cash_balance=config.available_cash,
                            portfolio=portfolio,
                        ),
                    )
                    if candidate and not candidate.earnings_warning:
                        candidates.append(candidate)
                        _count_candidate_filter_warnings(candidate, diagnostics, config)
                    elif candidate is None:
                        diagnostics.rejected_by_stale_or_missing_data += 1

    candidates.sort(key=lambda item: item.premium_efficiency_score, reverse=True)
    ranked: list[Candidate] = []
    for index, candidate in enumerate(candidates, start=1):
        ranked.append(candidate.__class__(**{**candidate.__dict__, "rank": index}))
    diagnostics.candidates_returned = len(ranked)
    return ScreeningResult(candidates=ranked, diagnostics=diagnostics, portfolio=portfolio)


def screening_suggestions(diagnostics: ScreeningDiagnostics) -> list[str]:
    suggestions: list[str] = []
    if diagnostics.unsupported_by_provider:
        suggestions.append("Use a provider that supports your uploaded tickers, or click Use Demo Portfolio when Demo is selected.")
    if diagnostics.rejected_by_expiration:
        suggestions.append("Choose an expiration date that exists in the selected provider's option chain.")
    if diagnostics.rejected_by_delta:
        suggestions.append("Widen the target delta range slightly.")
    if diagnostics.rejected_by_premium_yield:
        suggestions.append("Lower the minimum weekly premium yield or increase available cash for cash-secured puts.")
    if diagnostics.rejected_by_spread_liquidity:
        suggestions.append("Raise the max bid/ask spread limit slightly, or focus on more liquid tickers.")
    if diagnostics.rejected_by_stale_or_missing_data:
        suggestions.append("Refresh market data or use a provider with option bid/ask quotes and Greeks.")
    if not suggestions:
        suggestions.append("Try a wider delta range, a different expiration, or a lower minimum weekly premium yield.")
    return suggestions


def _apply_max_contracts(contracts: int, profile: TickerProfile) -> int:
    if profile.max_contracts is None:
        return contracts
    return min(contracts, profile.max_contracts)


def _diagnose_rejection(
    *,
    contract: OptionContract,
    strategy: Strategy,
    snapshot: EquitySnapshot,
    contracts: int,
    config: UserConfig,
) -> str:
    if getattr(contract, "is_stale", False):
        return "stale_or_missing"
    if getattr(contract, "is_delayed", False) and not getattr(contract, "provider", "").lower().startswith("demo"):
        return "stale_or_missing"
    if contract.bid is None or contract.ask is None or contract.bid <= 0 or contract.ask <= 0:
        return "stale_or_missing"
    if contract.delta is None:
        return "stale_or_missing"

    abs_delta = abs(contract.delta)
    if abs_delta < config.delta_min or abs_delta > config.delta_max:
        return "delta"

    current_price = snapshot.price
    if strategy == "Covered Call" and contract.strike <= current_price:
        return "delta"
    if strategy == "Cash-Secured Put" and contract.strike >= current_price:
        return "delta"

    return ""


def _increment_rejection(diagnostics: ScreeningDiagnostics, bucket: str) -> None:
    if bucket == "delta":
        diagnostics.rejected_by_delta += 1
    elif bucket == "premium_yield":
        diagnostics.rejected_by_premium_yield += 1
    elif bucket == "spread_liquidity":
        diagnostics.rejected_by_spread_liquidity += 1
    elif bucket == "stale_or_missing":
        diagnostics.rejected_by_stale_or_missing_data += 1


def _count_candidate_filter_warnings(candidate: Candidate, diagnostics: ScreeningDiagnostics, config: UserConfig) -> None:
    if candidate.weekly_yield < config.min_weekly_yield:
        diagnostics.rejected_by_premium_yield += 1
    if candidate.liquidity_warning:
        diagnostics.rejected_by_spread_liquidity += 1

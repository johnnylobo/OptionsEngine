from __future__ import annotations

from datetime import date

from options_income_engine.models import EquitySnapshot, Holding, OptionContract, TickerProfile, UserConfig
from options_income_engine.portfolio import build_portfolio_summary, calculate_option_exposure
from options_income_engine.providers import MarketDataProvider
from options_income_engine.scoring import score_candidate
from options_income_engine.screener import screen_income_candidates


EXPIRATION = date(2026, 7, 17)


class PortfolioProvider(MarketDataProvider):
    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        prices = {
            "AAA": 100.0,
            "BBB": 100.0,
            "CCC": 50.0,
        }
        return EquitySnapshot(ticker=ticker, price=prices[ticker])

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        price = self.get_equity_snapshot(ticker).price
        return [
            OptionContract(
                ticker=ticker,
                expiration=expiration,
                option_type="call",
                strike=round(price * 1.05, 2),
                bid=1.0,
                ask=1.1,
                delta=0.07,
                iv_rank=0.50,
                volume=100,
                open_interest=500,
            ),
            OptionContract(
                ticker=ticker,
                expiration=expiration,
                option_type="put",
                strike=round(price * 0.95, 2),
                bid=1.0,
                ask=1.1,
                delta=-0.07,
                iv_rank=0.50,
                volume=100,
                open_interest=500,
            ),
        ]


def test_category_and_ticker_exposure_calculation() -> None:
    profiles = {
        "AAA": TickerProfile("AAA", "Custom", "Semiconductor", 3, 3),
        "BBB": TickerProfile("BBB", "Custom", "Core Compounder", 3, 3),
    }

    portfolio = build_portfolio_summary(
        holdings=[Holding("AAA", 100), Holding("BBB", 50)],
        provider=PortfolioProvider(),
        profiles=profiles,
        cash_balance=5_000,
    )

    assert portfolio.total_portfolio_market_value == 15_000
    assert portfolio.total_account_value == 20_000
    assert portfolio.ticker_exposure["AAA"] == 0.50
    assert portfolio.ticker_exposure["BBB"] == 0.25
    assert portfolio.category_exposure["Semiconductor"] == 0.50
    assert portfolio.category_exposure["Core Compounder"] == 0.25
    assert portfolio.category_exposure["Cash"] == 0.25
    assert portfolio.largest_single_name is not None
    assert portfolio.largest_single_name.ticker == "AAA"
    assert portfolio.largest_category == ("Semiconductor", 0.50)


def test_put_post_assignment_exposure_and_alerts() -> None:
    profiles = {"AAA": TickerProfile("AAA", "Custom", "Semiconductor", 5, 3)}
    portfolio = build_portfolio_summary(
        holdings=[Holding("AAA", 100)],
        provider=PortfolioProvider(),
        profiles=profiles,
        cash_balance=10_000,
    )

    exposure = calculate_option_exposure(
        strategy="Cash-Secured Put",
        ticker="AAA",
        category="Semiconductor",
        current_price=100.0,
        strike=95.0,
        contracts=1,
        owned_shares=100,
        cash_balance=10_000,
        portfolio=portfolio,
    )

    assert exposure.current_ticker_exposure == 10_000
    assert exposure.additional_exposure_if_assigned == 9_500
    assert exposure.maximum_exposure_after_assignment == 19_500
    assert exposure.post_assignment_ticker_weight == 0.975
    assert exposure.post_assignment_category_weight == 0.975
    assert exposure.cash_used_if_assigned == 9_500
    assert "Single ticker exposure above 20%" in exposure.portfolio_risk_alerts
    assert "Put assignment would use more than 50% of available cash" in exposure.portfolio_risk_alerts
    assert exposure.portfolio_risk_adjustment < 1


def test_call_post_assignment_exposure_and_alerts() -> None:
    profiles = {"BBB": TickerProfile("BBB", "Custom", "Core Compounder", 4, 1)}
    portfolio = build_portfolio_summary(
        holdings=[Holding("BBB", 300)],
        provider=PortfolioProvider(),
        profiles=profiles,
        cash_balance=0,
    )

    exposure = calculate_option_exposure(
        strategy="Covered Call",
        ticker="BBB",
        category="Core Compounder",
        current_price=100.0,
        strike=105.0,
        contracts=2,
        owned_shares=300,
        cash_balance=0,
        portfolio=portfolio,
    )

    assert exposure.current_ticker_exposure == 30_000
    assert exposure.shares_remaining_if_called_away == 100
    assert exposure.market_value_at_risk_if_called_away == 20_000
    assert exposure.post_assignment_ticker_weight == 0.3333
    assert exposure.post_assignment_category_weight == 0.3333
    assert "Covered call would cover more than 50% of owned shares" in exposure.portfolio_risk_alerts
    assert "Selling call would materially reduce exposure to a core compounder" in exposure.portfolio_risk_alerts


def test_screener_adds_portfolio_output_fields() -> None:
    profiles = {
        "AAA": TickerProfile("AAA", "Custom", "Semiconductor", 5, 3, max_contracts=1),
    }
    config = UserConfig(available_cash=10_000, expirations=[EXPIRATION], watchlist=["AAA"])

    candidates = screen_income_candidates(
        holdings=[Holding("AAA", 100)],
        config=config,
        provider=PortfolioProvider(),
        profiles=profiles,
    )

    assert candidates
    assert all(candidate.current_ticker_weight > 0 for candidate in candidates)
    assert all(candidate.current_category_weight > 0 for candidate in candidates)
    assert any(candidate.cash_used_if_assigned > 0 for candidate in candidates)
    assert any(candidate.shares_remaining_if_called_away == 0 for candidate in candidates)
    assert any(candidate.portfolio_risk_alerts for candidate in candidates)


def test_scoring_penalizes_excessive_concentration() -> None:
    config = UserConfig(available_cash=10_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot("AAA", 100.0)
    profile = TickerProfile("AAA", "Custom", "Semiconductor", 5, 3)
    contract = OptionContract(
        ticker="AAA",
        expiration=EXPIRATION,
        option_type="put",
        strike=95.0,
        bid=1.0,
        ask=1.1,
        delta=-0.07,
        iv_rank=0.50,
        volume=100,
        open_interest=500,
    )
    risky_portfolio = build_portfolio_summary(
        holdings=[Holding("AAA", 100)],
        provider=PortfolioProvider(),
        profiles={"AAA": profile},
        cash_balance=10_000,
    )
    risky_exposure = calculate_option_exposure(
        strategy="Cash-Secured Put",
        ticker="AAA",
        category="Semiconductor",
        current_price=100.0,
        strike=95.0,
        contracts=1,
        owned_shares=100,
        cash_balance=10_000,
        portfolio=risky_portfolio,
    )

    neutral_candidate = score_candidate(
        contract=contract,
        strategy="Cash-Secured Put",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=profile,
    )
    risky_candidate = score_candidate(
        contract=contract,
        strategy="Cash-Secured Put",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=profile,
        option_exposure=risky_exposure,
    )

    assert neutral_candidate is not None
    assert risky_candidate is not None
    assert risky_candidate.portfolio_risk_adjustment < 1
    assert risky_candidate.premium_efficiency_score < neutral_candidate.premium_efficiency_score
    assert risky_candidate.score < neutral_candidate.score

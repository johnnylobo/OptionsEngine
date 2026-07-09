from __future__ import annotations

from datetime import date
from io import StringIO

from options_income_engine.holdings import parse_holdings_csv
from options_income_engine.models import EquitySnapshot, Holding, OptionContract, UserConfig
from options_income_engine.providers import MarketDataProvider
from options_income_engine.scoring import score_candidate
from options_income_engine.screener import screen_income_candidates, select_strategy_universe


EXPIRATION = date(2026, 7, 17)


class StaticProvider(MarketDataProvider):
    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        prices = {
            "AAA": 100.0,
            "BBB": 50.0,
            "CCC": 25.0,
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


def test_merrill_holdings_parser_accepts_common_columns() -> None:
    csv = StringIO(
        "Security Symbol,Qty,Total Cost Basis,Account Name\n"
        " aaa ,125,\"$10,000.00\",Merrill\n"
        "CASH,500,500.00,Merrill\n"
        "bbb,80,4000.00,IRA\n"
    )

    holdings = parse_holdings_csv(csv)

    assert holdings == [
        Holding(ticker="AAA", shares=125, cost_basis=10000.0, account="Merrill"),
        Holding(ticker="BBB", shares=80, cost_basis=4000.0, account="IRA"),
    ]


def test_universe_selection_uses_owned_names_for_puts_without_watchlist_requirement() -> None:
    holdings = [
        Holding(ticker="AAA", shares=125),
        Holding(ticker="BBB", shares=80),
    ]

    covered_calls, cash_secured_puts = select_strategy_universe(holdings, ["ccc"])

    assert covered_calls == {"AAA"}
    assert cash_secured_puts == {"AAA", "BBB", "CCC"}


def test_covered_calls_only_generate_for_owned_100_share_lots() -> None:
    holdings = [
        Holding(ticker="AAA", shares=125),
        Holding(ticker="BBB", shares=80),
    ]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION], watchlist=[])

    candidates = screen_income_candidates(holdings=holdings, config=config, provider=StaticProvider())
    covered_calls = [candidate for candidate in candidates if candidate.strategy == "Covered Call"]

    assert {candidate.ticker for candidate in covered_calls} == {"AAA"}
    assert all(candidate.shares_covered == 100 for candidate in covered_calls)


def test_cash_secured_puts_generate_for_owned_and_watchlist_names() -> None:
    holdings = [
        Holding(ticker="AAA", shares=125),
        Holding(ticker="BBB", shares=80),
    ]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION], watchlist=["CCC"])

    candidates = screen_income_candidates(holdings=holdings, config=config, provider=StaticProvider())
    puts = [candidate for candidate in candidates if candidate.strategy == "Cash-Secured Put"]

    assert {candidate.ticker for candidate in puts} == {"AAA", "BBB", "CCC"}
    assert all(candidate.cash_required > 0 for candidate in puts)


def test_basic_scoring_output_has_clear_strategy_risk_fields() -> None:
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot(ticker="AAA", price=100.0)
    call = OptionContract(
        ticker="AAA",
        expiration=EXPIRATION,
        option_type="call",
        strike=105.0,
        bid=1.0,
        ask=1.1,
        delta=0.07,
        iv_rank=0.50,
        volume=100,
        open_interest=500,
    )
    put = OptionContract(
        ticker="AAA",
        expiration=EXPIRATION,
        option_type="put",
        strike=95.0,
        bid=1.0,
        ask=1.1,
        delta=-0.07,
        iv_rank=0.60,
        volume=100,
        open_interest=500,
    )

    call_candidate = score_candidate(contract=call, strategy="Covered Call", snapshot=snapshot, contracts=1, config=config)
    put_candidate = score_candidate(contract=put, strategy="Cash-Secured Put", snapshot=snapshot, contracts=2, config=config)

    assert call_candidate is not None
    assert call_candidate.iv_rank == 0.50
    assert call_candidate.premium_efficiency_score == 0.075
    assert call_candidate.shares_covered == 100
    assert call_candidate.cash_required == 0
    assert call_candidate.capital_at_risk == 10_000
    assert call_candidate.assignment_outcome == "May sell 100 shares at $105.00 strike."

    assert put_candidate is not None
    assert put_candidate.iv_rank == 0.60
    assert put_candidate.premium_efficiency_score == 0.094737
    assert put_candidate.shares_covered == 0
    assert put_candidate.cash_required == 19_000
    assert put_candidate.capital_at_risk == 19_000
    assert put_candidate.effective_entry_price == 93.95
    assert put_candidate.assignment_outcome == "May buy 200 shares at $95.00 strike; effective entry $93.95."


def test_premium_efficiency_is_master_ranking() -> None:
    class RankingProvider(StaticProvider):
        def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
            prices = {
                "AAA": 100.0,
                "CCC": 25.0,
            }
            return EquitySnapshot(ticker=ticker, price=prices[ticker])

        def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
            if ticker == "AAA":
                return [
                    OptionContract(
                        ticker=ticker,
                        expiration=expiration,
                        option_type="put",
                        strike=95.0,
                        bid=2.0,
                        ask=2.2,
                        delta=-0.10,
                        iv_rank=0.20,
                        volume=100,
                        open_interest=500,
                    )
                ]
            return [
                OptionContract(
                    ticker=ticker,
                    expiration=expiration,
                    option_type="put",
                    strike=23.75,
                    bid=0.8,
                    ask=0.9,
                    delta=-0.05,
                    iv_rank=0.80,
                    volume=100,
                    open_interest=500,
                )
            ]

    config = UserConfig(available_cash=10_000, expirations=[EXPIRATION], watchlist=["AAA", "CCC"])

    candidates = screen_income_candidates(holdings=[], config=config, provider=RankingProvider())

    assert [candidate.ticker for candidate in candidates] == ["CCC", "AAA"]
    assert candidates[0].premium_efficiency_score > candidates[1].premium_efficiency_score

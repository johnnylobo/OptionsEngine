from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from options_income_engine.models import EquitySnapshot, Holding, OptionContract, UserConfig
from options_income_engine.providers import MarketDataError, MarketDataProvider, MockProvider
from options_income_engine.screener import (
    DEMO_REAL_PORTFOLIO_MESSAGE,
    diagnose_screening_universe,
    screen_income_candidates_with_diagnostics,
    screening_suggestions,
)


EXPIRATION = date(2026, 7, 17)


class DiagnosticProvider(MarketDataProvider):
    def __init__(
        self,
        *,
        supported: Optional[set[str]] = None,
        contracts: Optional[list[OptionContract]] = None,
        price: float = 100.0,
        fail: bool = False,
        empty_chain: bool = False,
    ) -> None:
        self.supported = supported
        self.contracts = contracts or []
        self.price = price
        self.fail = fail
        self.empty_chain = empty_chain

    def supports_ticker(self, ticker: str) -> bool:
        return self.supported is None or ticker in self.supported

    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        if self.fail:
            raise MarketDataError("Provider unavailable for testing.")
        return EquitySnapshot(ticker=ticker, price=self.price)

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        if self.fail:
            raise MarketDataError("Options chain unavailable for testing.")
        if self.empty_chain:
            return []
        return [contract.__class__(**{**contract.__dict__, "ticker": ticker, "expiration": expiration}) for contract in self.contracts]


def _good_contracts() -> list[OptionContract]:
    return [
        OptionContract(
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
        ),
        OptionContract(
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
        ),
    ]


def test_real_holdings_with_demo_provider_are_clearly_not_supported() -> None:
    holdings = [Holding(ticker="ABBV", shares=100), Holding(ticker="AMD", shares=100)]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    provider = MockProvider()

    diagnostics = diagnose_screening_universe(holdings=holdings, config=config, provider=provider)

    assert DEMO_REAL_PORTFOLIO_MESSAGE.startswith("Demo market data supports only the demo universe")
    assert diagnostics.holdings_scanned == 2
    assert diagnostics.supported_by_provider == 1
    assert diagnostics.unsupported_tickers == ["ABBV"]
    with pytest.raises(MarketDataError):
        provider.get_quote("ABBV")


def test_zero_candidates_report_delta_rejections() -> None:
    contracts = [
        OptionContract(
            ticker="AAA",
            expiration=EXPIRATION,
            option_type="put",
            strike=95.0,
            bid=1.0,
            ask=1.1,
            delta=-0.40,
            iv_rank=0.50,
            volume=100,
            open_interest=500,
        )
    ]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION], watchlist=["AAA"])

    result = screen_income_candidates_with_diagnostics(
        holdings=[],
        config=config,
        provider=DiagnosticProvider(contracts=contracts),
    )

    assert result.candidates == []
    assert result.diagnostics.rejected_by_delta == 1
    assert "Widen the target delta range slightly." in screening_suggestions(result.diagnostics)


def test_unsupported_tickers_are_counted_without_screening_them() -> None:
    holdings = [Holding(ticker="AAA", shares=100), Holding(ticker="BBB", shares=100)]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])

    result = screen_income_candidates_with_diagnostics(
        holdings=holdings,
        config=config,
        provider=DiagnosticProvider(supported={"AAA"}, contracts=_good_contracts()),
    )

    assert result.diagnostics.supported_by_provider == 1
    assert result.diagnostics.unsupported_tickers == ["BBB"]
    assert {candidate.ticker for candidate in result.candidates} == {"AAA"}


def test_invalid_expiration_is_reported_when_chain_is_empty() -> None:
    holdings = [Holding(ticker="AAA", shares=100)]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])

    result = screen_income_candidates_with_diagnostics(
        holdings=holdings,
        config=config,
        provider=DiagnosticProvider(empty_chain=True),
    )

    assert result.candidates == []
    assert result.diagnostics.rejected_by_expiration == 1
    assert "Choose an expiration date" in screening_suggestions(result.diagnostics)[0]


def test_provider_error_returns_human_readable_diagnostics() -> None:
    holdings = [Holding(ticker="AAA", shares=100)]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])

    result = screen_income_candidates_with_diagnostics(
        holdings=holdings,
        config=config,
        provider=DiagnosticProvider(fail=True),
    )

    assert result.candidates == []
    assert result.portfolio is None
    assert result.diagnostics.provider_errors == ["Provider unavailable for testing."]


def test_successful_screening_output_includes_candidates_and_counts() -> None:
    holdings = [Holding(ticker="AAA", shares=100)]
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])

    result = screen_income_candidates_with_diagnostics(
        holdings=holdings,
        config=config,
        provider=DiagnosticProvider(contracts=_good_contracts()),
    )

    assert result.portfolio is not None
    assert len(result.candidates) == 2
    assert result.diagnostics.candidates_returned == 2
    assert result.diagnostics.contracts_screened == 2


def test_streamlit_screening_feedback_copy_is_present() -> None:
    app_text = Path("streamlit_app.py").read_text()

    assert "Screening candidates..." in app_text
    assert "Screening complete:" in app_text
    assert "No candidates matched. Here is why." in app_text
    assert "DEMO_REAL_PORTFOLIO_MESSAGE" in app_text

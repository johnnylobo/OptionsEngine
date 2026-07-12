from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from options_income_engine.market_data import (
    MarketDataCache,
    OptionChain,
    OptionContractSnapshot,
    EquityQuote,
    trace_for,
    with_stale_status,
)
from options_income_engine.models import EquitySnapshot, OptionContract, UserConfig
from options_income_engine.providers import (
    MarketDataError,
    MassiveProvider,
    MockProvider,
    TradierProvider,
    build_provider,
    equity_quote_from_snapshot,
    test_market_data_connection,
)
from options_income_engine.scoring import score_candidate


EXPIRATION = date(2026, 7, 17)


def test_normalized_quote_model_carries_provider_trace() -> None:
    trace = trace_for(provider="Test", raw_symbol="abc", normalized_symbol="ABC", source_feed="unit")
    quote = EquityQuote(ticker="ABC", price=100.0, bid=99.9, ask=100.1, trace=trace)

    assert quote.trace.provider == "Test"
    assert quote.trace.normalized_symbol == "ABC"
    assert quote.to_snapshot().provider == "Test"


def test_massive_option_chain_mapping() -> None:
    provider = MassiveProvider("test-key")
    payload = {
        "results": [
            {
                "details": {
                    "ticker": "O:AMD260717C00160000",
                    "contract_type": "call",
                    "expiration_date": "2026-07-17",
                    "strike_price": 160,
                },
                "last_quote": {"bid": 1.2, "ask": 1.35, "bid_size": 10, "ask_size": 12, "sip_timestamp": 1_783_955_000_000},
                "last_trade": {"price": 1.25, "sip_timestamp": 1_783_955_000_000},
                "greeks": {"delta": 0.08, "gamma": 0.02, "theta": -0.03, "vega": 0.10},
                "implied_volatility": 0.55,
                "open_interest": 900,
                "day": {"volume": 120},
                "underlying_asset": {"price": 155.0},
            }
        ]
    }

    chain = provider.map_option_chain_payload(payload, "AMD", EXPIRATION)

    assert isinstance(chain, OptionChain)
    assert len(chain.contracts) == 1
    contract = chain.contracts[0]
    assert isinstance(contract, OptionContractSnapshot)
    assert contract.provider == "Massive"
    assert contract.bid == 1.2
    assert contract.ask == 1.35
    assert contract.delta == 0.08
    assert contract.gamma == 0.02
    assert contract.implied_volatility == 0.55
    assert contract.iv_rank is None
    assert contract.open_interest == 900
    assert contract.volume == 120


def test_tradier_option_chain_mapping_is_delayed_in_sandbox() -> None:
    provider = TradierProvider("token", environment="sandbox")
    payload = {
        "options": {
            "option": {
                "symbol": "AMD260717P00150000",
                "option_type": "put",
                "strike": 150,
                "bid": 1.1,
                "ask": 1.25,
                "volume": 80,
                "open_interest": 500,
                "greeks": {"delta": -0.07, "gamma": 0.01, "theta": -0.02, "vega": 0.09, "iv_rank": 60},
            }
        }
    }

    chain = provider.map_option_chain_payload(payload, "AMD", EXPIRATION)
    contract = chain.contracts[0]

    assert contract.provider == "Tradier"
    assert contract.is_delayed is True
    assert contract.is_realtime is False
    assert contract.delta == -0.07
    assert contract.iv_rank == 0.60
    assert contract.implied_volatility is None


def test_mock_provider_returns_normalized_chain() -> None:
    provider = MockProvider()

    chain = provider.get_option_chain("AMD", EXPIRATION)

    assert chain.trace.provider == "Demo"
    assert chain.trace.is_delayed is True
    assert chain.contracts
    assert all(contract.provider == "Demo" for contract in chain.contracts)


def test_provider_selection_prefers_massive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN", "tradier-token")

    provider = build_provider("auto")

    assert isinstance(provider, MassiveProvider)


def test_massive_provider_entitlement_defaults_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASSIVE_DATA_ENTITLEMENT", raising=False)
    provider = MassiveProvider("test-key")

    health = provider.health()

    assert health.realtime_status == "Unknown"
    assert health.is_realtime is False
    assert "entitlement is unknown" in health.message


def test_provider_can_be_labeled_realtime_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASSIVE_DATA_ENTITLEMENT", "real-time")
    provider = MassiveProvider("test-key")

    assert provider.health().realtime_status == "Real-time"


def test_provider_selection_does_not_silently_use_mock_without_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("OPTIONS_PROVIDER", "auto")

    with pytest.raises(MarketDataError):
        build_provider("auto")


def test_cache_prevents_duplicate_calls_within_ttl() -> None:
    cache: MarketDataCache[int] = MarketDataCache()
    calls = {"count": 0}

    def loader() -> int:
        calls["count"] += 1
        return 7

    assert cache.get_or_set(("quote", "AMD"), 30, loader) == 7
    assert cache.get_or_set(("quote", "AMD"), 30, loader) == 7
    assert calls["count"] == 1


def test_stale_data_detection_during_market_hours() -> None:
    market_now = datetime(2026, 7, 13, 15, 0, tzinfo=timezone.utc)
    old_timestamp = market_now - timedelta(minutes=3)
    trace = trace_for(
        provider="Test",
        raw_symbol="AMD",
        normalized_symbol="AMD",
        retrieved_at=market_now,
        market_timestamp=old_timestamp,
    )

    stale = with_stale_status(trace, max_age_seconds=30, now=market_now)

    assert stale.is_stale is True
    assert "older than 30 seconds" in stale.stale_reason


def test_market_closed_realtime_trace_is_not_marked_live() -> None:
    closed_now = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)
    trace = trace_for(
        provider="Test",
        raw_symbol="AMD",
        normalized_symbol="AMD",
        retrieved_at=closed_now,
        market_timestamp=closed_now - timedelta(hours=1),
        realtime_status="Real-time",
    )

    assert trace.realtime_status == "Market closed"
    assert trace.is_realtime is False
    assert "not live" in trace.stale_reason


def test_score_candidate_refuses_stale_or_missing_bid_ask() -> None:
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot(ticker="AMD", price=155.0)
    stale_contract = OptionContract(
        ticker="AMD",
        expiration=EXPIRATION,
        option_type="put",
        strike=150.0,
        bid=1.0,
        ask=1.2,
        delta=-0.07,
        is_stale=True,
        stale_reason="Data older than 30 seconds.",
    )
    missing_bid = OptionContract(
        ticker="AMD",
        expiration=EXPIRATION,
        option_type="put",
        strike=150.0,
        bid=0.0,
        ask=1.2,
        delta=-0.07,
    )

    assert score_candidate(contract=stale_contract, strategy="Cash-Secured Put", snapshot=snapshot, contracts=1, config=config) is None
    assert score_candidate(contract=missing_bid, strategy="Cash-Secured Put", snapshot=snapshot, contracts=1, config=config) is None


def test_missing_iv_rank_uses_neutral_scoring_and_warning() -> None:
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot(ticker="AMD", price=155.0)
    contract = OptionContract(
        ticker="AMD",
        expiration=EXPIRATION,
        option_type="put",
        strike=150.0,
        bid=1.0,
        ask=1.2,
        delta=-0.10,
        iv_rank=None,
        implied_volatility=0.45,
    )

    candidate = score_candidate(contract=contract, strategy="Cash-Secured Put", snapshot=snapshot, contracts=1, config=config)

    assert candidate is not None
    assert candidate.iv_rank is None
    assert candidate.implied_volatility == 0.45
    assert candidate.premium_efficiency_score == 0.073333
    assert candidate.iv_rank_warning == "IV rank unavailable from selected provider."


def test_provider_health_status() -> None:
    provider = MockProvider()

    health = provider.health()

    assert health.provider == "Demo"
    assert health.status == "ok"
    assert health.is_delayed is True
    assert health.realtime_status == "Delayed"


def test_equity_snapshot_mapping_preserves_provider() -> None:
    snapshot = EquitySnapshot(ticker="AMD", price=155.0, provider="Legacy", is_delayed=True)

    quote = equity_quote_from_snapshot(snapshot, provider="Fallback")

    assert quote.trace.provider == "Legacy"
    assert quote.trace.is_delayed is True


def test_connection_test_reports_demo_data_without_terminal_logs() -> None:
    result = test_market_data_connection(MockProvider(), ticker="AMD")

    assert result.status == "Data returned but is delayed"
    assert result.quote_ok is True
    assert result.expirations_ok is True
    assert result.option_chain_ok is True
    assert result.has_bid_ask is True
    assert result.has_greeks is True
    assert result.realtime_status == "Delayed"


def test_dashboard_labels_iv_separately_from_iv_rank() -> None:
    app_source = open("streamlit_app.py", encoding="utf-8").read()

    assert '"implied_volatility": "IV"' in app_source
    assert '"iv_rank": "IV Rank"' in app_source

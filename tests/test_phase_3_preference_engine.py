from __future__ import annotations

import json
from datetime import date

from options_income_engine.models import EquitySnapshot, Holding, OptionContract, TickerProfile, UserConfig
from options_income_engine.preferences import load_ticker_profiles
from options_income_engine.providers import MarketDataProvider
from options_income_engine.scoring import score_candidate
from options_income_engine.screener import screen_income_candidates


EXPIRATION = date(2026, 7, 17)


class PreferenceProvider(MarketDataProvider):
    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        return EquitySnapshot(ticker=ticker, price=100.0)

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        return [
            OptionContract(
                ticker=ticker,
                expiration=expiration,
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
                ticker=ticker,
                expiration=expiration,
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


def test_load_ticker_profiles_from_json(tmp_path) -> None:
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "abc",
                    "tier": "Tier 1 core compounder",
                    "category": "Core Compounders",
                    "own_more_score": 5,
                    "happy_to_sell_score": 1,
                    "max_contracts": 2,
                    "notes": "Test profile",
                }
            ]
        ),
        encoding="utf-8",
    )

    profiles = load_ticker_profiles(profile_path)

    assert profiles["ABC"] == TickerProfile(
        ticker="ABC",
        tier="Tier 1 core compounder",
        category="Core Compounder",
        own_more_score=5,
        happy_to_sell_score=1,
        max_contracts=2,
        notes="Test profile",
    )


def test_default_profiles_include_core_and_thematic_names() -> None:
    profiles = load_ticker_profiles()

    assert profiles["NVDA"].category == "Core Compounder"
    assert profiles["NVDA"].own_more_score == 5
    assert profiles["NVDA"].happy_to_sell_score == 1
    assert profiles["RKLB"].category == "Space"


def test_preference_adjustment_penalizes_low_happy_to_sell_covered_calls() -> None:
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot(ticker="AAA", price=100.0)
    contract = OptionContract(
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

    low_happy = TickerProfile("AAA", "Custom", "Core", 3, 1, None, "Avoid selling")
    high_happy = TickerProfile("AAA", "Custom", "Harvest", 3, 5, None, "Fine selling")

    low_candidate = score_candidate(
        contract=contract,
        strategy="Covered Call",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=low_happy,
    )
    high_candidate = score_candidate(
        contract=contract,
        strategy="Covered Call",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=high_happy,
    )

    assert low_candidate is not None
    assert high_candidate is not None
    assert low_candidate.preference_adjustment == 0.70
    assert high_candidate.preference_adjustment == 1.30
    assert low_candidate.premium_efficiency_score < high_candidate.premium_efficiency_score


def test_preference_adjustment_boosts_high_own_more_cash_secured_puts() -> None:
    config = UserConfig(available_cash=20_000, expirations=[EXPIRATION])
    snapshot = EquitySnapshot(ticker="AAA", price=100.0)
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

    low_own_more = TickerProfile("AAA", "Custom", "Harvest", 1, 3, None, "Do not add much")
    high_own_more = TickerProfile("AAA", "Custom", "Core", 5, 3, None, "Want more")

    low_candidate = score_candidate(
        contract=contract,
        strategy="Cash-Secured Put",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=low_own_more,
    )
    high_candidate = score_candidate(
        contract=contract,
        strategy="Cash-Secured Put",
        snapshot=snapshot,
        contracts=1,
        config=config,
        profile=high_own_more,
    )

    assert low_candidate is not None
    assert high_candidate is not None
    assert low_candidate.preference_adjustment == 0.70
    assert high_candidate.preference_adjustment == 1.30
    assert high_candidate.premium_efficiency_score > low_candidate.premium_efficiency_score


def test_screener_respects_profile_max_contracts() -> None:
    profile = TickerProfile(
        ticker="AAA",
        tier="Custom",
        category="Core",
        own_more_score=5,
        happy_to_sell_score=5,
        max_contracts=1,
        notes="Cap sizing",
    )
    config = UserConfig(available_cash=50_000, expirations=[EXPIRATION], watchlist=["AAA"])

    candidates = screen_income_candidates(
        holdings=[Holding(ticker="AAA", shares=300)],
        config=config,
        provider=PreferenceProvider(),
        profiles={"AAA": profile},
    )

    assert candidates
    assert {candidate.strategy for candidate in candidates} == {"Covered Call", "Cash-Secured Put"}
    assert all(candidate.contracts == 1 for candidate in candidates)
    assert all(candidate.max_contracts == 1 for candidate in candidates)

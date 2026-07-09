from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from .models import Category, TickerProfile
from .tiers import get_tier, normalize_ticker


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "data" / "ticker_profiles.json"


def load_ticker_profiles(path: Optional[Union[str, Path]] = None) -> dict[str, TickerProfile]:
    profile_path = Path(path or os.getenv("TICKER_PROFILES_PATH") or DEFAULT_PROFILE_PATH)
    with profile_path.open("r", encoding="utf-8") as handle:
        raw_profiles = json.load(handle)

    if not isinstance(raw_profiles, list):
        raise ValueError("Ticker profile file must contain a JSON list.")

    profiles: dict[str, TickerProfile] = {}
    for raw_profile in raw_profiles:
        profile = _parse_profile(raw_profile)
        profiles[profile.ticker] = profile
    return profiles


def default_ticker_profile(ticker: str) -> TickerProfile:
    symbol = normalize_ticker(ticker)
    return TickerProfile(
        ticker=symbol,
        tier=get_tier(symbol),
        category="Other",
        own_more_score=3,
        happy_to_sell_score=3,
        max_contracts=None,
        notes="No custom preference profile configured.",
    )


def get_ticker_profile(ticker: str, profiles: dict[str, TickerProfile]) -> TickerProfile:
    symbol = normalize_ticker(ticker)
    return profiles.get(symbol, default_ticker_profile(symbol))


def _parse_profile(raw_profile: Any) -> TickerProfile:
    if not isinstance(raw_profile, dict):
        raise ValueError("Each ticker profile must be a JSON object.")

    ticker = normalize_ticker(str(raw_profile.get("ticker", "")))
    if not ticker:
        raise ValueError("Ticker profile is missing ticker.")

    own_more_score = _score(raw_profile.get("own_more_score"), "own_more_score", ticker)
    happy_to_sell_score = _score(raw_profile.get("happy_to_sell_score"), "happy_to_sell_score", ticker)

    max_contracts = raw_profile.get("max_contracts")
    if max_contracts is not None:
        max_contracts = int(max_contracts)
        if max_contracts < 0:
            raise ValueError(f"{ticker} max_contracts must be zero or greater.")

    return TickerProfile(
        ticker=ticker,
        tier=str(raw_profile.get("tier") or get_tier(ticker)),
        category=_profile_category(str(raw_profile.get("category") or "Other")),
        own_more_score=own_more_score,
        happy_to_sell_score=happy_to_sell_score,
        max_contracts=max_contracts,
        notes=str(raw_profile.get("notes") or ""),
    )


def _score(value: Any, field_name: str, ticker: str) -> int:
    score = int(value)
    if score < 1 or score > 5:
        raise ValueError(f"{ticker} {field_name} must be between 1 and 5.")
    return score


def _profile_category(category: str) -> Category:
    normalized = category.strip().lower()
    aliases = {
        "core compounder": "Core Compounder",
        "core compounders": "Core Compounder",
        "semiconductor": "Semiconductor",
        "semiconductors": "Semiconductor",
        "ai infrastructure": "AI Infrastructure",
        "cybersecurity": "Cybersecurity",
        "crypto infrastructure": "Crypto Infrastructure",
        "space": "Space",
        "energy": "Energy",
        "cash": "Cash",
        "other": "Other",
        "volatility harvest / thematic": "Other",
        "unprofiled": "Other",
        "uncategorized": "Other",
    }
    return aliases.get(normalized, "Other")  # type: ignore[return-value]

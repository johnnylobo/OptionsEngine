from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Optional

from .models import EquitySnapshot, OptionContract
from .tiers import normalize_ticker


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    @abstractmethod
    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        raise NotImplementedError


class YFinanceFallback:
    """Fallback only for stock prices and earnings dates."""

    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        import yfinance as yf

        symbol = normalize_ticker(ticker)
        stock = yf.Ticker(symbol)

        price = None
        try:
            fast_info = stock.fast_info
            price = fast_info.get("last_price") or fast_info.get("regular_market_price")
        except Exception:
            price = None

        if price is None:
            history = stock.history(period="5d")
            if history.empty:
                raise MarketDataError(f"No price found for {symbol}.")
            price = float(history["Close"].dropna().iloc[-1])

        next_earnings = None
        try:
            calendar = stock.calendar
            raw_date = None
            if isinstance(calendar, dict):
                raw_date = calendar.get("Earnings Date")
            elif hasattr(calendar, "loc") and not calendar.empty:
                raw_date = calendar.iloc[0, 0]
            if isinstance(raw_date, (list, tuple)) and raw_date:
                raw_date = raw_date[0]
            if raw_date is not None:
                next_earnings = _coerce_date(raw_date)
        except Exception:
            next_earnings = None

        return EquitySnapshot(ticker=symbol, price=float(price), next_earnings_date=next_earnings)


class TradierProvider(MarketDataProvider):
    def __init__(self, access_token: str, environment: str = "sandbox") -> None:
        if not access_token:
            raise MarketDataError("TRADIER_ACCESS_TOKEN is required for the Tradier provider.")
        self.access_token = access_token
        self.environment = environment
        self.base_url = (
            "https://sandbox.tradier.com/v1"
            if environment == "sandbox"
            else "https://api.tradier.com/v1"
        )
        self.fallback = YFinanceFallback()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import requests

        response = requests.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
            params=params,
            timeout=20,
        )
        if response.status_code >= 400:
            raise MarketDataError(f"Tradier request failed: {response.status_code} {response.text[:200]}")
        return response.json()

    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        symbol = normalize_ticker(ticker)
        payload = self._get("/markets/quotes", {"symbols": symbol, "greeks": "false"})
        quote = payload.get("quotes", {}).get("quote")
        if isinstance(quote, list):
            quote = quote[0] if quote else None
        if not quote:
            return self.fallback.get_equity_snapshot(symbol)

        price = quote.get("last") or quote.get("close") or quote.get("bid") or quote.get("ask")
        if price is None:
            return self.fallback.get_equity_snapshot(symbol)

        fallback_snapshot = self.fallback.get_equity_snapshot(symbol)
        return EquitySnapshot(
            ticker=symbol,
            price=float(price),
            next_earnings_date=fallback_snapshot.next_earnings_date,
        )

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        symbol = normalize_ticker(ticker)
        payload = self._get(
            "/markets/options/chains",
            {"symbol": symbol, "expiration": expiration.isoformat(), "greeks": "true"},
        )
        options = payload.get("options", {}).get("option") or []
        if isinstance(options, dict):
            options = [options]

        contracts: list[OptionContract] = []
        for option in options:
            greeks = option.get("greeks") or {}
            bid = option.get("bid")
            ask = option.get("ask")
            strike = option.get("strike")
            option_type = option.get("option_type")
            if bid is None or ask is None or strike is None or option_type not in {"call", "put"}:
                continue
            contracts.append(
                OptionContract(
                    ticker=symbol,
                    expiration=expiration,
                    option_type=option_type,
                    strike=float(strike),
                    bid=float(bid),
                    ask=float(ask),
                    delta=_optional_float(greeks.get("delta")),
                    iv_rank=_optional_iv_rank(
                        option.get("iv_rank")
                        or option.get("ivRank")
                        or greeks.get("iv_rank")
                        or greeks.get("ivRank")
                    ),
                    volume=_optional_int(option.get("volume")),
                    open_interest=_optional_int(option.get("open_interest")),
                    symbol=option.get("symbol"),
                )
            )
        return contracts


class MockProvider(MarketDataProvider):
    """Small offline provider so the app can be explored before API setup."""

    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        prices = {
            "TQQQ": 84.0,
            "NVDA": 164.0,
            "GOOG": 188.0,
            "AMZN": 224.0,
            "MU": 132.0,
            "AMD": 155.0,
            "RKLB": 28.0,
            "LUNR": 12.5,
        }
        return EquitySnapshot(ticker=normalize_ticker(ticker), price=prices.get(normalize_ticker(ticker), 50.0))

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        symbol = normalize_ticker(ticker)
        price = self.get_equity_snapshot(symbol).price
        contracts: list[OptionContract] = []
        for pct, delta, bid in [
            (0.03, 0.10, 0.44),
            (0.05, 0.07, 0.31),
            (0.08, 0.04, 0.18),
        ]:
            contracts.append(
                OptionContract(
                    ticker=symbol,
                    expiration=expiration,
                    option_type="call",
                    strike=round(price * (1 + pct), 2),
                    bid=bid,
                    ask=round(bid * 1.12, 2),
                    delta=delta,
                    iv_rank=0.55,
                    volume=120,
                    open_interest=850,
                )
            )
            contracts.append(
                OptionContract(
                    ticker=symbol,
                    expiration=expiration,
                    option_type="put",
                    strike=round(price * (1 - pct), 2),
                    bid=bid,
                    ask=round(bid * 1.14, 2),
                    delta=-delta,
                    iv_rank=0.60,
                    volume=95,
                    open_interest=640,
                )
            )
        return contracts


def build_provider() -> MarketDataProvider:
    provider_name = _get_setting("OPTIONS_PROVIDER", "tradier").lower().strip()
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "tradier":
        return TradierProvider(
            access_token=_get_setting("TRADIER_ACCESS_TOKEN", ""),
            environment=_get_setting("TRADIER_ENV", "sandbox").lower().strip(),
        )
    raise MarketDataError(f"Unsupported OPTIONS_PROVIDER={provider_name!r}. Use tradier or mock.")


def _get_setting(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value:
        return env_value
    try:
        import streamlit as st

        value = st.secrets.get(name, default)
        return str(value)
    except Exception:
        return default


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _optional_iv_rank(value: Any) -> Optional[float]:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if parsed > 1:
        parsed = parsed / 100
    return max(0.0, min(parsed, 1.0))


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(float(value))


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = datetime.fromisoformat(str(value).split(" ")[0])
    return parsed.date()

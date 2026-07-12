from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from .market_data import (
    ConnectionTestResult,
    EquityQuote,
    FreshnessRules,
    MarketDataCache,
    OptionChain,
    OptionContractSnapshot,
    OptionGreeks,
    OptionQuote,
    ProviderHealth,
    now_utc,
    parse_timestamp,
    trace_for,
)
from .models import EquitySnapshot, OptionContract
from .tiers import normalize_ticker


class MarketDataError(RuntimeError):
    pass


class RateLimitError(MarketDataError):
    pass


class MarketDataProvider:
    provider_name = "unknown"
    is_demo = False

    def get_quote(self, ticker: str) -> EquityQuote:
        if type(self).get_equity_snapshot is not MarketDataProvider.get_equity_snapshot:
            snapshot = self.get_equity_snapshot(ticker)
            return equity_quote_from_snapshot(snapshot, provider=self.provider_name)
        raise NotImplementedError

    def get_option_expirations(self, ticker: str) -> list[date]:
        raise NotImplementedError

    def get_option_chain(self, ticker: str, expiration: date) -> OptionChain:
        if type(self).get_options_chain is not MarketDataProvider.get_options_chain:
            contracts = self.get_options_chain(ticker, expiration)
            return option_chain_from_contracts(
                ticker=ticker,
                expiration=expiration,
                contracts=contracts,
                provider=self.provider_name,
            )
        raise NotImplementedError

    def get_option_snapshot(self, option_symbol: str) -> OptionContractSnapshot:
        raise NotImplementedError

    def get_earnings_date(self, ticker: str) -> Optional[date]:
        return self.get_quote(ticker).next_earnings_date

    def get_historical_volatility(self, ticker: str) -> Optional[float]:
        return None

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="unknown", checked_at=now_utc(), realtime_status="Unknown")

    def get_equity_snapshot(self, ticker: str) -> EquitySnapshot:
        return self.get_quote(ticker).to_snapshot()

    def get_options_chain(self, ticker: str, expiration: date) -> list[OptionContract]:
        return [snapshot.to_option_contract() for snapshot in self.get_option_chain(ticker, expiration).contracts]


class HttpMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        *,
        request_timeout: float = 20,
        max_retries: int = 2,
        freshness_rules: Optional[FreshnessRules] = None,
    ) -> None:
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.freshness_rules = freshness_rules or FreshnessRules()
        self.cache: MarketDataCache[Any] = MarketDataCache()
        self.last_successful_refresh: Optional[datetime] = None
        self.last_error = ""

    def _get_json(self, url: str, *, headers: Optional[dict[str, str]] = None, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        delay = 0.25
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=self.request_timeout)
                if response.status_code == 429:
                    raise RateLimitError("Provider rate limit reached. Try refreshing again in a minute.")
                if response.status_code >= 400:
                    raise MarketDataError(f"{self.provider_name} request failed: {response.status_code} {response.text[:200]}")
                self.last_successful_refresh = now_utc()
                self.last_error = ""
                return response.json()
            except (requests.RequestException, MarketDataError) as exc:
                last_exc = exc
                self.last_error = str(exc)
                if attempt >= self.max_retries:
                    break
                time.sleep(delay)
                delay *= 2
        raise MarketDataError(self.last_error or str(last_exc) or f"{self.provider_name} request failed.")


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

        return EquitySnapshot(ticker=symbol, price=float(price), next_earnings_date=next_earnings, provider="yfinance")


class MassiveProvider(HttpMarketDataProvider):
    provider_name = "Massive"

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        if not api_key:
            raise MarketDataError("MASSIVE_API_KEY is required for the Massive provider.")
        super().__init__(**kwargs)
        self.api_key = api_key
        self.base_url = "https://api.massive.com"
        self.fallback = YFinanceFallback()
        self.realtime_status = _entitlement_status("MASSIVE_DATA_ENTITLEMENT")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def get_quote(self, ticker: str) -> EquityQuote:
        symbol = normalize_ticker(ticker)
        return self.cache.get_or_set(
            ("equity_quote", symbol),
            self.freshness_rules.equity_quote_seconds,
            lambda: self._fetch_quote(symbol),
        )

    def _fetch_quote(self, symbol: str) -> EquityQuote:
        payload = self._get_json(
            f"{self.base_url}/v3/snapshot",
            headers=self._headers(),
            params={"ticker.any_of": symbol},
        )
        snapshot = _first(payload.get("results"))
        if not snapshot:
            fallback = self.fallback.get_equity_snapshot(symbol)
            return equity_quote_from_snapshot(fallback, provider="yfinance")
        return self.map_equity_snapshot(snapshot, symbol)

    def get_option_expirations(self, ticker: str) -> list[date]:
        symbol = normalize_ticker(ticker)
        payload = self._get_json(
            f"{self.base_url}/v3/reference/options/contracts",
            headers=self._headers(),
            params={"underlying_ticker": symbol, "expired": "false", "limit": 1000},
        )
        expirations = sorted(
            {
                _coerce_date(item.get("expiration_date"))
                for item in payload.get("results", [])
                if item.get("expiration_date")
            }
        )
        return [item for item in expirations if item is not None]

    def get_option_chain(self, ticker: str, expiration: date) -> OptionChain:
        symbol = normalize_ticker(ticker)
        cache_key = ("option_chain", symbol, expiration.isoformat())
        return self.cache.get_or_set(
            cache_key,
            self.freshness_rules.option_chain_seconds,
            lambda: self._fetch_option_chain(symbol, expiration),
        )

    def _fetch_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        payload = self._get_json(
            f"{self.base_url}/v3/snapshot/options/{symbol}",
            headers=self._headers(),
            params={"expiration_date": expiration.isoformat(), "limit": 250},
        )
        return self.map_option_chain_payload(payload, symbol, expiration)

    def get_option_snapshot(self, option_symbol: str) -> OptionContractSnapshot:
        underlying = _underlying_from_option_symbol(option_symbol)
        payload = self._get_json(
            f"{self.base_url}/v3/snapshot/options/{underlying}/{option_symbol}",
            headers=self._headers(),
        )
        result = payload.get("results") or payload
        chain = self.map_option_chain_payload({"results": [result]}, underlying, _coerce_date(result.get("details", {}).get("expiration_date")))
        if not chain.contracts:
            raise MarketDataError(f"No option snapshot found for {option_symbol}.")
        return chain.contracts[0]

    def health(self) -> ProviderHealth:
        message = self.last_error
        if not message and self.realtime_status == "Unknown":
            message = "Market-data entitlement is unknown. Set MASSIVE_DATA_ENTITLEMENT=real-time or delayed if your plan is confirmed."
        return ProviderHealth(
            provider=self.provider_name,
            status="error" if self.last_error else "ok",
            checked_at=now_utc(),
            message=message,
            is_realtime=self.realtime_status == "Real-time",
            is_delayed=self.realtime_status == "Delayed",
            realtime_status=self.realtime_status,
            last_successful_refresh=self.last_successful_refresh,
        )

    def map_equity_snapshot(self, snapshot: dict[str, Any], symbol: str) -> EquityQuote:
        retrieved_at = now_utc()
        last_quote = snapshot.get("last_quote") or {}
        last_trade = snapshot.get("last_trade") or {}
        price = (
            _optional_float(snapshot.get("value"))
            or _optional_float(snapshot.get("price"))
            or _optional_float(last_trade.get("price"))
            or _mid(_optional_float(last_quote.get("bid")), _optional_float(last_quote.get("ask")))
        )
        if price is None:
            raise MarketDataError(f"No equity price found for {symbol}.")
        market_timestamp = _timestamp_from_dict(last_quote) or _timestamp_from_dict(last_trade)
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=str(snapshot.get("ticker") or symbol),
            normalized_symbol=symbol,
            retrieved_at=retrieved_at,
            market_timestamp=market_timestamp,
            realtime_status=self.realtime_status,
            source_feed=str(snapshot.get("source_feed") or "SIP"),
            freshness_seconds=self.freshness_rules.equity_quote_seconds,
        )
        return EquityQuote(
            ticker=symbol,
            price=float(price),
            bid=_optional_float(last_quote.get("bid")),
            ask=_optional_float(last_quote.get("ask")),
            last=_optional_float(last_trade.get("price")),
            volume=_optional_int((snapshot.get("day") or {}).get("volume")),
            trace=trace,
        )

    def map_option_chain_payload(self, payload: dict[str, Any], symbol: str, expiration: date) -> OptionChain:
        retrieved_at = now_utc()
        contracts = [
            self.map_option_snapshot(item, symbol=symbol, expiration=expiration, retrieved_at=retrieved_at)
            for item in payload.get("results", [])
        ]
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=symbol,
            normalized_symbol=symbol,
            retrieved_at=retrieved_at,
            market_timestamp=max((item.market_timestamp for item in contracts if item.market_timestamp), default=None),
            realtime_status=self.realtime_status,
            source_feed="OPRA",
            freshness_seconds=self.freshness_rules.option_chain_seconds,
        )
        return OptionChain(underlying_ticker=symbol, expiration=expiration, contracts=contracts, trace=trace)

    def map_option_snapshot(
        self,
        item: dict[str, Any],
        *,
        symbol: str,
        expiration: date,
        retrieved_at: datetime,
    ) -> OptionContractSnapshot:
        details = item.get("details") or {}
        quote = item.get("last_quote") or {}
        trade = item.get("last_trade") or {}
        greeks = item.get("greeks") or {}
        day = item.get("day") or {}
        underlying = item.get("underlying_asset") or {}
        option_symbol = str(details.get("ticker") or item.get("ticker") or "")
        contract_expiration = _coerce_date(details.get("expiration_date")) or expiration
        market_timestamp = _timestamp_from_dict(quote) or _timestamp_from_dict(trade) or parse_timestamp(item.get("last_updated"))
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=option_symbol,
            normalized_symbol=option_symbol,
            retrieved_at=retrieved_at,
            market_timestamp=market_timestamp,
            realtime_status=self.realtime_status,
            source_feed="OPRA",
            freshness_seconds=self.freshness_rules.option_quote_seconds,
        )
        option_quote = OptionQuote(
            bid=_optional_float(quote.get("bid")),
            ask=_optional_float(quote.get("ask")),
            bid_size=_optional_int(quote.get("bid_size")),
            ask_size=_optional_int(quote.get("ask_size")),
            last=_optional_float(trade.get("price")),
            volume=_optional_int(day.get("volume")),
            open_interest=_optional_int(item.get("open_interest")),
            implied_volatility=_optional_iv(item.get("implied_volatility")),
            trace=trace,
        )
        option_greeks = OptionGreeks(
            delta=_optional_float(greeks.get("delta")),
            gamma=_optional_float(greeks.get("gamma")),
            theta=_optional_float(greeks.get("theta")),
            vega=_optional_float(greeks.get("vega")),
            implied_volatility=_optional_iv(item.get("implied_volatility")),
            trace=trace,
        )
        return OptionContractSnapshot(
            underlying_ticker=normalize_ticker(symbol),
            option_symbol=option_symbol,
            expiration=contract_expiration,
            strike=float(details.get("strike_price")),
            option_type=str(details.get("contract_type") or "").lower(),
            quote=option_quote,
            greeks=option_greeks,
            underlying_price=_optional_float(underlying.get("price")),
            trace=trace,
        )


class TradierProvider(HttpMarketDataProvider):
    provider_name = "Tradier"

    def __init__(self, access_token: str, environment: str = "sandbox", **kwargs: Any) -> None:
        if not access_token:
            raise MarketDataError("TRADIER_ACCESS_TOKEN is required for the Tradier provider.")
        super().__init__(**kwargs)
        self.access_token = access_token
        self.environment = environment
        self.base_url = "https://sandbox.tradier.com/v1" if environment == "sandbox" else "https://api.tradier.com/v1"
        self.fallback = YFinanceFallback()
        self.realtime_status = "Delayed" if environment == "sandbox" else _entitlement_status("TRADIER_DATA_ENTITLEMENT")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._get_json(f"{self.base_url}{path}", headers=self._headers(), params=params)

    def get_quote(self, ticker: str) -> EquityQuote:
        symbol = normalize_ticker(ticker)
        return self.cache.get_or_set(
            ("equity_quote", symbol),
            self.freshness_rules.equity_quote_seconds,
            lambda: self._fetch_quote(symbol),
        )

    def _fetch_quote(self, symbol: str) -> EquityQuote:
        payload = self._get("/markets/quotes", {"symbols": symbol, "greeks": "false"})
        quote = payload.get("quotes", {}).get("quote")
        if isinstance(quote, list):
            quote = quote[0] if quote else None
        if not quote:
            return equity_quote_from_snapshot(self.fallback.get_equity_snapshot(symbol), provider="yfinance")

        price = quote.get("last") or quote.get("close") or quote.get("bid") or quote.get("ask")
        if price is None:
            return equity_quote_from_snapshot(self.fallback.get_equity_snapshot(symbol), provider="yfinance")
        fallback_snapshot = self.fallback.get_equity_snapshot(symbol)
        return self.map_equity_quote(quote, symbol, fallback_snapshot.next_earnings_date)

    def get_option_expirations(self, ticker: str) -> list[date]:
        symbol = normalize_ticker(ticker)
        payload = self._get("/markets/options/expirations", {"symbol": symbol, "includeAllRoots": "true"})
        dates = payload.get("expirations", {}).get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        return [parsed for parsed in (_coerce_date(item) for item in dates) if parsed is not None]

    def get_option_chain(self, ticker: str, expiration: date) -> OptionChain:
        symbol = normalize_ticker(ticker)
        return self.cache.get_or_set(
            ("option_chain", symbol, expiration.isoformat()),
            self.freshness_rules.option_chain_seconds,
            lambda: self._fetch_option_chain(symbol, expiration),
        )

    def _fetch_option_chain(self, symbol: str, expiration: date) -> OptionChain:
        payload = self._get("/markets/options/chains", {"symbol": symbol, "expiration": expiration.isoformat(), "greeks": "true"})
        return self.map_option_chain_payload(payload, symbol, expiration)

    def get_option_snapshot(self, option_symbol: str) -> OptionContractSnapshot:
        raise NotImplementedError("Tradier option snapshot lookup by OCC symbol is not implemented in Phase 6.")

    def health(self) -> ProviderHealth:
        message = self.last_error
        if not message and self.realtime_status == "Unknown":
            message = "Market-data entitlement is unknown. Set TRADIER_DATA_ENTITLEMENT=real-time or delayed if your plan is confirmed."
        return ProviderHealth(
            provider=self.provider_name,
            status="error" if self.last_error else "ok",
            checked_at=now_utc(),
            message=message,
            is_realtime=self.realtime_status == "Real-time",
            is_delayed=self.realtime_status == "Delayed",
            realtime_status=self.realtime_status,
            last_successful_refresh=self.last_successful_refresh,
        )

    def map_equity_quote(self, quote: dict[str, Any], symbol: str, next_earnings: Optional[date] = None) -> EquityQuote:
        retrieved_at = now_utc()
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=str(quote.get("symbol") or symbol),
            normalized_symbol=symbol,
            retrieved_at=retrieved_at,
            market_timestamp=parse_timestamp(quote.get("trade_date") or quote.get("bid_date") or quote.get("ask_date")),
            realtime_status=self.realtime_status,
            source_feed="Tradier sandbox" if self.realtime_status == "Delayed" else "Tradier",
            freshness_seconds=self.freshness_rules.equity_quote_seconds,
        )
        return EquityQuote(
            ticker=symbol,
            price=float(quote.get("last") or quote.get("close") or quote.get("bid") or quote.get("ask")),
            bid=_optional_float(quote.get("bid")),
            ask=_optional_float(quote.get("ask")),
            last=_optional_float(quote.get("last")),
            volume=_optional_int(quote.get("volume")),
            next_earnings_date=next_earnings,
            trace=trace,
        )

    def map_option_chain_payload(self, payload: dict[str, Any], symbol: str, expiration: date) -> OptionChain:
        retrieved_at = now_utc()
        options = payload.get("options", {}).get("option") or []
        if isinstance(options, dict):
            options = [options]
        contracts = [self.map_option_snapshot(option, symbol=symbol, expiration=expiration, retrieved_at=retrieved_at) for option in options]
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=symbol,
            normalized_symbol=symbol,
            retrieved_at=retrieved_at,
            market_timestamp=max((item.market_timestamp for item in contracts if item.market_timestamp), default=None),
            realtime_status=self.realtime_status,
            source_feed="Tradier sandbox" if self.realtime_status == "Delayed" else "Tradier",
            freshness_seconds=self.freshness_rules.option_chain_seconds,
        )
        return OptionChain(underlying_ticker=symbol, expiration=expiration, contracts=contracts, trace=trace)

    def map_option_snapshot(
        self,
        option: dict[str, Any],
        *,
        symbol: str,
        expiration: date,
        retrieved_at: datetime,
    ) -> OptionContractSnapshot:
        greeks = option.get("greeks") or {}
        raw_symbol = str(option.get("symbol") or "")
        market_timestamp = parse_timestamp(option.get("trade_date") or option.get("bid_date") or option.get("ask_date"))
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=raw_symbol,
            normalized_symbol=raw_symbol or symbol,
            retrieved_at=retrieved_at,
            market_timestamp=market_timestamp,
            realtime_status=self.realtime_status,
            source_feed="Tradier sandbox" if self.realtime_status == "Delayed" else "Tradier",
            freshness_seconds=self.freshness_rules.option_quote_seconds,
        )
        return OptionContractSnapshot(
            underlying_ticker=symbol,
            option_symbol=raw_symbol,
            expiration=expiration,
            strike=float(option.get("strike")),
            option_type=str(option.get("option_type") or "").lower(),
            quote=OptionQuote(
                bid=_optional_float(option.get("bid")),
                ask=_optional_float(option.get("ask")),
                bid_size=_optional_int(option.get("bidsize")),
                ask_size=_optional_int(option.get("asksize")),
                last=_optional_float(option.get("last")),
                volume=_optional_int(option.get("volume")),
                open_interest=_optional_int(option.get("open_interest")),
                implied_volatility=_optional_iv(option.get("implied_volatility") or greeks.get("mid_iv") or greeks.get("smv_vol")),
                iv_rank=_optional_rank(option.get("iv_rank") or option.get("ivRank") or greeks.get("iv_rank") or greeks.get("ivRank")),
                iv_percentile=_optional_rank(option.get("iv_percentile") or option.get("ivPercentile") or greeks.get("iv_percentile") or greeks.get("ivPercentile")),
                trace=trace,
            ),
            greeks=OptionGreeks(
                delta=_optional_float(greeks.get("delta")),
                gamma=_optional_float(greeks.get("gamma")),
                theta=_optional_float(greeks.get("theta")),
                vega=_optional_float(greeks.get("vega")),
                implied_volatility=_optional_iv(option.get("implied_volatility") or greeks.get("mid_iv") or greeks.get("smv_vol")),
                trace=trace,
            ),
            underlying_price=None,
            trace=trace,
        )


class MockProvider(MarketDataProvider):
    """Small offline provider so the app can be explored before API setup."""

    provider_name = "Demo"
    is_demo = True

    def __init__(self, freshness_rules: Optional[FreshnessRules] = None) -> None:
        self.freshness_rules = freshness_rules or FreshnessRules()
        self.last_successful_refresh = now_utc()

    def get_quote(self, ticker: str) -> EquityQuote:
        symbol = normalize_ticker(ticker)
        prices = {
            "TQQQ": 84.0,
            "NVDA": 164.0,
            "GOOG": 188.0,
            "AMZN": 224.0,
            "MU": 132.0,
            "AMD": 155.0,
            "AEHR": 15.5,
            "CRWD": 320.0,
            "IREN": 9.0,
            "RKLB": 28.0,
            "LUNR": 12.5,
        }
        price = prices.get(symbol, 50.0)
        trace = trace_for(
            provider=self.provider_name,
            raw_symbol=symbol,
            normalized_symbol=symbol,
            retrieved_at=now_utc(),
            market_timestamp=now_utc(),
            realtime_status="Delayed",
            source_feed="Demo data",
            freshness_seconds=self.freshness_rules.equity_quote_seconds,
        )
        return EquityQuote(ticker=symbol, price=price, bid=price - 0.01, ask=price + 0.01, last=price, trace=trace)

    def get_option_expirations(self, ticker: str) -> list[date]:
        return [date.today() + timedelta(days=7)]

    def get_option_chain(self, ticker: str, expiration: date) -> OptionChain:
        symbol = normalize_ticker(ticker)
        price = self.get_quote(symbol).price
        retrieved_at = now_utc()
        contracts: list[OptionContractSnapshot] = []
        for pct, delta, bid in [
            (0.03, 0.10, 0.44),
            (0.05, 0.07, 0.31),
            (0.08, 0.04, 0.18),
        ]:
            for option_type, strike, signed_delta, iv, ask_mult in [
                ("call", round(price * (1 + pct), 2), delta, 0.55, 1.12),
                ("put", round(price * (1 - pct), 2), -delta, 0.60, 1.14),
            ]:
                raw_symbol = f"{symbol}-{expiration.isoformat()}-{option_type}-{strike}"
                trace = trace_for(
                    provider=self.provider_name,
                    raw_symbol=raw_symbol,
                    normalized_symbol=raw_symbol,
                    retrieved_at=retrieved_at,
                    market_timestamp=retrieved_at,
                    realtime_status="Delayed",
                    source_feed="Demo data",
                    freshness_seconds=self.freshness_rules.option_quote_seconds,
                )
                contracts.append(
                    OptionContractSnapshot(
                        underlying_ticker=symbol,
                        option_symbol=raw_symbol,
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        quote=OptionQuote(
                            bid=bid,
                            ask=round(bid * ask_mult, 2),
                            bid_size=10,
                            ask_size=8,
                            last=bid,
                            volume=120 if option_type == "call" else 95,
                            open_interest=850 if option_type == "call" else 640,
                            implied_volatility=iv,
                            iv_rank=0.50,
                            trace=trace,
                        ),
                        greeks=OptionGreeks(delta=signed_delta, gamma=0.02, theta=-0.03, vega=0.08, implied_volatility=iv, trace=trace),
                        underlying_price=price,
                        trace=trace,
                    )
                )
        chain_trace = trace_for(
            provider=self.provider_name,
            raw_symbol=symbol,
            normalized_symbol=symbol,
            retrieved_at=retrieved_at,
            market_timestamp=retrieved_at,
            realtime_status="Delayed",
            source_feed="Demo data",
            freshness_seconds=self.freshness_rules.option_chain_seconds,
        )
        return OptionChain(underlying_ticker=symbol, expiration=expiration, contracts=contracts, trace=chain_trace)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_name,
            status="ok",
            checked_at=now_utc(),
            message="Demo data only.",
            is_realtime=False,
            is_delayed=True,
            realtime_status="Delayed",
            last_successful_refresh=self.last_successful_refresh,
        )


def equity_quote_from_snapshot(snapshot: EquitySnapshot, provider: str) -> EquityQuote:
    trace = trace_for(
        provider=snapshot.provider or provider,
        raw_symbol=snapshot.raw_symbol or snapshot.ticker,
        normalized_symbol=snapshot.normalized_symbol or snapshot.ticker,
        retrieved_at=snapshot.retrieved_at or now_utc(),
        market_timestamp=snapshot.market_timestamp,
        is_realtime=snapshot.is_realtime,
        is_delayed=snapshot.is_delayed,
        realtime_status=_status_from_flags(snapshot.is_realtime, snapshot.is_delayed),
        source_feed=snapshot.source_feed,
        request_status=snapshot.request_status,
    )
    return EquityQuote(
        ticker=normalize_ticker(snapshot.ticker),
        price=snapshot.price,
        last=snapshot.price,
        next_earnings_date=snapshot.next_earnings_date,
        trace=trace,
    )


def option_chain_from_contracts(
    *,
    ticker: str,
    expiration: date,
    contracts: list[OptionContract],
    provider: str,
) -> OptionChain:
    snapshots = []
    for contract in contracts:
        trace = trace_for(
            provider=contract.provider or provider,
            raw_symbol=contract.raw_symbol or contract.symbol or contract.ticker,
            normalized_symbol=contract.normalized_symbol or contract.symbol or contract.ticker,
            retrieved_at=contract.retrieved_at or now_utc(),
            market_timestamp=contract.market_timestamp,
            is_realtime=contract.is_realtime,
            is_delayed=contract.is_delayed,
            realtime_status=_status_from_flags(contract.is_realtime, contract.is_delayed),
            source_feed=contract.source_feed,
            request_status=contract.request_status,
        )
        snapshots.append(
            OptionContractSnapshot(
                underlying_ticker=contract.ticker,
                option_symbol=contract.symbol or "",
                expiration=contract.expiration,
                strike=contract.strike,
                option_type=contract.option_type,
                quote=OptionQuote(
                    bid=contract.bid,
                    ask=contract.ask,
                    bid_size=contract.bid_size,
                    ask_size=contract.ask_size,
                    last=contract.last,
                    volume=contract.volume,
                    open_interest=contract.open_interest,
                    implied_volatility=contract.implied_volatility,
                    iv_rank=contract.iv_rank,
                    iv_percentile=contract.iv_percentile,
                    trace=trace,
                ),
                greeks=OptionGreeks(
                    delta=contract.delta,
                    gamma=contract.gamma,
                    theta=contract.theta,
                    vega=contract.vega,
                    implied_volatility=contract.implied_volatility,
                    trace=trace,
                ),
                underlying_price=contract.underlying_price,
                trace=trace,
            )
        )
    chain_trace = snapshots[0].trace if snapshots else trace_for(provider=provider, raw_symbol=ticker, normalized_symbol=ticker)
    return OptionChain(underlying_ticker=normalize_ticker(ticker), expiration=expiration, contracts=snapshots, trace=chain_trace)


def test_market_data_connection(provider: MarketDataProvider, ticker: str = "SPY") -> ConnectionTestResult:
    checked_at = now_utc()
    try:
        quote = provider.get_quote(ticker)
        expirations = provider.get_option_expirations(ticker)
        if not expirations:
            return ConnectionTestResult(
                provider=provider.provider_name,
                status="Subscription does not include required options data",
                message="No option expirations were returned.",
                checked_at=checked_at,
                quote_ok=True,
                realtime_status=quote.trace.realtime_status,
            )
        chain = provider.get_option_chain(ticker, expirations[0])
    except RateLimitError as exc:
        return ConnectionTestResult(provider=provider.provider_name, status="Provider unavailable", message=str(exc), checked_at=checked_at)
    except MarketDataError as exc:
        message = str(exc)
        lowered = message.lower()
        if "401" in message or "403" in message or "unauthorized" in lowered or "forbidden" in lowered:
            status = "Authentication failed"
        elif "option" in lowered or "subscription" in lowered or "entitlement" in lowered:
            status = "Subscription does not include required options data"
        else:
            status = "Provider unavailable"
        return ConnectionTestResult(provider=provider.provider_name, status=status, message=message, checked_at=checked_at)

    contracts = chain.contracts
    has_bid_ask = any(contract.quote.bid is not None and contract.quote.ask is not None for contract in contracts)
    has_greeks = any(
        contract.greeks.delta is not None
        or contract.greeks.gamma is not None
        or contract.greeks.theta is not None
        or contract.greeks.vega is not None
        for contract in contracts
    )
    has_timestamps = quote.trace.market_timestamp is not None or any(contract.market_timestamp is not None for contract in contracts)
    realtime_status = chain.trace.realtime_status
    if not has_bid_ask:
        status = "Subscription does not include required options data"
        message = "Option chain returned without usable bid/ask quotes."
    elif realtime_status == "Delayed":
        status = "Data returned but is delayed"
        message = "Connection works, but returned data is delayed."
    elif not has_greeks:
        status = "Data returned without Greeks"
        message = "Connection works, but option Greeks were not present."
    else:
        status = "Connected"
        message = "Connection works and returned option bid/ask data."
    return ConnectionTestResult(
        provider=provider.provider_name,
        status=status,
        message=message,
        checked_at=checked_at,
        quote_ok=True,
        expirations_ok=True,
        option_chain_ok=bool(contracts),
        has_bid_ask=has_bid_ask,
        has_greeks=has_greeks,
        has_timestamps=has_timestamps,
        realtime_status=realtime_status,
    )


test_market_data_connection.__test__ = False


def build_provider(provider_name: Optional[str] = None) -> MarketDataProvider:
    selected = (provider_name or _get_setting("OPTIONS_PROVIDER", "auto")).lower().strip()
    massive_key = _usable_setting("MASSIVE_API_KEY") or _usable_setting("POLYGON_API_KEY")
    tradier_key = _usable_setting("TRADIER_ACCESS_TOKEN")

    if selected in {"auto", ""}:
        if massive_key:
            return MassiveProvider(massive_key)
        if tradier_key:
            return TradierProvider(access_token=tradier_key, environment=_get_setting("TRADIER_ENV", "sandbox").lower().strip())
        raise MarketDataError("No live market-data provider is configured. Choose Demo mode or set MASSIVE_API_KEY.")
    if selected in {"massive", "polygon"}:
        return MassiveProvider(massive_key)
    if selected == "tradier":
        return TradierProvider(access_token=tradier_key, environment=_get_setting("TRADIER_ENV", "sandbox").lower().strip())
    if selected in {"demo", "mock"}:
        return MockProvider()
    raise MarketDataError(f"Unsupported OPTIONS_PROVIDER={selected!r}. Use auto, massive, tradier, or demo.")


def available_provider_names() -> list[str]:
    names = []
    if _usable_setting("MASSIVE_API_KEY") or _usable_setting("POLYGON_API_KEY"):
        names.append("Massive")
    if _usable_setting("TRADIER_ACCESS_TOKEN"):
        names.append("Tradier")
    names.append("Demo")
    return names


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


def _usable_setting(name: str) -> str:
    value = _get_setting(name, "").strip()
    if not value or value.lower() in {"replace_me", "your_token_here", "your_key_here"}:
        return ""
    return value


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def _optional_iv(value: Any) -> Optional[float]:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if parsed > 1:
        parsed = parsed / 100
    return max(0.0, min(parsed, 1.0))


def _optional_rank(value: Any) -> Optional[float]:
    return _optional_iv(value)


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


def _first(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, dict):
        return value
    return None


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _timestamp_from_dict(value: dict[str, Any]) -> Optional[datetime]:
    for key in ("sip_timestamp", "participant_timestamp", "last_updated", "timestamp", "t", "date", "biddate", "askdate"):
        parsed = parse_timestamp(value.get(key))
        if parsed:
            return parsed
    return None


def _underlying_from_option_symbol(option_symbol: str) -> str:
    letters = []
    for char in option_symbol:
        if char.isalpha():
            letters.append(char)
        else:
            break
    return "".join(letters).upper()


def _status_from_flags(is_realtime: bool, is_delayed: bool) -> str:
    if is_delayed:
        return "Delayed"
    if is_realtime:
        return "Real-time"
    return "Unknown"


def _entitlement_status(env_name: str) -> str:
    value = _get_setting(env_name, "").strip().lower()
    if value in {"real-time", "realtime", "real_time", "true", "yes"}:
        return "Real-time"
    if value in {"delayed", "false", "no"}:
        return "Delayed"
    return "Unknown"

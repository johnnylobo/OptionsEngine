from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Generic, Optional, TypeVar
from zoneinfo import ZoneInfo

from .models import EquitySnapshot, OptionContract
from .tiers import normalize_ticker


EASTERN = ZoneInfo("America/New_York")
T = TypeVar("T")


@dataclass(frozen=True)
class FreshnessRules:
    equity_quote_seconds: int = 30
    option_quote_seconds: int = 30
    option_chain_seconds: int = 60
    earnings_seconds: int = 86_400
    historical_volatility_seconds: int = 86_400


@dataclass(frozen=True)
class MarketDataTrace:
    provider: str
    retrieved_at: datetime
    market_timestamp: Optional[datetime]
    is_realtime: bool
    is_delayed: bool
    realtime_status: str
    is_stale: bool
    stale_reason: str
    source_feed: str
    request_status: str
    raw_symbol: str
    normalized_symbol: str


@dataclass(frozen=True)
class EquityQuote:
    ticker: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    next_earnings_date: Optional[date] = None
    trace: MarketDataTrace = None  # type: ignore[assignment]

    def to_snapshot(self) -> EquitySnapshot:
        return EquitySnapshot(
            ticker=self.ticker,
            price=self.price,
            next_earnings_date=self.next_earnings_date,
            provider=self.trace.provider,
            retrieved_at=self.trace.retrieved_at,
            market_timestamp=self.trace.market_timestamp,
            is_realtime=self.trace.is_realtime,
            is_delayed=self.trace.is_delayed,
            is_stale=self.trace.is_stale,
            stale_reason=self.trace.stale_reason,
            source_feed=self.trace.source_feed,
            request_status=self.trace.request_status,
            raw_symbol=self.trace.raw_symbol,
            normalized_symbol=self.trace.normalized_symbol,
        )


@dataclass(frozen=True)
class OptionQuote:
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    implied_volatility: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    trace: MarketDataTrace = None  # type: ignore[assignment]


@dataclass(frozen=True)
class OptionGreeks:
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None
    trace: MarketDataTrace = None  # type: ignore[assignment]


@dataclass(frozen=True)
class OptionContractSnapshot:
    underlying_ticker: str
    option_symbol: str
    expiration: date
    strike: float
    option_type: str
    quote: OptionQuote
    greeks: OptionGreeks
    underlying_price: Optional[float] = None
    trace: MarketDataTrace = None  # type: ignore[assignment]

    @property
    def ticker(self) -> str:
        return self.underlying_ticker

    @property
    def bid(self) -> float:
        return float(self.quote.bid or 0.0)

    @property
    def ask(self) -> float:
        return float(self.quote.ask or 0.0)

    @property
    def bid_size(self) -> Optional[int]:
        return self.quote.bid_size

    @property
    def ask_size(self) -> Optional[int]:
        return self.quote.ask_size

    @property
    def last(self) -> Optional[float]:
        return self.quote.last

    @property
    def volume(self) -> Optional[int]:
        return self.quote.volume

    @property
    def open_interest(self) -> Optional[int]:
        return self.quote.open_interest

    @property
    def delta(self) -> Optional[float]:
        return self.greeks.delta

    @property
    def gamma(self) -> Optional[float]:
        return self.greeks.gamma

    @property
    def theta(self) -> Optional[float]:
        return self.greeks.theta

    @property
    def vega(self) -> Optional[float]:
        return self.greeks.vega

    @property
    def implied_volatility(self) -> Optional[float]:
        return self.greeks.implied_volatility or self.quote.implied_volatility

    @property
    def iv_rank(self) -> Optional[float]:
        return self.quote.iv_rank

    @property
    def iv_percentile(self) -> Optional[float]:
        return self.quote.iv_percentile

    @property
    def symbol(self) -> str:
        return self.option_symbol

    @property
    def provider(self) -> str:
        return self.trace.provider

    @property
    def retrieved_at(self) -> datetime:
        return self.trace.retrieved_at

    @property
    def market_timestamp(self) -> Optional[datetime]:
        return self.trace.market_timestamp

    @property
    def is_realtime(self) -> bool:
        return self.trace.is_realtime

    @property
    def is_delayed(self) -> bool:
        return self.trace.is_delayed

    @property
    def is_stale(self) -> bool:
        return self.trace.is_stale

    @property
    def stale_reason(self) -> str:
        return self.trace.stale_reason

    @property
    def source_feed(self) -> str:
        return self.trace.source_feed

    def to_option_contract(self) -> OptionContract:
        return OptionContract(
            ticker=self.underlying_ticker,
            expiration=self.expiration,
            option_type="call" if self.option_type.lower().startswith("c") else "put",
            strike=self.strike,
            bid=self.bid,
            ask=self.ask,
            delta=self.delta,
            iv_rank=self.iv_rank,
            iv_percentile=self.iv_percentile,
            volume=self.volume,
            open_interest=self.open_interest,
            symbol=self.option_symbol,
            bid_size=self.bid_size,
            ask_size=self.ask_size,
            last=self.last,
            gamma=self.gamma,
            theta=self.theta,
            vega=self.vega,
            implied_volatility=self.implied_volatility,
            underlying_price=self.underlying_price,
            provider=self.provider,
            retrieved_at=self.retrieved_at,
            market_timestamp=self.market_timestamp,
            is_realtime=self.is_realtime,
            is_delayed=self.is_delayed,
            is_stale=self.is_stale,
            stale_reason=self.stale_reason,
            source_feed=self.source_feed,
            request_status=self.trace.request_status,
            raw_symbol=self.trace.raw_symbol,
            normalized_symbol=self.trace.normalized_symbol,
        )


@dataclass(frozen=True)
class OptionChain:
    underlying_ticker: str
    expiration: date
    contracts: list[OptionContractSnapshot]
    trace: MarketDataTrace


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    status: str
    checked_at: datetime
    message: str = ""
    is_realtime: bool = False
    is_delayed: bool = False
    realtime_status: str = "Unknown"
    last_successful_refresh: Optional[datetime] = None


@dataclass(frozen=True)
class ConnectionTestResult:
    provider: str
    status: str
    message: str
    checked_at: datetime
    quote_ok: bool = False
    expirations_ok: bool = False
    option_chain_ok: bool = False
    has_bid_ask: bool = False
    has_greeks: bool = False
    has_timestamps: bool = False
    realtime_status: str = "Unknown"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: object) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000_000:
            numeric = numeric / 1_000_000_000
        elif numeric > 10_000_000_000:
            numeric = numeric / 1_000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return parse_timestamp(int(text))
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def market_is_open(at: Optional[datetime] = None) -> bool:
    moment = at or now_utc()
    local = moment.astimezone(EASTERN)
    if local.weekday() >= 5:
        return False
    return time(9, 30) <= local.time() <= time(16, 0)


def with_stale_status(
    trace: MarketDataTrace,
    *,
    max_age_seconds: int,
    now: Optional[datetime] = None,
) -> MarketDataTrace:
    if not market_is_open(now):
        if trace.realtime_status == "Real-time":
            return replace(
                trace,
                is_realtime=False,
                is_stale=False,
                realtime_status="Market closed",
                stale_reason="Market is closed; latest official quote is not live.",
            )
        return replace(trace, is_stale=False, stale_reason="")
    timestamp = trace.market_timestamp or trace.retrieved_at
    age = (now or now_utc()) - timestamp
    if age > timedelta(seconds=max_age_seconds):
        return replace(trace, is_stale=True, stale_reason=f"Data older than {max_age_seconds} seconds.")
    return trace


def trace_for(
    *,
    provider: str,
    raw_symbol: str,
    normalized_symbol: Optional[str] = None,
    retrieved_at: Optional[datetime] = None,
    market_timestamp: Optional[datetime] = None,
    is_realtime: bool = False,
    is_delayed: bool = False,
    realtime_status: str = "Unknown",
    source_feed: str = "",
    request_status: str = "ok",
    freshness_seconds: int = 30,
) -> MarketDataTrace:
    timestamp = retrieved_at or now_utc()
    status = _realtime_status(realtime_status, is_realtime, is_delayed)
    trace = MarketDataTrace(
        provider=provider,
        retrieved_at=timestamp,
        market_timestamp=market_timestamp,
        is_realtime=status == "Real-time",
        is_delayed=status == "Delayed",
        realtime_status=status,
        is_stale=False,
        stale_reason="",
        source_feed=source_feed,
        request_status=request_status,
        raw_symbol=str(raw_symbol),
        normalized_symbol=normalize_ticker(normalized_symbol or str(raw_symbol)),
    )
    return with_stale_status(trace, max_age_seconds=freshness_seconds, now=timestamp)


def _realtime_status(realtime_status: str, is_realtime: bool, is_delayed: bool) -> str:
    normalized = realtime_status.strip().lower()
    if normalized in {"real-time", "realtime", "real_time"}:
        return "Real-time"
    if normalized == "delayed":
        return "Delayed"
    if normalized == "unknown":
        return "Unknown"
    if normalized in {"", "derive"}:
        if is_realtime:
            return "Real-time"
        if is_delayed:
            return "Delayed"
    return "Unknown"


class MarketDataCache(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[tuple[str, object], tuple[datetime, T]] = {}

    def get(self, key: tuple[str, object], ttl_seconds: int, now: Optional[datetime] = None) -> Optional[T]:
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, value = item
        if (now or now_utc()) - stored_at > timedelta(seconds=ttl_seconds):
            return None
        return value

    def set(self, key: tuple[str, object], value: T, now: Optional[datetime] = None) -> T:
        self._items[key] = (now or now_utc(), value)
        return value

    def get_or_set(
        self,
        key: tuple[str, object],
        ttl_seconds: int,
        loader: Callable[[], T],
        now: Optional[datetime] = None,
    ) -> T:
        cached = self.get(key, ttl_seconds, now)
        if cached is not None:
            return cached
        return self.set(key, loader(), now)

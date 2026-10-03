from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .cache import TtlCache
from .market_models import Candle
from .scanner_config import ScannerSettings
from .transport import SafeGetTransport


@dataclass(frozen=True, slots=True)
class MarketDataResult:
    data: object | None
    error_category: str | None
    http_status: int | None
    requests_made: int
    cache_hit: bool


class BinanceMarketDataClient:
    def __init__(
        self,
        base_url: str,
        transport: SafeGetTransport,
        settings: ScannerSettings,
        cache: TtlCache | None = None,
    ) -> None:
        self._base_url = base_url
        self._transport = transport
        self._settings = settings
        self._cache = cache or TtlCache()

    async def exchange_info(self) -> MarketDataResult:
        return await self._cached_get(
            "exchange_info",
            "/api/v3/exchangeInfo",
            ttl=self._settings.exchange_info_ttl_seconds,
        )

    async def ticker_24h(self) -> MarketDataResult:
        return await self._cached_get(
            "ticker_24h", "/api/v3/ticker/24hr", ttl=self._settings.ticker_ttl_seconds
        )

    async def book_ticker(self) -> MarketDataResult:
        return await self._cached_get(
            "book_ticker",
            "/api/v3/ticker/bookTicker",
            ttl=self._settings.ticker_ttl_seconds,
        )

    async def klines(self, symbol: str, interval: str) -> MarketDataResult:
        key = f"klines:{symbol}:{interval}:{self._settings.kline_limit}"
        result = await self._cached_get(
            key,
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": self._settings.kline_limit},
            ttl=self._settings.kline_ttl_seconds,
        )
        if result.error_category:
            return result
        try:
            candles = normalize_klines(result.data)
        except ValueError:
            return MarketDataResult(
                None,
                "malformed_kline_data",
                result.http_status,
                result.requests_made,
                result.cache_hit,
            )
        return MarketDataResult(
            candles,
            None,
            result.http_status,
            result.requests_made,
            result.cache_hit,
        )

    async def _cached_get(
        self,
        key: str,
        path: str,
        *,
        ttl: float,
        params: dict[str, object] | None = None,
    ) -> MarketDataResult:
        cached = self._cache.get(key)
        if cached is not None:
            return MarketDataResult(cached, None, 200, 0, True)
        result = await self._transport.get(self._base_url, path, params=params)
        if result.error_category is None:
            self._cache.set(key, result.data, ttl)
        return MarketDataResult(
            result.data,
            result.error_category,
            result.status_code,
            result.attempts,
            False,
        )


def normalize_klines(data: object) -> list[Candle]:
    if not isinstance(data, list):
        raise ValueError("klines must be a list")
    candles = []
    previous_open_time: int | None = None
    for row in data:
        if not isinstance(row, list) or len(row) < 11:
            raise ValueError("malformed kline row")
        try:
            open_time = _integer(row[0])
            close_time = _integer(row[6])
            open_price = _decimal(row[1])
            high = _decimal(row[2])
            low = _decimal(row[3])
            close = _decimal(row[4])
            volume = _decimal(row[5])
            quote_volume = _decimal(row[7])
            trades = _integer(row[8])
            taker_buy_base = _decimal(row[9])
            taker_buy_quote = _decimal(row[10])
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("malformed kline value") from exc
        if (
            close_time < open_time
            or high < max(open_price, close, low)
            or low > min(open_price, close, high)
            or min(open_price, high, low, close, volume, quote_volume, taker_buy_base, taker_buy_quote) < 0
            or trades < 0
            or (previous_open_time is not None and open_time <= previous_open_time)
        ):
            raise ValueError("invalid kline values")
        candles.append(
            Candle(
                open_time=open_time,
                close_time=close_time,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                quote_volume=quote_volume,
                trades=trades,
                taker_buy_base_volume=taker_buy_base,
                taker_buy_quote_volume=taker_buy_quote,
            )
        )
        previous_open_time = open_time
    return candles


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not decimal")
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("decimal must be finite")
    return parsed


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not integer")
    parsed = int(value)
    if str(parsed) != str(value) and not isinstance(value, int):
        raise ValueError("integer is not exact")
    return parsed


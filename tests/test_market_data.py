from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from adaptive_grid_manager.cache import TtlCache
from adaptive_grid_manager.market_data import BinanceMarketDataClient, normalize_klines
from adaptive_grid_manager.scanner_config import ScannerSettings
from adaptive_grid_manager.transport import SafeGetTransport


def run(coro):
    return asyncio.run(coro)


def scanner_settings(**overrides):
    values = dict(
        max_concurrency=2,
        max_limit=10,
        quote_volume_min=Decimal("1000"),
        trade_count_min=10,
        spread_pct_max=Decimal("1"),
        book_liquidity_quote_min=Decimal("100"),
        denylist=frozenset(),
        kline_limit=50,
        minimum_candles=10,
        indicator_period=3,
        volatility_period=5,
        rolling_period=5,
        volume_period=5,
        oscillation_period=5,
        exchange_info_ttl_seconds=10,
        ticker_ttl_seconds=10,
        kline_ttl_seconds=10,
    )
    values.update(overrides)
    return ScannerSettings(**values)


def kline_row(index, value="1.000000000000000001"):
    return [
        index * 1000,
        value,
        "2.000000000000000001",
        "0.500000000000000001",
        value,
        "123.000000000000000001",
        index * 1000 + 999,
        "246.000000000000000001",
        42,
        "60.000000000000000001",
        "120.000000000000000001",
        "0",
    ]


def test_kline_normalization_preserves_decimal_precision():
    candle = normalize_klines([kline_row(1)])[0]
    assert candle.open == Decimal("1.000000000000000001")
    assert candle.quote_volume == Decimal("246.000000000000000001")


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-list",
        [[1, "bad"]],
        [[0, "1", "NaN", "0", "1", "1", 1, "1", 1, "1", "1"]],
        [[0, "2", "1", "0", "2", "1", 1, "1", 1, "1", "1"]],
    ],
)
def test_malformed_kline_data_is_rejected(payload):
    with pytest.raises(ValueError):
        normalize_klines(payload)


def test_cache_hit_and_expiry():
    now = [0.0]
    cache = TtlCache(clock=lambda: now[0])
    assert cache.get("key") is None
    cache.set("key", {"value": 1}, 5)
    assert cache.get("key") == {"value": 1}
    now[0] = 5.0
    assert cache.get("key") is None
    assert (cache.hits, cache.misses) == (1, 2)


def test_market_data_cache_avoids_second_binance_call():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"symbols": []})

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tx = SafeGetTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=0,
        backoff_base_seconds=0.01,
        retry_after_cap_seconds=1,
        client=raw,
    )
    client = BinanceMarketDataClient("https://binance.test", tx, scanner_settings())
    first = run(client.exchange_info())
    second = run(client.exchange_info())
    run(raw.aclose())
    assert calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True


def test_market_data_429_uses_existing_bounded_get_retry_policy():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": -1003})

    async def no_sleep(_delay):
        return None

    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tx = SafeGetTransport(
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=1,
        backoff_base_seconds=0.01,
        retry_after_cap_seconds=1,
        client=raw,
        sleep=no_sleep,
    )
    result = run(BinanceMarketDataClient("https://binance.test", tx, scanner_settings()).exchange_info())
    run(raw.aclose())
    assert calls == 2
    assert result.requests_made == 2
    assert result.error_category == "rate_limited"


from __future__ import annotations

import asyncio
from decimal import Decimal
import json

from adaptive_grid_manager.app import ConnectivityApp
from adaptive_grid_manager.config import Settings
from adaptive_grid_manager.market_data import MarketDataResult
from adaptive_grid_manager.market_models import Candle
from adaptive_grid_manager.scanner import MarketScanner
from adaptive_grid_manager.scanner_config import ScannerSettings


def run(coro):
    return asyncio.run(coro)


def scanner_settings(**overrides):
    values = dict(
        max_concurrency=2,
        max_limit=10,
        quote_volume_min=Decimal("1000"),
        trade_count_min=10,
        spread_pct_max=Decimal("2"),
        book_liquidity_quote_min=Decimal("100"),
        denylist=frozenset(),
        kline_limit=40,
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


def symbol(name, *, base=None, quote="USDT", status="TRADING", spot=True, **extra):
    return {
        "symbol": name,
        "baseAsset": base or name.removesuffix(quote),
        "quoteAsset": quote,
        "status": status,
        "isSpotTradingAllowed": spot,
        "permissions": ["SPOT"] if spot else [],
        **extra,
    }


def candles(count=40):
    result = []
    for index in range(count):
        close = Decimal("100") + Decimal(index % 4) - Decimal("1.5")
        result.append(
            Candle(
                open_time=index * 1000,
                close_time=index * 1000 + 999,
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("10") + index,
                quote_volume=(Decimal("10") + index) * close,
                trades=100,
                taker_buy_base_volume=Decimal("5"),
                taker_buy_quote_volume=Decimal("500"),
            )
        )
    return result


class FakeMarketData:
    def __init__(self, symbols, *, ticker=None, book=None, kline_errors=None, candle_count=40):
        self.symbols = symbols
        self.ticker = ticker or [
            {"symbol": item["symbol"], "quoteVolume": "5000", "count": 100}
            for item in symbols
        ]
        self.book = book or [
            {
                "symbol": item["symbol"],
                "bidPrice": "99",
                "askPrice": "100",
                "bidQty": "10",
                "askQty": "10",
            }
            for item in symbols
        ]
        self.kline_errors = kline_errors or {}
        self.candle_count = candle_count
        self.active = 0
        self.max_active = 0

    async def exchange_info(self):
        return MarketDataResult({"symbols": self.symbols}, None, 200, 1, False)

    async def ticker_24h(self):
        return MarketDataResult(self.ticker, None, 200, 1, False)

    async def book_ticker(self):
        return MarketDataResult(self.book, None, 200, 1, False)

    async def klines(self, symbol_name, interval):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        error = self.kline_errors.get((symbol_name, interval))
        if error:
            return MarketDataResult(None, error, 429 if error == "rate_limited" else 503, 2, False)
        return MarketDataResult(candles(self.candle_count), None, 200, 1, False)


def rejection_reasons(result):
    return {reason for item in result["rejections"] for reason in item["reasons"]}


def test_usdt_spot_universe_filtering_leveraged_and_denylist():
    symbols = [
        symbol("BTCUSDT"),
        symbol("ETHBTC", quote="BTC", base="ETH"),
        symbol("OLDUSDT", status="BREAK"),
        symbol("NOSPOTUSDT", spot=False),
        symbol("BTCUPUSDT", base="BTCUP"),
        symbol("DENYUSDT"),
    ]
    data = FakeMarketData(symbols)
    scanner = MarketScanner(
        data, scanner_settings(denylist=frozenset({"DENYUSDT"})), clock=lambda: 10000
    )
    result = run(scanner.scan())
    reasons = rejection_reasons(result)
    assert result["universe_count"] == 6
    assert result["analyzed_count"] == 1
    assert result["snapshots"][0]["symbol"] == "BTCUSDT"
    assert {
        "non_usdt_quote",
        "inactive_symbol",
        "non_spot_symbol",
        "leveraged_or_tokenized_product",
        "denylisted",
    } <= reasons


def test_tokenized_equities_are_excluded_without_rejecting_normal_crypto():
    symbols = [
        symbol("AAPLBUSDT", base="AAPLB"),
        symbol("MSFTBUSDT", base="MSFTB"),
        symbol("SPYBUSDT", base="SPYB"),
        symbol("QQQBUSDT", base="QQQB"),
        symbol("METADATAUSDT", base="METADATA", assetType="TOKENIZED_EQUITY"),
        symbol("ARBUSDT", base="ARB"),
        symbol("JUPUSDT", base="JUP"),
    ]
    result = run(
        MarketScanner(FakeMarketData(symbols), scanner_settings(), clock=lambda: 10000).scan()
    )
    rejected = {item["symbol"]: item["reasons"] for item in result["rejections"]}
    assert "tokenized_equity_fallback_classification" in rejected["AAPLBUSDT"]
    assert "tokenized_equity_fallback_classification" in rejected["MSFTBUSDT"]
    assert "tokenized_equity_fallback_classification" in rejected["SPYBUSDT"]
    assert "tokenized_equity_fallback_classification" in rejected["QQQBUSDT"]
    assert "non_crypto_instrument_metadata" in rejected["METADATAUSDT"]
    assert {item["symbol"] for item in result["snapshots"]} == {"ARBUSDT", "JUPUSDT"}


def test_volume_and_spread_prefilter_rejections():
    symbols = [symbol("LOWUSDT"), symbol("WIDEUSDT")]
    ticker = [
        {"symbol": "LOWUSDT", "quoteVolume": "999", "count": 100},
        {"symbol": "WIDEUSDT", "quoteVolume": "5000", "count": 100},
    ]
    book = [
        {"symbol": "LOWUSDT", "bidPrice": "99", "askPrice": "100", "bidQty": "10", "askQty": "10"},
        {"symbol": "WIDEUSDT", "bidPrice": "90", "askPrice": "100", "bidQty": "10", "askQty": "10"},
    ]
    result = run(MarketScanner(FakeMarketData(symbols, ticker=ticker, book=book), scanner_settings(), clock=lambda: 10000).scan())
    reasons = rejection_reasons(result)
    assert "quote_volume_below_threshold" in reasons
    assert "spread_above_threshold" in reasons
    assert result["prefilter_passed_count"] == 0


def test_insufficient_history_is_rejected_after_prefilter():
    result = run(
        MarketScanner(
            FakeMarketData([symbol("NEWUSDT")], candle_count=5),
            scanner_settings(minimum_candles=10),
            clock=lambda: 10000,
        ).scan()
    )
    assert result["analyzed_count"] == 0
    assert "15m:insufficient_history" in rejection_reasons(result)


def test_bounded_concurrency():
    symbols = [symbol(f"ASSET{index}USDT") for index in range(6)]
    data = FakeMarketData(symbols)
    result = run(
        MarketScanner(data, scanner_settings(max_concurrency=2), clock=lambda: 10000).scan()
    )
    assert result["analyzed_count"] == 6
    assert data.max_active <= 2
    assert data.max_active == 2


def test_partial_binance_failure_does_not_abort_other_symbols():
    symbols = [symbol("GOODUSDT"), symbol("PARTIALUSDT")]
    data = FakeMarketData(symbols, kline_errors={("PARTIALUSDT", "4h"): "server_error"})
    result = run(MarketScanner(data, scanner_settings(), clock=lambda: 10000).scan())
    assert [item["symbol"] for item in result["snapshots"]] == ["GOODUSDT"]
    assert "4h:server_error" in rejection_reasons(result)
    assert result["diagnostics"]["errors_by_category"] == {"server_error": 1}
    assert result["diagnostics"]["binance_requests_made"] == 12


def test_scanner_works_without_credentials_and_output_has_no_nonfinite_numbers():
    settings_without_credentials = Settings(
        endpoints=(("test", "https://binance.test"),),
        authenticated_endpoint="https://binance.test",
        api_key=None,
        api_secret=None,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=1,
        backoff_base_seconds=0.1,
        retry_after_cap_seconds=1,
        recv_window_ms=5000,
    )
    assert settings_without_credentials.authenticated_diagnostics_enabled is False
    result = run(
        MarketScanner(FakeMarketData([symbol("BTCUSDT")]), scanner_settings(), clock=lambda: 10000).scan()
    )
    json.dumps(result, allow_nan=False)
    assert result["analyzed_count"] == 1
    assert set(result["snapshots"][0]["timeframes"]) == {"15m", "1h", "4h", "1d"}


def test_limit_parameter_caps_analysis_without_changing_prefilter_count():
    symbols = [symbol(f"ASSET{index}USDT") for index in range(3)]
    result = run(
        MarketScanner(FakeMarketData(symbols), scanner_settings(), clock=lambda: 10000).scan(limit=1)
    )
    assert result["prefilter_passed_count"] == 3
    assert result["analyzed_count"] == 1
    assert result["diagnostics"]["binance_requests_made"] == 7


def test_scanner_api_parses_limit():
    class StubScanner:
        def __init__(self):
            self.limit = None

        async def scan(self, limit=None):
            self.limit = limit
            return {"analyzed_count": limit}

    async def request(app, query):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "GET",
                "path": "/scanner/scan",
                "query_string": query,
            },
            receive,
            send,
        )
        return sent[0]["status"], json.loads(sent[1]["body"])

    cfg = Settings(
        endpoints=(("test", "https://binance.test"),),
        authenticated_endpoint="https://binance.test",
        api_key=None,
        api_secret=None,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=0,
        backoff_base_seconds=0.1,
        retry_after_cap_seconds=1,
        recv_window_ms=5000,
    )
    stub = StubScanner()
    app = ConnectivityApp(cfg, object(), stub)
    status, body = run(request(app, b"limit=2"))
    assert status == 200
    assert body["analyzed_count"] == 2
    assert stub.limit == 2
    status, body = run(request(app, b"limit=invalid"))
    assert status == 400
    assert body == {"error": "invalid_limit"}

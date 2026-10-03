from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import time
from typing import Any

from .indicators import (
    adx,
    atr,
    range_crossing_count,
    range_width_pct,
    realized_volatility,
    rolling_high_low,
    rsi,
    spread_percentage,
    volume_statistics,
)
from .market_data import BinanceMarketDataClient, MarketDataResult
from .market_models import Candle, SymbolDescriptor, safe_api_value
from .scanner_config import ScannerSettings, TIMEFRAMES


logger = logging.getLogger(__name__)


def build_timeframe_metrics(
    candles: list[Candle], settings: ScannerSettings
) -> dict[str, object]:
    """Build the production timeframe feature set from already-closed candles."""
    current_price = candles[-1].close
    atr_value = atr(candles, settings.indicator_period)
    rsi_value = rsi(candles, settings.indicator_period)
    adx_value = adx(candles, settings.indicator_period)
    volatility = realized_volatility(candles, settings.volatility_period)
    rolling = rolling_high_low(candles, settings.rolling_period)
    volumes = volume_statistics(candles, settings.volume_period)
    crossings = range_crossing_count(candles, settings.oscillation_period)
    required = (atr_value, rsi_value, adx_value, volatility, rolling, volumes, crossings)
    quality = "ok" if all(value is not None for value in required) else "insufficient_history"
    rolling_high, rolling_low = rolling if rolling else (None, None)
    return {
        "current_price": current_price,
        "atr": atr_value,
        "atr_percentage": None
        if atr_value is None or current_price <= 0
        else atr_value / current_price * Decimal(100),
        "rsi": rsi_value,
        "adx": adx_value,
        "realized_volatility": volatility,
        "rolling_high": rolling_high,
        "rolling_low": rolling_low,
        "range_width_percentage": None
        if rolling is None
        else range_width_pct(rolling_high, rolling_low),
        "volume_statistics": volumes,
        "oscillation_crossings": crossings,
        "candle_count": len(candles),
        "data_quality_status": quality,
    }


class MarketScanner:
    def __init__(
        self,
        market_data: BinanceMarketDataClient,
        settings: ScannerSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._market_data = market_data
        self._settings = settings
        self._clock = clock

    async def scan(self, limit: int | None = None) -> dict[str, object]:
        started = time.perf_counter()
        scan_epoch = self._clock()
        diagnostics = {
            "binance_requests_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors_by_category": Counter(),
        }
        rejections: list[dict[str, object]] = []

        exchange_result = await self._market_data.exchange_info()
        self._record(exchange_result, diagnostics)
        symbols = self._exchange_symbols(exchange_result.data)
        if exchange_result.error_category or symbols is None:
            category = exchange_result.error_category or "malformed_exchange_info"
            if exchange_result.error_category is None:
                diagnostics["errors_by_category"][category] += 1
            return self._response(
                scan_epoch, started, 0, 0, 0, [], rejections, diagnostics, fatal_error=category
            )

        universe_count = len(symbols)
        eligible: list[SymbolDescriptor] = []
        for raw in symbols:
            descriptor, reasons = self._universe_candidate(raw)
            if reasons:
                rejections.append(self._rejection(raw, "universe", reasons))
            elif descriptor:
                eligible.append(descriptor)

        ticker_result, book_result = await asyncio.gather(
            self._market_data.ticker_24h(), self._market_data.book_ticker()
        )
        self._record(ticker_result, diagnostics)
        self._record(book_result, diagnostics)
        ticker_map = self._symbol_map(ticker_result.data)
        book_map = self._symbol_map(book_result.data)
        global_prefilter_errors = []
        if ticker_result.error_category or ticker_map is None:
            category = ticker_result.error_category or "malformed_ticker_24h"
            global_prefilter_errors.append(category)
            if ticker_result.error_category is None:
                diagnostics["errors_by_category"][category] += 1
        if book_result.error_category or book_map is None:
            category = book_result.error_category or "malformed_book_ticker"
            global_prefilter_errors.append(category)
            if book_result.error_category is None:
                diagnostics["errors_by_category"][category] += 1

        passing: list[tuple[SymbolDescriptor, dict[str, object]]] = []
        for descriptor in eligible:
            if global_prefilter_errors:
                rejections.append(
                    self._rejection(
                        descriptor,
                        "prefilter",
                        [f"prefilter_data_unavailable:{reason}" for reason in global_prefilter_errors],
                    )
                )
                continue
            metrics, reasons = self._prefilter(
                descriptor, ticker_map.get(descriptor.symbol), book_map.get(descriptor.symbol)
            )
            if reasons:
                rejections.append(self._rejection(descriptor, "prefilter", reasons, metrics))
            else:
                passing.append((descriptor, metrics))

        prefilter_passed_count = len(passing)
        prefilter_rejected_count = len(rejections)
        analysis_targets = passing[:limit] if limit is not None else passing
        analyzed = await self._analyze(analysis_targets, int(scan_epoch * 1000), diagnostics)
        snapshots = []
        for descriptor, metrics, timeframe_data, errors in analyzed:
            if errors:
                rejections.append(self._rejection(descriptor, "market_data", errors, metrics))
            else:
                snapshots.append(
                    {
                        "symbol": descriptor.symbol,
                        "base_asset": descriptor.base_asset,
                        "quote_asset": descriptor.quote_asset,
                        "prefilter_metrics": metrics,
                        "prefilter_status": "passed",
                        "rejection_reasons": [],
                        "timeframes": timeframe_data,
                    }
                )

        return self._response(
            scan_epoch,
            started,
            universe_count,
            prefilter_passed_count,
            prefilter_rejected_count,
            snapshots,
            rejections,
            diagnostics,
        )

    async def _analyze(
        self,
        targets: list[tuple[SymbolDescriptor, dict[str, object]]],
        scan_time_ms: int,
        diagnostics: dict[str, Any],
    ) -> list[tuple[SymbolDescriptor, dict[str, object], dict[str, object], list[str]]]:
        queue: asyncio.Queue[tuple[int, SymbolDescriptor, dict[str, object]]] = asyncio.Queue()
        for index, (descriptor, metrics) in enumerate(targets):
            queue.put_nowait((index, descriptor, metrics))
        results: list[Any] = [None] * len(targets)

        async def worker() -> None:
            while True:
                try:
                    index, descriptor, metrics = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    timeframes: dict[str, object] = {}
                    errors: list[str] = []
                    for timeframe in TIMEFRAMES:
                        result = await self._market_data.klines(descriptor.symbol, timeframe)
                        self._record(result, diagnostics)
                        if result.error_category:
                            errors.append(f"{timeframe}:{result.error_category}")
                            continue
                        candles = [
                            candle
                            for candle in result.data
                            if isinstance(candle, Candle) and candle.close_time <= scan_time_ms
                        ]
                        if len(candles) < self._settings.minimum_candles:
                            errors.append(f"{timeframe}:insufficient_history")
                            continue
                        metrics_for_timeframe = self._timeframe_metrics(candles)
                        if metrics_for_timeframe["data_quality_status"] != "ok":
                            errors.append(f"{timeframe}:insufficient_indicator_history")
                            continue
                        timeframes[timeframe] = metrics_for_timeframe
                    results[index] = (descriptor, metrics, timeframes, errors)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(self._settings.max_concurrency, len(targets)))
        ]
        if workers:
            await asyncio.gather(*workers)
        return results

    def _timeframe_metrics(self, candles: list[Candle]) -> dict[str, object]:
        return build_timeframe_metrics(candles, self._settings)

    def _universe_candidate(
        self, raw: dict[str, object]
    ) -> tuple[SymbolDescriptor | None, list[str]]:
        symbol = raw.get("symbol")
        base = raw.get("baseAsset")
        quote = raw.get("quoteAsset")
        if not all(isinstance(value, str) and value for value in (symbol, base, quote)):
            return None, ["malformed_symbol_metadata"]
        reasons = []
        if quote != "USDT":
            reasons.append("non_usdt_quote")
        if raw.get("status") != "TRADING":
            reasons.append("inactive_symbol")
        if not self._spot_allowed(raw):
            reasons.append("non_spot_symbol")
        if self._leveraged_token(raw):
            reasons.append("leveraged_or_tokenized_product")
        non_crypto_reason = self._non_crypto_product(raw)
        if non_crypto_reason:
            reasons.append(non_crypto_reason)
        if symbol in self._settings.denylist:
            reasons.append("denylisted")
        return SymbolDescriptor(symbol, base, quote), reasons

    def _prefilter(
        self,
        descriptor: SymbolDescriptor,
        ticker: dict[str, object] | None,
        book: dict[str, object] | None,
    ) -> tuple[dict[str, object], list[str]]:
        if ticker is None or book is None:
            return {}, ["prefilter_symbol_data_missing"]
        quote_volume = self._decimal(ticker.get("quoteVolume"))
        trades = self._integer(ticker.get("count"))
        bid = self._decimal(book.get("bidPrice"))
        ask = self._decimal(book.get("askPrice"))
        bid_qty = self._decimal(book.get("bidQty"))
        ask_qty = self._decimal(book.get("askQty"))
        if None in (quote_volume, trades, bid, ask, bid_qty, ask_qty):
            return {}, ["malformed_prefilter_data"]
        spread = spread_percentage(bid, ask)
        liquidity = bid * bid_qty + ask * ask_qty
        metrics = {
            "quote_volume_24h": quote_volume,
            "trades_24h": trades,
            "bid": bid,
            "ask": ask,
            "spread_percentage": spread,
            "book_liquidity_quote_proxy": liquidity,
        }
        reasons = []
        if quote_volume < self._settings.quote_volume_min:
            reasons.append("quote_volume_below_threshold")
        if trades < self._settings.trade_count_min:
            reasons.append("trade_activity_below_threshold")
        if spread is None:
            reasons.append("invalid_bid_ask_spread")
        elif spread > self._settings.spread_pct_max:
            reasons.append("spread_above_threshold")
        if liquidity < self._settings.book_liquidity_quote_min:
            reasons.append("book_liquidity_below_threshold")
        return metrics, reasons

    @staticmethod
    def _spot_allowed(raw: dict[str, object]) -> bool:
        if raw.get("isSpotTradingAllowed") is True:
            return True
        permissions = raw.get("permissions")
        if isinstance(permissions, list) and "SPOT" in permissions:
            return True
        permission_sets = raw.get("permissionSets")
        return isinstance(permission_sets, list) and any(
            isinstance(group, list) and "SPOT" in group for group in permission_sets
        )

    @staticmethod
    def _leveraged_token(raw: dict[str, object]) -> bool:
        if raw.get("isLeveragedToken") is True:
            return True
        tags = raw.get("tags")
        if isinstance(tags, list) and any(
            isinstance(tag, str) and tag.upper() in {"LEVERAGED", "TOKENIZED_STOCK"}
            for tag in tags
        ):
            return True
        base = raw.get("baseAsset")
        if not isinstance(base, str):
            return False
        return any(base.endswith(suffix) and len(base) > len(suffix) + 1 for suffix in ("UP", "DOWN", "BULL", "BEAR"))

    def _non_crypto_product(self, raw: dict[str, object]) -> str | None:
        if raw.get("isTokenizedAsset") is True or raw.get("isTokenizedStock") is True:
            return "non_crypto_instrument_metadata"
        classification_terms = {
            "EQUITY",
            "ETF",
            "STOCK",
            "TOKENIZED_EQUITY",
            "TOKENIZED_STOCK",
            "TRADFI",
        }
        tags = raw.get("tags")
        if isinstance(tags, list) and any(
            isinstance(tag, str) and tag.upper().replace("-", "_") in classification_terms
            for tag in tags
        ):
            return "non_crypto_instrument_metadata"
        for key in ("assetType", "underlyingType", "productType", "securityType"):
            value = raw.get(key)
            if isinstance(value, str) and any(
                term in value.upper().replace("-", "_") for term in classification_terms
            ):
                return "non_crypto_instrument_metadata"
        base = raw.get("baseAsset")
        if isinstance(base, str) and base.endswith("B"):
            root = base[:-1]
            if root in self._settings.tokenized_equity_roots:
                return "tokenized_equity_fallback_classification"
        return None

    @staticmethod
    def _exchange_symbols(data: object) -> list[dict[str, object]] | None:
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            return None
        symbols = data["symbols"]
        return symbols if all(isinstance(item, dict) for item in symbols) else None

    @staticmethod
    def _symbol_map(data: object) -> dict[str, dict[str, object]] | None:
        if not isinstance(data, list):
            return None
        mapped = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
                return None
            mapped[item["symbol"]] = item
        return mapped

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
            if Decimal(str(value)) != parsed:
                return None
            return parsed
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _record(result: MarketDataResult, diagnostics: dict[str, Any]) -> None:
        diagnostics["binance_requests_made"] += result.requests_made
        diagnostics["cache_hits"] += int(result.cache_hit)
        diagnostics["cache_misses"] += int(not result.cache_hit)
        if result.error_category:
            diagnostics["errors_by_category"][result.error_category] += 1

    @staticmethod
    def _rejection(
        subject: SymbolDescriptor | dict[str, object],
        stage: str,
        reasons: list[str],
        metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if isinstance(subject, SymbolDescriptor):
            symbol, base, quote = subject.symbol, subject.base_asset, subject.quote_asset
        else:
            symbol, base, quote = subject.get("symbol"), subject.get("baseAsset"), subject.get("quoteAsset")
        return {
            "symbol": symbol,
            "base_asset": base,
            "quote_asset": quote,
            "stage": stage,
            "reasons": reasons,
            "prefilter_metrics": metrics,
        }

    def _response(
        self,
        scan_epoch: float,
        started: float,
        universe_count: int,
        prefilter_passed_count: int,
        prefilter_rejected_count: int,
        snapshots: list[dict[str, object]],
        rejections: list[dict[str, object]],
        diagnostics: dict[str, Any],
        fatal_error: str | None = None,
    ) -> dict[str, object]:
        rejected_counts = Counter(
            reason for rejection in rejections for reason in rejection["reasons"]
        )
        diagnostic_payload = {
            "binance_requests_made": diagnostics["binance_requests_made"],
            "cache_hits": diagnostics["cache_hits"],
            "cache_misses": diagnostics["cache_misses"],
            "rate_limit_events": diagnostics["errors_by_category"].get("rate_limited", 0),
            "errors_by_category": dict(sorted(diagnostics["errors_by_category"].items())),
        }
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "Scanner completed duration_ms=%s requests=%s cache_hits=%s analyzed=%s rejected=%s rejection_reasons=%s errors=%s",
            duration_ms,
            diagnostic_payload["binance_requests_made"],
            diagnostic_payload["cache_hits"],
            len(snapshots),
            len(rejections),
            dict(sorted(rejected_counts.items())),
            diagnostic_payload["errors_by_category"],
        )
        response = {
            "scan_timestamp": datetime.fromtimestamp(scan_epoch, timezone.utc).isoformat(),
            "universe_count": universe_count,
            "prefilter_passed_count": prefilter_passed_count,
            "prefilter_rejected_count": prefilter_rejected_count,
            "analyzed_count": len(snapshots),
            "scan_duration_ms": duration_ms,
            "diagnostics": diagnostic_payload,
            "rejected_counts_by_reason": dict(sorted(rejected_counts.items())),
            "snapshots": snapshots,
            "rejections": rejections,
        }
        if fatal_error:
            response["fatal_error"] = fatal_error
        return safe_api_value(response)

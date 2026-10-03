from __future__ import annotations

import json
from pathlib import Path
import re
from decimal import Decimal
from typing import Any

from .calibration_config import CalibrationSettings
from .intelligence import GridCandidateIntelligence
from .market_data import normalize_klines
from .market_models import Candle, safe_api_value
from .scanner import build_timeframe_metrics
from .scanner_config import ScannerSettings, TIMEFRAMES
from .transport import SafeGetTransport


INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}
SAFE_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}$")


class HistoricalFileCache:
    """Deterministic, resumable cache of public Binance kline pages."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> Path:
        if not SAFE_SYMBOL.fullmatch(symbol) or interval not in INTERVAL_MS:
            raise ValueError("unsafe historical cache key")
        return self.root / symbol / interval / f"{start_ms}-{end_ms}.json"

    def load(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> object | None:
        path = self._path(symbol, interval, start_ms, end_ms)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError(f"invalid cache file: {path}")
        return payload.get("rows")

    def store(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        rows: object,
    ) -> None:
        path = self._path(symbol, interval, start_ms, end_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "symbol": symbol,
            "interval": interval,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "rows": rows,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(path)


class HistoricalBinanceClient:
    def __init__(
        self,
        base_url: str,
        transport: SafeGetTransport,
        cache: HistoricalFileCache,
    ) -> None:
        self._base_url = base_url
        self._transport = transport
        self._cache = cache
        self.requests_made = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.errors: list[dict[str, object]] = []

    async def candles(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> list[Candle]:
        if interval not in INTERVAL_MS or end_ms < start_ms:
            raise ValueError("invalid historical range")
        page_span = INTERVAL_MS[interval] * 1000
        cursor = start_ms
        collected: dict[int, Candle] = {}
        while cursor <= end_ms:
            page_end = min(end_ms, cursor + page_span - 1)
            rows = self._cache.load(symbol, interval, cursor, page_end)
            if rows is None:
                self.cache_misses += 1
                result = await self._transport.get(
                    self._base_url,
                    "/api/v3/klines",
                    params={
                        "symbol": symbol,
                        "interval": interval,
                        "startTime": cursor,
                        "endTime": page_end,
                        "limit": 1000,
                    },
                )
                self.requests_made += result.attempts
                if result.error_category:
                    self.errors.append(
                        {
                            "symbol": symbol,
                            "interval": interval,
                            "start_ms": cursor,
                            "end_ms": page_end,
                            "category": result.error_category,
                        }
                    )
                    raise RuntimeError(f"historical_data_error:{result.error_category}")
                rows = result.data
                normalize_klines(rows)
                self._cache.store(symbol, interval, cursor, page_end, rows)
            else:
                self.cache_hits += 1
            for candle in normalize_klines(rows):
                if candle.open_time >= start_ms and candle.close_time <= end_ms:
                    collected[candle.open_time] = candle
            cursor = page_end + 1
        return [collected[key] for key in sorted(collected)]


class HistoricalReplayEngine:
    def __init__(
        self,
        scanner_settings: ScannerSettings,
        intelligence: GridCandidateIntelligence,
        calibration_settings: CalibrationSettings,
    ) -> None:
        self.scanner_settings = scanner_settings
        self.intelligence = intelligence
        self.calibration_settings = calibration_settings

    @staticmethod
    def closed_candles(candles: list[Candle], evaluation_ms: int) -> list[Candle]:
        return [candle for candle in candles if candle.close_time <= evaluation_ms]

    def snapshot_at(
        self,
        symbol: str,
        candles_by_timeframe: dict[str, list[Candle]],
        evaluation_ms: int,
    ) -> tuple[dict[str, object] | None, list[str]]:
        timeframe_metrics: dict[str, object] = {}
        errors = []
        histories: dict[str, list[Candle]] = {}
        for timeframe in TIMEFRAMES:
            closed = self.closed_candles(candles_by_timeframe.get(timeframe, []), evaluation_ms)
            production_window = closed[-self.scanner_settings.kline_limit :]
            histories[timeframe] = production_window
            if len(production_window) < self.scanner_settings.minimum_candles:
                errors.append(f"{timeframe}:insufficient_history")
                continue
            metrics = build_timeframe_metrics(production_window, self.scanner_settings)
            if metrics["data_quality_status"] != "ok":
                errors.append(f"{timeframe}:insufficient_indicator_history")
                continue
            timeframe_metrics[timeframe] = metrics
        if errors:
            return None, errors

        recent_15m = histories["15m"][-96:]
        current = recent_15m[-1].close
        spread = self.calibration_settings.historical_spread_pct_assumption
        half_spread_fraction = spread / Decimal(200)
        bid = current * (Decimal(1) - half_spread_fraction)
        ask = current * (Decimal(1) + half_spread_fraction)
        prefilter = {
            "quote_volume_24h": sum(
                (candle.quote_volume for candle in recent_15m), Decimal(0)
            ),
            "trades_24h": sum(candle.trades for candle in recent_15m),
            "bid": bid,
            "ask": ask,
            "spread_percentage": spread,
            "book_liquidity_quote_proxy": self.calibration_settings.historical_book_liquidity_quote_assumption,
            "historical_liquidity_assumption": True,
        }
        snapshot = {
            "symbol": symbol,
            "base_asset": symbol.removesuffix("USDT"),
            "quote_asset": "USDT",
            "prefilter_metrics": prefilter,
            "prefilter_status": "historical_observation",
            "rejection_reasons": [],
            "timeframes": timeframe_metrics,
            "evaluation_time_ms": evaluation_ms,
        }
        return snapshot, []

    def assessment_at(
        self,
        symbol: str,
        candles_by_timeframe: dict[str, list[Candle]],
        evaluation_ms: int,
    ) -> tuple[dict[str, object] | None, list[str]]:
        snapshot, errors = self.snapshot_at(symbol, candles_by_timeframe, evaluation_ms)
        if snapshot is None:
            return None, errors
        assessment = self.intelligence.assess(snapshot)
        if assessment.get("proposed_range") is None:
            assessment["proposed_range"] = self.intelligence.propose_range_for_observation(
                snapshot, assessment
            )
        return safe_api_value(assessment), []


from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os


TIMEFRAMES = ("15m", "1h", "4h", "1d")
DEFAULT_TOKENIZED_EQUITY_ROOTS = frozenset(
    {"AAPL", "AMZN", "GOOG", "GOOGL", "META", "MSFT", "NVDA", "QQQ", "SPY", "TSLA"}
)


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _ttl_env(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


@dataclass(frozen=True, slots=True)
class ScannerSettings:
    max_concurrency: int
    max_limit: int
    quote_volume_min: Decimal
    trade_count_min: int
    spread_pct_max: Decimal
    book_liquidity_quote_min: Decimal
    denylist: frozenset[str]
    kline_limit: int
    minimum_candles: int
    indicator_period: int
    volatility_period: int
    rolling_period: int
    volume_period: int
    oscillation_period: int
    exchange_info_ttl_seconds: float
    ticker_ttl_seconds: float
    kline_ttl_seconds: float
    tokenized_equity_roots: frozenset[str] = DEFAULT_TOKENIZED_EQUITY_ROOTS

    @classmethod
    def from_env(cls) -> "ScannerSettings":
        denylist = frozenset(
            item.strip().upper()
            for item in os.getenv("SCANNER_DENYLIST", "").split(",")
            if item.strip()
        )
        equity_roots = frozenset(
            item.strip().upper()
            for item in os.getenv(
                "SCANNER_TOKENIZED_EQUITY_ROOTS",
                ",".join(sorted(DEFAULT_TOKENIZED_EQUITY_ROOTS)),
            ).split(",")
            if item.strip()
        )
        return cls(
            max_concurrency=_int_env("SCANNER_MAX_CONCURRENCY", 4, 1, 32),
            max_limit=_int_env("SCANNER_MAX_LIMIT", 100, 1, 500),
            # Provisional safety-oriented defaults; calibrate before strategy use.
            quote_volume_min=_decimal_env("SCANNER_MIN_QUOTE_VOLUME", "1000000"),
            trade_count_min=_int_env("SCANNER_MIN_TRADES", 1000, 0, 2_000_000_000),
            spread_pct_max=_decimal_env("SCANNER_MAX_SPREAD_PCT", "0.50"),
            book_liquidity_quote_min=_decimal_env(
                "SCANNER_MIN_BOOK_LIQUIDITY_QUOTE", "1000"
            ),
            denylist=denylist,
            kline_limit=_int_env("SCANNER_KLINE_LIMIT", 200, 20, 1000),
            minimum_candles=_int_env("SCANNER_MIN_CANDLES", 100, 20, 1000),
            indicator_period=_int_env("SCANNER_INDICATOR_PERIOD", 14, 2, 100),
            volatility_period=_int_env("SCANNER_VOLATILITY_PERIOD", 30, 2, 500),
            rolling_period=_int_env("SCANNER_ROLLING_PERIOD", 20, 2, 500),
            volume_period=_int_env("SCANNER_VOLUME_PERIOD", 20, 2, 500),
            oscillation_period=_int_env("SCANNER_OSCILLATION_PERIOD", 20, 3, 500),
            exchange_info_ttl_seconds=_ttl_env("SCANNER_EXCHANGE_INFO_TTL_SECONDS", "300"),
            ticker_ttl_seconds=_ttl_env("SCANNER_TICKER_TTL_SECONDS", "30"),
            kline_ttl_seconds=_ttl_env("SCANNER_KLINE_TTL_SECONDS", "60"),
            tokenized_equity_roots=equity_roots,
        )

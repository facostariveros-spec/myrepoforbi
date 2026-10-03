from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal


@dataclass(frozen=True, slots=True)
class SymbolDescriptor:
    symbol: str
    base_asset: str
    quote_asset: str


def decimal_text(value: Decimal | None) -> str | None:
    if value is None or not value.is_finite():
        return None
    return format(value, "f")


def safe_api_value(value: Any) -> Any:
    """Convert internal Decimal-rich structures into finite JSON values."""
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, dict):
        return {key: safe_api_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_api_value(item) for item in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


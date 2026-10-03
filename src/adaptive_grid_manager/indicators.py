from __future__ import annotations

from decimal import Decimal
import math
from statistics import median

from .market_models import Candle


HUNDRED = Decimal("100")


def true_ranges(candles: list[Candle]) -> list[Decimal]:
    values: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        candidates = [candle.high - candle.low]
        if previous_close is not None:
            candidates.extend((abs(candle.high - previous_close), abs(candle.low - previous_close)))
        values.append(max(candidates))
        previous_close = candle.close
    return values


def atr(candles: list[Candle], period: int = 14) -> Decimal | None:
    ranges = true_ranges(candles)
    if period <= 0 or len(ranges) < period:
        return None
    return sum(ranges[-period:], Decimal(0)) / Decimal(period)


def rsi(candles: list[Candle], period: int = 14) -> Decimal | None:
    if period <= 0 or len(candles) < period + 1:
        return None
    changes = [candles[index].close - candles[index - 1].close for index in range(1, len(candles))]
    seed = changes[:period]
    average_gain = sum((max(change, Decimal(0)) for change in seed), Decimal(0)) / Decimal(period)
    average_loss = sum((max(-change, Decimal(0)) for change in seed), Decimal(0)) / Decimal(period)
    for change in changes[period:]:
        average_gain = (average_gain * (period - 1) + max(change, Decimal(0))) / Decimal(period)
        average_loss = (average_loss * (period - 1) + max(-change, Decimal(0))) / Decimal(period)
    if average_loss == 0:
        return HUNDRED if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return HUNDRED - (HUNDRED / (Decimal(1) + relative_strength))


def adx(candles: list[Candle], period: int = 14) -> Decimal | None:
    if period <= 0 or len(candles) < period * 2 + 1:
        return None
    tr_values: list[Decimal] = []
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    for previous, current in zip(candles, candles[1:]):
        tr_values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
        upward = current.high - previous.high
        downward = previous.low - current.low
        plus_dm.append(upward if upward > downward and upward > 0 else Decimal(0))
        minus_dm.append(downward if downward > upward and downward > 0 else Decimal(0))

    smoothed_tr = sum(tr_values[:period], Decimal(0))
    smoothed_plus = sum(plus_dm[:period], Decimal(0))
    smoothed_minus = sum(minus_dm[:period], Decimal(0))
    dx_values: list[Decimal] = []
    for index in range(period - 1, len(tr_values)):
        if index >= period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + tr_values[index]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_dm[index]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_dm[index]
        if smoothed_tr == 0:
            dx_values.append(Decimal(0))
            continue
        plus_di = HUNDRED * smoothed_plus / smoothed_tr
        minus_di = HUNDRED * smoothed_minus / smoothed_tr
        denominator = plus_di + minus_di
        dx_values.append(Decimal(0) if denominator == 0 else HUNDRED * abs(plus_di - minus_di) / denominator)

    if len(dx_values) < period:
        return None
    current_adx = sum(dx_values[:period], Decimal(0)) / Decimal(period)
    for dx_value in dx_values[period:]:
        current_adx = (current_adx * (period - 1) + dx_value) / Decimal(period)
    return current_adx


def realized_volatility(candles: list[Candle], period: int = 30) -> Decimal | None:
    if period <= 1 or len(candles) < period + 1:
        return None
    closes = [candle.close for candle in candles[-(period + 1) :]]
    if any(value <= 0 for value in closes):
        return None
    try:
        returns = [
            math.log(float(current / previous))
            for previous, current in zip(closes, closes[1:])
        ]
    except (OverflowError, ValueError):
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    result = math.sqrt(variance)
    return Decimal(str(result)) if math.isfinite(result) else None


def rolling_high_low(candles: list[Candle], period: int = 20) -> tuple[Decimal, Decimal] | None:
    if period <= 0 or len(candles) < period:
        return None
    window = candles[-period:]
    return max(candle.high for candle in window), min(candle.low for candle in window)


def range_width_pct(high: Decimal, low: Decimal) -> Decimal | None:
    midpoint = (high + low) / Decimal(2)
    if high < low or midpoint <= 0:
        return None
    return (high - low) / midpoint * HUNDRED


def volume_statistics(
    candles: list[Candle], period: int = 20
) -> dict[str, Decimal | None] | None:
    if period <= 0 or len(candles) < period:
        return None
    window = candles[-period:]
    volumes = [candle.volume for candle in window]
    quote_volumes = [candle.quote_volume for candle in window]
    average = sum(volumes, Decimal(0)) / Decimal(period)
    quote_average = sum(quote_volumes, Decimal(0)) / Decimal(period)
    return {
        "average_volume": average,
        "median_volume": Decimal(str(median(volumes))),
        "average_quote_volume": quote_average,
        "latest_volume_ratio": None if average == 0 else volumes[-1] / average,
    }


def spread_percentage(bid: Decimal, ask: Decimal) -> Decimal | None:
    midpoint = (bid + ask) / Decimal(2)
    if bid < 0 or ask < bid or midpoint <= 0:
        return None
    return (ask - bid) / midpoint * HUNDRED


def range_crossing_count(candles: list[Candle], period: int = 20) -> int | None:
    if period < 3 or len(candles) < period:
        return None
    window = candles[-period:]
    midpoint = (max(candle.high for candle in window) + min(candle.low for candle in window)) / Decimal(2)
    signs = [1 if candle.close > midpoint else -1 if candle.close < midpoint else 0 for candle in window]
    nonzero = [sign for sign in signs if sign]
    return sum(current != previous for previous, current in zip(nonzero, nonzero[1:]))

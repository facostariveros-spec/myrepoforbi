from __future__ import annotations

from decimal import Decimal

from adaptive_grid_manager.indicators import (
    adx,
    atr,
    range_crossing_count,
    range_width_pct,
    realized_volatility,
    rolling_high_low,
    rsi,
    spread_percentage,
    true_ranges,
    volume_statistics,
)
from adaptive_grid_manager.market_models import Candle


def candle(index, close, *, high=None, low=None, volume="10"):
    close = Decimal(str(close))
    high = Decimal(str(high)) if high is not None else close + 1
    low = Decimal(str(low)) if low is not None else close - 1
    return Candle(
        open_time=index * 1000,
        close_time=index * 1000 + 999,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=Decimal(volume),
        quote_volume=Decimal(volume) * close,
        trades=10,
        taker_buy_base_volume=Decimal(volume) / 2,
        taker_buy_quote_volume=Decimal(volume) * close / 2,
    )


def test_true_range_and_atr():
    candles = [candle(0, 10, high=11, low=9), candle(1, 12, high=13, low=11)]
    assert true_ranges(candles) == [Decimal("2"), Decimal("3")]
    assert atr(candles, 2) == Decimal("2.5")
    assert atr(candles, 3) is None


def test_rsi_and_adx_are_deterministic_and_safe_for_short_history():
    rising = [candle(index, 100 + index) for index in range(40)]
    assert rsi(rising, 14) == Decimal("100")
    assert adx(rising, 14) == Decimal("100")
    assert rsi(rising[:5], 14) is None
    assert adx(rising[:20], 14) is None


def test_realized_volatility():
    constant = [candle(index, 100) for index in range(6)]
    changing = [candle(index, value) for index, value in enumerate((100, 101, 99, 103, 98, 104))]
    assert realized_volatility(constant, 5) == Decimal("0.0")
    assert realized_volatility(changing, 5) > 0
    assert realized_volatility(changing[:2], 5) is None


def test_rolling_range_width_and_volume_statistics():
    candles = [candle(index, 10 + index, high=12 + index, low=8 + index, volume=str(index + 1)) for index in range(5)]
    high, low = rolling_high_low(candles, 3)
    assert (high, low) == (Decimal("16"), Decimal("10"))
    assert range_width_pct(high, low) == Decimal("600") / Decimal("13")
    stats = volume_statistics(candles, 3)
    assert stats["average_volume"] == Decimal("4")
    assert stats["median_volume"] == Decimal("4")
    assert stats["latest_volume_ratio"] == Decimal("1.25")


def test_spread_and_oscillation_crossings():
    closes = (9, 11, 9, 11, 9, 11)
    candles = [candle(index, close, high=12, low=8) for index, close in enumerate(closes)]
    assert spread_percentage(Decimal("99"), Decimal("101")) == Decimal("2")
    assert range_crossing_count(candles, 6) == 5
    assert range_crossing_count(candles[:2], 6) is None


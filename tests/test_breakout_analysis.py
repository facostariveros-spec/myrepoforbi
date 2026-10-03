from decimal import Decimal

from adaptive_grid_manager.breakout_analysis import (
    analyze_post_breakout,
)
from adaptive_grid_manager.market_models import Candle


def candle(t, o, h, l, c):
    return Candle(
        open_time=t,
        close_time=t + 899999,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal("1000"),
        quote_volume=Decimal("100000"),
        trades=100,
        taker_buy_base_volume=Decimal("500"),
        taker_buy_quote_volume=Decimal("50000"),
    )


def test_lower_breakout_reentry_is_detected_causally():
    candles = [
        candle(0,       79, 79.5, 77, 78),   # confirmation candle
        candle(900000,  78, 79,   76, 77),
        candle(1800000, 77, 81,   76, 80.5), # first close back inside
        candle(2700000, 80.5, 83, 80, 82),
    ]

    result = analyze_post_breakout(
        candles,
        breakout_time_ms=candles[0].close_time,
        direction="LOWER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    assert result.reentered is True
    assert result.reentry_time_ms == candles[2].close_time
    assert result.time_to_reentry_ms == 1800000


def test_lower_breakout_continuation_is_measured():
    candles = [
        candle(0,       79, 79.5, 77, 78),
        candle(900000,  78, 79,   72, 73),
        candle(1800000, 73, 75,   68, 70),
    ]

    result = analyze_post_breakout(
        candles,
        breakout_time_ms=candles[0].close_time,
        direction="LOWER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    assert result.reentered is False
    assert result.max_continuation_pct > Decimal("0")
    assert result.worst_price == Decimal("68")


def test_upper_breakout_reentry_is_directional():
    candles = [
        candle(0,       121, 123, 120.5, 122),
        candle(900000,  122, 124, 119,   119.5),
    ]

    result = analyze_post_breakout(
        candles,
        breakout_time_ms=candles[0].close_time,
        direction="UPPER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    assert result.reentered is True
    assert result.reentry_time_ms == candles[1].close_time


def test_pre_breakout_candles_do_not_affect_analysis():
    pre = candle(-900000, 100, 150, 50, 100)

    post = [
        candle(0,       79, 79.5, 77, 78),
        candle(900000,  78, 79,   75, 76),
    ]

    without_pre = analyze_post_breakout(
        post,
        breakout_time_ms=post[0].close_time,
        direction="LOWER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    with_pre = analyze_post_breakout(
        [pre] + post,
        breakout_time_ms=post[0].close_time,
        direction="LOWER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    assert with_pre.worst_price == without_pre.worst_price
    assert with_pre.max_continuation_pct == without_pre.max_continuation_pct


def test_confirmation_candle_defines_reference_close():
    candles = [
        candle(0,       79, 79.5, 77, 78),
        candle(900000,  78, 79,   70, 72),
    ]

    result = analyze_post_breakout(
        candles,
        breakout_time_ms=candles[0].close_time,
        direction="LOWER",
        lower=Decimal("80"),
        upper=Decimal("120"),
    )

    assert result.confirmation_price == Decimal("78")
    assert result.boundary == Decimal("80")
    assert result.distance_outside_at_confirmation_pct > Decimal("0")

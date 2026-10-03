from decimal import Decimal

from adaptive_grid_manager.economic_backtest import (
    GridBacktestSettings,
    StaticGridEconomicBacktester,
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


def test_levels_are_deterministic():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(grid_count=4)
    )
    levels = bt.build_levels(
        Decimal("80"),
        Decimal("120"),
        4,
    )
    assert levels == [
        Decimal("80"),
        Decimal("90"),
        Decimal("100"),
        Decimal("110"),
        Decimal("120"),
    ]


def test_round_trip_generates_realized_profit():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            reserve_pct=Decimal("5"),
        )
    )

    candles = [
        candle(0, 100, 101, 89, 91),       # fills 90
        candle(900000, 91, 101, 90, 100),  # sells at 100
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.buy_fills >= 1
    assert result.sell_fills >= 1
    assert result.completed_round_trips >= 1
    assert result.realized_net_pnl > 0
    assert result.fees_paid > 0


def test_no_same_candle_round_trip():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
        )
    )

    candles = [
        candle(0, 100, 105, 85, 100),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.buy_fills > 0
    assert result.sell_fills == 0
    assert result.completed_round_trips == 0


def test_never_spends_more_than_available_capital():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("100"),
            grid_count=20,
            reserve_pct=Decimal("5"),
        )
    )

    candles = [
        candle(0, 100, 101, 50, 55),
    ]

    result = bt.run(
        candles,
        lower=Decimal("50"),
        upper=Decimal("150"),
        start_price=Decimal("100"),
    )

    assert result.cash >= 0
    assert result.final_equity >= 0


def test_breakout_confirmation_requires_two_closed_candles():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
        )
    )

    lower = Decimal("80")
    upper = Decimal("120")

    first = candle(0, 119, 123, 118, 121)
    second = candle(900000, 121, 124, 120, 122)

    assert bt.breakout_direction(
        [first],
        lower=lower,
        upper=upper,
    ) is None

    assert bt.breakout_direction(
        [first, second],
        lower=lower,
        upper=upper,
    ) == "UPPER"


def test_breakout_confirmation_resets_when_price_returns_inside():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
        )
    )

    candles = [
        candle(0, 119, 123, 118, 121),
        candle(900000, 121, 122, 110, 115),
        candle(1800000, 115, 123, 114, 121),
    ]

    assert bt.breakout_direction(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
    ) is None


def test_lower_breakout_confirmation_is_directional():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
        )
    )

    candles = [
        candle(0, 81, 82, 77, 79),
        candle(900000, 79, 80, 76, 78),
    ]

    assert bt.breakout_direction(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
    ) == "LOWER"


def test_freeze_stops_new_buys_after_confirmed_lower_breakout():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="FREEZE",
        )
    )

    candles = [
        candle(0,       100, 101, 89, 91),  # buy 90
        candle(900000,   91,  92, 78, 79),  # first close below 80
        candle(1800000,  79,  80, 77, 78),  # confirms lower breakout
        candle(2700000,  78,  79, 69, 70),  # must not buy after freeze
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"
    assert result.breakout_policy == "FREEZE"
    assert result.breakout_time_ms == candles[2].close_time


def test_close_liquidates_inventory_on_confirmed_lower_breakout():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CLOSE",
        )
    )

    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 69, 70),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"
    assert result.breakout_policy == "CLOSE"
    assert result.breakout_time_ms == candles[2].close_time
    assert result.inventory_quantity == 0
    assert result.inventory_value == 0
    assert result.unrealized_pnl == 0


def test_static_policy_preserves_existing_behavior():
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 69, 70),
    ]

    baseline = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    explicit_static = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_policy="STATIC",
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert explicit_static.final_equity == baseline.final_equity
    assert explicit_static.buy_fills == baseline.buy_fills
    assert explicit_static.sell_fills == baseline.sell_fills
    assert explicit_static.fees_paid == baseline.fees_paid


def test_close_uses_confirmation_close_and_ignores_later_crash():
    settings = GridBacktestSettings(
        initial_capital=Decimal("500"),
        fee_rate_pct_per_side=Decimal("0.10"),
        grid_count=4,
        breakout_consecutive_closes=2,
        breakout_policy="CLOSE",
    )

    bt = StaticGridEconomicBacktester(settings)

    # Grid: 80, 90, 100, 110, 120
    #
    # Candle 1 buys inventory at 90.
    # Candle 2 = first close below lower boundary.
    # Candle 3 = second consecutive close below 80 -> confirms breakout.
    # CLOSE must liquidate at candle 3 CLOSE = 78.
    #
    # Candle 4 crashes to 40. It must have zero economic effect because
    # inventory was already liquidated at the previous candle close.
    confirmation_candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
    ]

    with_later_crash = confirmation_candles + [
        candle(2700000, 78, 79, 35, 40),
    ]

    at_confirmation = bt.run(
        confirmation_candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    after_crash = bt.run(
        with_later_crash,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    expected_time = confirmation_candles[-1].close_time

    assert at_confirmation.breakout_triggered is True
    assert at_confirmation.breakout_direction == "LOWER"
    assert at_confirmation.breakout_time_ms == expected_time

    assert at_confirmation.inventory_quantity == Decimal("0")
    assert at_confirmation.inventory_value == Decimal("0")

    # Once CLOSE has liquidated at the confirmation close, future candles
    # cannot alter the already-realized economic result.
    assert after_crash.inventory_quantity == Decimal("0")
    assert after_crash.inventory_value == Decimal("0")

    assert after_crash.final_equity == at_confirmation.final_equity
    assert after_crash.realized_net_pnl == at_confirmation.realized_net_pnl
    assert after_crash.fees_paid == at_confirmation.fees_paid
    assert after_crash.sell_fills == at_confirmation.sell_fills
    assert after_crash.completed_round_trips == at_confirmation.completed_round_trips
    assert after_crash.breakout_time_ms == expected_time

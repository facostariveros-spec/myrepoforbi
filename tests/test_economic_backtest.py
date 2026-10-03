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


def test_delayed_close_freezes_immediately_and_liquidates_at_30m_close():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=30,
        )
    )

    # Grid: 80, 90, 100, 110, 120
    #
    # Candle 1 buys at 90.
    # Candle 2 = first close below 80.
    # Candle 3 = confirms LOWER breakout at close=78.
    # DELAYED_CLOSE must freeze immediately.
    # Candle 4 = T+15m, close=75 -> inventory must remain.
    # Candle 5 = T+30m, close=70 -> liquidate here.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
        candle(3600000,  75,  76, 69, 70),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"
    assert result.breakout_policy == "DELAYED_CLOSE"
    assert result.breakout_time_ms == candles[2].close_time

    assert result.protective_close_triggered is True
    assert result.protective_close_time_ms == candles[4].close_time
    assert result.protective_close_price == Decimal("70")

    assert result.inventory_quantity == 0
    assert result.inventory_value == 0
    assert result.unrealized_pnl == 0


def test_delayed_close_does_not_liquidate_before_delay():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=30,
        )
    )

    # Simulation ends only 15 minutes after confirmation.
    # Inventory must still exist.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.protective_close_triggered is False
    assert result.protective_close_time_ms is None
    assert result.protective_close_price is None
    assert result.inventory_quantity > 0


def test_delayed_close_ignores_crash_after_liquidation():
    settings = GridBacktestSettings(
        initial_capital=Decimal("500"),
        fee_rate_pct_per_side=Decimal("0.10"),
        grid_count=4,
        breakout_consecutive_closes=2,
        breakout_policy="DELAYED_CLOSE",
        breakout_close_delay_minutes=30,
    )

    bt = StaticGridEconomicBacktester(settings)

    through_close = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
        candle(3600000,  75,  76, 69, 70),
    ]

    after_crash = through_close + [
        candle(4500000, 70, 71, 35, 40),
    ]

    at_close = bt.run(
        through_close,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    later = bt.run(
        after_crash,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert at_close.protective_close_triggered is True
    assert later.protective_close_triggered is True

    assert later.final_equity == at_close.final_equity
    assert later.realized_net_pnl == at_close.realized_net_pnl
    assert later.fees_paid == at_close.fees_paid


def test_zero_delay_delayed_close_matches_immediate_close():
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 69, 70),
    ]

    immediate = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CLOSE",
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    delayed_zero = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=0,
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert delayed_zero.final_equity == immediate.final_equity
    assert delayed_zero.realized_net_pnl == immediate.realized_net_pnl
    assert delayed_zero.fees_paid == immediate.fees_paid
    assert delayed_zero.inventory_quantity == 0


def test_delayed_close_freezes_immediately_and_liquidates_at_30m_close():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=30,
        )
    )

    # Grid: 80, 90, 100, 110, 120
    #
    # Candle 1 buys at 90.
    # Candle 2 = first close below 80.
    # Candle 3 = confirms LOWER breakout at close=78.
    # DELAYED_CLOSE must freeze immediately.
    # Candle 4 = T+15m, close=75 -> inventory must remain.
    # Candle 5 = T+30m, close=70 -> liquidate here.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
        candle(3600000,  75,  76, 69, 70),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"
    assert result.breakout_policy == "DELAYED_CLOSE"
    assert result.breakout_time_ms == candles[2].close_time

    assert result.protective_close_triggered is True
    assert result.protective_close_time_ms == candles[4].close_time
    assert result.protective_close_price == Decimal("70")

    assert result.inventory_quantity == 0
    assert result.inventory_value == 0
    assert result.unrealized_pnl == 0


def test_delayed_close_does_not_liquidate_before_delay():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=30,
        )
    )

    # Simulation ends only 15 minutes after confirmation.
    # Inventory must still exist.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.protective_close_triggered is False
    assert result.protective_close_time_ms is None
    assert result.protective_close_price is None
    assert result.inventory_quantity > 0


def test_delayed_close_ignores_crash_after_liquidation():
    settings = GridBacktestSettings(
        initial_capital=Decimal("500"),
        fee_rate_pct_per_side=Decimal("0.10"),
        grid_count=4,
        breakout_consecutive_closes=2,
        breakout_policy="DELAYED_CLOSE",
        breakout_close_delay_minutes=30,
    )

    bt = StaticGridEconomicBacktester(settings)

    through_close = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 74, 75),
        candle(3600000,  75,  76, 69, 70),
    ]

    after_crash = through_close + [
        candle(4500000, 70, 71, 35, 40),
    ]

    at_close = bt.run(
        through_close,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    later = bt.run(
        after_crash,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert at_close.protective_close_triggered is True
    assert later.protective_close_triggered is True

    assert later.final_equity == at_close.final_equity
    assert later.realized_net_pnl == at_close.realized_net_pnl
    assert later.fees_paid == at_close.fees_paid


def test_zero_delay_delayed_close_matches_immediate_close():
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78,  79, 69, 70),
    ]

    immediate = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CLOSE",
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    delayed_zero = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="DELAYED_CLOSE",
            breakout_close_delay_minutes=0,
        )
    ).run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert delayed_zero.final_equity == immediate.final_equity
    assert delayed_zero.realized_net_pnl == immediate.realized_net_pnl
    assert delayed_zero.fees_paid == immediate.fees_paid
    assert delayed_zero.inventory_quantity == 0


def test_conditional_close_closes_lower_breakout_when_30m_rule_is_met():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            fee_rate_pct_per_side=Decimal("0.10"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    # Confirmation close = 78.
    # During next 30m:
    # - final close = 74 => return from confirmation < 0
    # - worst low = 70 => continuation = 8/78
    # - best high = 79 => recovery = 1/78
    # - recovery/continuation = 1/8 = 0.125 < 0.25
    #
    # Therefore protective close must occur at T+30m close = 74.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),  # confirmation
        candle(2700000,  78,  79, 70, 75),
        candle(3600000,  75,  76, 72, 74),  # T+30m
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"
    assert result.protective_close_triggered is True
    assert result.protective_close_time_ms == candles[4].close_time
    assert result.protective_close_price == Decimal("74")
    assert result.inventory_quantity == 0


def test_conditional_close_keeps_inventory_when_recovery_ratio_is_strong():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    # Confirmation = 78.
    # Worst = 74 -> continuation = 4.
    # Best = 79.5 -> recovery = 1.5.
    # ratio = 1.5 / 4 = 0.375.
    #
    # Even though T+30 close remains below confirmation,
    # recovery is strong enough that it must remain frozen.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78, 79.5, 74, 76),
        candle(3600000,  76,  78, 75, 77),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.protective_close_triggered is False
    assert result.protective_close_time_ms is None
    assert result.inventory_quantity > 0


def test_conditional_close_keeps_inventory_when_30m_return_is_positive():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    # Confirmation = 78.
    # T+30 close = 79, therefore return from confirmation > 0.
    # It must NOT close even if continuation was large.
    candles = [
        candle(0,       100, 101, 89, 91),
        candle(900000,   91,  92, 78, 79),
        candle(1800000,  79,  80, 77, 78),
        candle(2700000,  78, 78.2, 70, 72),
        candle(3600000,  72, 79.2, 71, 79),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.protective_close_triggered is False
    assert result.inventory_quantity > 0


def test_conditional_close_is_lower_only_for_now():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    candles = [
        candle(0,       100, 111, 99, 110),
        candle(900000,  110, 123, 109, 121),
        candle(1800000, 121, 124, 120, 122),  # confirms UPPER
        candle(2700000, 122, 130, 121, 128),
        candle(3600000, 128, 135, 127, 134),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "UPPER"
    assert result.protective_close_triggered is False


def test_conditional_close_never_buys_after_breakout_confirmation():
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    candles = [
        candle(0,       100, 101, 89, 91),  # buy 90
        # First outside close, but LOW deliberately stays above 80 so
        # level 80 cannot fill before breakout confirmation.
        candle(900000,   91,  92, 80.5, 79),
        candle(1800000,  79,  80, 79, 78),  # confirms LOWER
        candle(2700000,  78,  79, 69, 75),  # must NOT buy 80 after confirmation
        candle(3600000,  75,  76, 68, 74),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    # Only the pre-breakout 90-level position should exist/be handled.
    # No additional exposure may be created after confirmation.
    assert result.buy_fills == 1


def test_conditional_close_decision_is_evaluated_only_once():
    """
    CONDITIONAL_CLOSE is a one-shot decision.

    If the configured T+30m checkpoint does not satisfy the close rule,
    later deterioration must not trigger a delayed protective close.
    """
    bt = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=Decimal("500"),
            grid_count=4,
            breakout_consecutive_closes=2,
            breakout_policy="CONDITIONAL_CLOSE",
            breakout_close_delay_minutes=30,
            breakout_recovery_continuation_ratio=Decimal("0.25"),
        )
    )

    candles = [
        candle(0,       100, 101, 89, 91),   # buy 90
        candle(900000,   91,  92, 81, 79),   # first close below 80
        candle(1800000,  79,  80, 79, 78),   # confirms LOWER

        # T+15m: strong recovery excursion.
        candle(2700000,  78, 80, 77, 79),

        # T+30m decision point:
        # negative close, but recovery/continuation is strong,
        # therefore KEEP/FREEZE.
        candle(3600000,  79, 80, 77, 77.9),

        # After the decision, price collapses. This must NOT cause
        # a second evaluation / protective close.
        candle(4500000, 77.9, 78, 65, 66),
        candle(5400000, 66, 67, 60, 61),
    ]

    result = bt.run(
        candles,
        lower=Decimal("80"),
        upper=Decimal("120"),
        start_price=Decimal("100"),
    )

    assert result.breakout_triggered is True
    assert result.breakout_direction == "LOWER"

    # One-shot T+30 decision said KEEP.
    assert result.protective_close_triggered is False
    assert result.protective_close_time_ms is None
    assert result.protective_close_price is None

    # Existing inventory remains exposed/frozen.
    assert result.inventory_quantity > 0

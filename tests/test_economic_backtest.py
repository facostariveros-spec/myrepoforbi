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

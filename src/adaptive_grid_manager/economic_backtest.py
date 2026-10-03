from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from .market_models import Candle


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class GridBacktestSettings:
    initial_capital: Decimal = Decimal("500")
    fee_rate_pct_per_side: Decimal = Decimal("0.10")
    grid_count: int = 10
    reserve_pct: Decimal = Decimal("5")
    breakout_consecutive_closes: int = 2
    breakout_policy: str = "STATIC"
    breakout_close_delay_minutes: int = 0
    breakout_recovery_continuation_ratio: Decimal = Decimal("0.25")

    def validate(self) -> None:
        if self.initial_capital <= ZERO:
            raise ValueError("initial_capital must be positive")
        if self.fee_rate_pct_per_side < ZERO:
            raise ValueError("fee_rate_pct_per_side cannot be negative")
        if self.grid_count < 2:
            raise ValueError("grid_count must be at least 2")
        if not ZERO <= self.reserve_pct < HUNDRED:
            raise ValueError("reserve_pct must be in [0, 100)")
        if self.breakout_consecutive_closes < 1:
            raise ValueError("breakout_consecutive_closes must be positive")
        if self.breakout_policy not in {
            "STATIC",
            "FREEZE",
            "CLOSE",
            "DELAYED_CLOSE",
            "CONDITIONAL_CLOSE",
        }:
            raise ValueError(
                "breakout_policy must be STATIC, FREEZE, CLOSE, "
                "DELAYED_CLOSE, or CONDITIONAL_CLOSE"
            )
        if self.breakout_close_delay_minutes < 0:
            raise ValueError(
                "breakout_close_delay_minutes cannot be negative"
            )
        if self.breakout_recovery_continuation_ratio < ZERO:
            raise ValueError(
                "breakout_recovery_continuation_ratio cannot be negative"
            )


@dataclass(slots=True)
class GridLot:
    entry_price: Decimal
    quantity: Decimal
    entry_fee: Decimal


@dataclass(slots=True)
class GridBacktestResult:
    initial_capital: Decimal
    final_equity: Decimal
    cash: Decimal
    inventory_quantity: Decimal
    inventory_value: Decimal
    realized_gross_pnl: Decimal
    realized_net_pnl: Decimal
    unrealized_pnl: Decimal
    fees_paid: Decimal
    buy_fills: int
    sell_fills: int
    completed_round_trips: int
    max_inventory_value: Decimal
    max_drawdown_pct: Decimal
    capital_return_pct: Decimal
    breakout_triggered: bool = False
    breakout_direction: str | None = None
    breakout_time_ms: int | None = None
    breakout_policy: str = "STATIC"
    protective_close_triggered: bool = False
    protective_close_time_ms: int | None = None
    protective_close_price: Decimal | None = None
    levels: list[Decimal] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "initial_capital": self.initial_capital,
            "final_equity": self.final_equity,
            "cash": self.cash,
            "inventory_quantity": self.inventory_quantity,
            "inventory_value": self.inventory_value,
            "realized_gross_pnl": self.realized_gross_pnl,
            "realized_net_pnl": self.realized_net_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "fees_paid": self.fees_paid,
            "buy_fills": self.buy_fills,
            "sell_fills": self.sell_fills,
            "completed_round_trips": self.completed_round_trips,
            "max_inventory_value": self.max_inventory_value,
            "max_drawdown_pct": self.max_drawdown_pct,
            "capital_return_pct": self.capital_return_pct,
            "breakout_triggered": self.breakout_triggered,
            "breakout_direction": self.breakout_direction,
            "breakout_time_ms": self.breakout_time_ms,
            "breakout_policy": self.breakout_policy,
            "protective_close_triggered": self.protective_close_triggered,
            "protective_close_time_ms": self.protective_close_time_ms,
            "protective_close_price": self.protective_close_price,
            "levels": self.levels,
        }


class StaticGridEconomicBacktester:
    """
    Conservative static-grid economic simulator.

    V1 assumptions:
    - Spot only.
    - No leverage.
    - No short selling.
    - Equal linear grid spacing.
    - Capital is reserved before simulation.
    - Buy fills use candle LOW.
    - Sell fills use candle HIGH.
    - Same-candle buy->sell round trips are NOT allowed.
    - Inventory is marked to final close.
    - No adaptive regrid/expand/close logic yet.
    """

    def __init__(self, settings: GridBacktestSettings) -> None:
        settings.validate()
        self.settings = settings

    @staticmethod
    def build_levels(
        lower: Decimal,
        upper: Decimal,
        grid_count: int,
    ) -> list[Decimal]:
        if lower <= ZERO or upper <= lower:
            raise ValueError("invalid grid range")
        if grid_count < 2:
            raise ValueError("grid_count must be at least 2")

        step = (upper - lower) / Decimal(grid_count)
        return [lower + step * Decimal(i) for i in range(grid_count + 1)]

    def breakout_direction(
        self,
        candles: Iterable[Candle],
        lower: Decimal,
        upper: Decimal,
    ) -> str | None:
        """
        Return a confirmed directional breakout using only supplied closed candles.

        Confirmation requires N consecutive closes beyond the same boundary.
        Returning inside the range or crossing to the opposite side resets
        the previous directional run.
        """
        if lower <= ZERO or upper <= lower:
            raise ValueError("invalid grid range")

        direction: str | None = None
        consecutive = 0

        for candle in candles:
            current = (
                "UPPER"
                if candle.close > upper
                else "LOWER"
                if candle.close < lower
                else None
            )

            if current is None:
                direction = None
                consecutive = 0
                continue

            if current == direction:
                consecutive += 1
            else:
                direction = current
                consecutive = 1

            if consecutive >= self.settings.breakout_consecutive_closes:
                return direction

        return None

    def run(
        self,
        candles: Iterable[Candle],
        lower: Decimal,
        upper: Decimal,
        start_price: Decimal,
    ) -> GridBacktestResult:
        candles = list(candles)
        if not candles:
            raise ValueError("candles cannot be empty")
        if start_price <= ZERO:
            raise ValueError("start_price must be positive")

        levels = self.build_levels(lower, upper, self.settings.grid_count)

        usable_capital = (
            self.settings.initial_capital
            * (HUNDRED - self.settings.reserve_pct)
            / HUNDRED
        )
        reserve = self.settings.initial_capital - usable_capital

        # Allocate quote capital across all potential buy levels below start.
        buy_levels = [level for level in levels[:-1] if level < start_price]
        if not buy_levels:
            raise ValueError("no executable buy levels below start price")

        quote_per_buy = usable_capital / Decimal(len(buy_levels))
        fee_fraction = self.settings.fee_rate_pct_per_side / HUNDRED

        cash = usable_capital
        lots_by_level: dict[int, GridLot] = {}

        fees_paid = ZERO
        realized_gross = ZERO
        realized_net = ZERO
        buy_fills = 0
        sell_fills = 0
        round_trips = 0
        max_inventory_value = ZERO

        peak_equity = self.settings.initial_capital
        max_drawdown_pct = ZERO

        breakout_triggered = False
        breakout_direction = None
        breakout_time_ms = None
        outside_direction = None
        outside_consecutive = 0
        frozen = False

        protective_close_triggered = False
        protective_close_time_ms = None
        protective_close_price = None

        breakout_confirmation_price = None
        post_breakout_worst_price = None
        post_breakout_best_price = None

        # CONDITIONAL_CLOSE is evaluated exactly once at its configured
        # post-breakout checkpoint. KEEP is as final as CLOSE.
        conditional_decision_evaluated = False

        # A level may refill after its lot has been sold.
        for candle in candles:
            # Snapshot occupied levels at candle open. This prevents a buy
            # filled during this candle from also selling in the same candle.
            occupied_at_open = set(lots_by_level)

            # SELL first only for inventory that existed before this candle.
            # A lot bought at level i exits at level i+1.
            for level_index in sorted(occupied_at_open, reverse=True):
                lot = lots_by_level.get(level_index)
                if lot is None:
                    continue

                sell_price = levels[level_index + 1]
                if candle.high < sell_price:
                    continue

                gross_proceeds = lot.quantity * sell_price
                exit_fee = gross_proceeds * fee_fraction
                net_proceeds = gross_proceeds - exit_fee

                gross_pnl = (sell_price - lot.entry_price) * lot.quantity
                net_pnl = gross_pnl - lot.entry_fee - exit_fee

                cash += net_proceeds
                fees_paid += exit_fee
                realized_gross += gross_pnl
                realized_net += net_pnl
                sell_fills += 1
                round_trips += 1
                del lots_by_level[level_index]

            # Breakout decisions use this candle's CLOSE only. Therefore the
            # signal becomes actionable only after this candle has completed.
            current_outside_direction = (
                "UPPER"
                if candle.close > upper
                else "LOWER"
                if candle.close < lower
                else None
            )

            if current_outside_direction is None:
                outside_direction = None
                outside_consecutive = 0
            elif current_outside_direction == outside_direction:
                outside_consecutive += 1
            else:
                outside_direction = current_outside_direction
                outside_consecutive = 1

            just_confirmed_breakout = (
                not breakout_triggered
                and outside_direction is not None
                and outside_consecutive
                >= self.settings.breakout_consecutive_closes
            )

            if just_confirmed_breakout:
                breakout_triggered = True
                breakout_direction = outside_direction
                breakout_time_ms = candle.close_time
                breakout_confirmation_price = candle.close

                if self.settings.breakout_policy in {
                    "FREEZE",
                    "CLOSE",
                    "DELAYED_CLOSE",
                    "CONDITIONAL_CLOSE",
                }:
                    frozen = True

                if self.settings.breakout_policy == "CLOSE":
                    # The breakout is known only at candle close, so liquidation
                    # occurs at that close. Using the candle low/high would
                    # introduce intrabar look-ahead.
                    liquidation_price = candle.close

                    protective_close_triggered = True
                    protective_close_time_ms = candle.close_time
                    protective_close_price = liquidation_price

                    for level_index in sorted(list(lots_by_level)):
                        lot = lots_by_level[level_index]

                        gross_proceeds = lot.quantity * liquidation_price
                        exit_fee = gross_proceeds * fee_fraction
                        net_proceeds = gross_proceeds - exit_fee

                        gross_pnl = (
                            liquidation_price - lot.entry_price
                        ) * lot.quantity
                        net_pnl = gross_pnl - lot.entry_fee - exit_fee

                        cash += net_proceeds
                        fees_paid += exit_fee
                        realized_gross += gross_pnl
                        realized_net += net_pnl
                        sell_fills += 1

                        # Protective liquidation is not a completed grid
                        # round trip.
                        del lots_by_level[level_index]

            # CONDITIONAL_CLOSE freezes immediately at confirmation,
            # observes only subsequently CLOSED candles, and evaluates once
            # the configured delay has elapsed.
            if (
                self.settings.breakout_policy == "CONDITIONAL_CLOSE"
                and breakout_triggered
                and breakout_direction == "LOWER"
                and breakout_time_ms is not None
                and breakout_confirmation_price is not None
                and candle.close_time > breakout_time_ms
                and not protective_close_triggered
                and not conditional_decision_evaluated
            ):
                if post_breakout_worst_price is None:
                    post_breakout_worst_price = candle.low
                    post_breakout_best_price = candle.high
                else:
                    post_breakout_worst_price = min(
                        post_breakout_worst_price,
                        candle.low,
                    )
                    post_breakout_best_price = max(
                        post_breakout_best_price,
                        candle.high,
                    )

                decision_time_ms = (
                    breakout_time_ms
                    + self.settings.breakout_close_delay_minutes * 60_000
                )

                if candle.close_time >= decision_time_ms:
                    # One-shot decision point. Once this checkpoint is
                    # reached, later candles must never reconsider it.
                    conditional_decision_evaluated = True

                    continuation = max(
                        ZERO,
                        breakout_confirmation_price
                        - post_breakout_worst_price,
                    )
                    recovery = max(
                        ZERO,
                        post_breakout_best_price
                        - breakout_confirmation_price,
                    )

                    return_is_negative = (
                        candle.close < breakout_confirmation_price
                    )

                    ratio_is_weak = (
                        continuation > ZERO
                        and recovery / continuation
                        < self.settings.breakout_recovery_continuation_ratio
                    )

                    if return_is_negative and ratio_is_weak:
                        liquidation_price = candle.close

                        protective_close_triggered = True
                        protective_close_time_ms = candle.close_time
                        protective_close_price = liquidation_price

                        for level_index in sorted(list(lots_by_level)):
                            lot = lots_by_level[level_index]

                            gross_proceeds = (
                                lot.quantity * liquidation_price
                            )
                            exit_fee = (
                                gross_proceeds * fee_fraction
                            )
                            net_proceeds = (
                                gross_proceeds - exit_fee
                            )

                            gross_pnl = (
                                liquidation_price - lot.entry_price
                            ) * lot.quantity
                            net_pnl = (
                                gross_pnl
                                - lot.entry_fee
                                - exit_fee
                            )

                            cash += net_proceeds
                            fees_paid += exit_fee
                            realized_gross += gross_pnl
                            realized_net += net_pnl
                            sell_fills += 1

                            # Protective liquidation is not a completed
                            # grid round trip.
                            del lots_by_level[level_index]

            # DELAYED_CLOSE freezes immediately at confirmation,
            # then liquidates existing inventory at the first CLOSED candle
            # whose close time reaches the configured delay.
            if (
                self.settings.breakout_policy == "DELAYED_CLOSE"
                and breakout_triggered
                and not protective_close_triggered
                and breakout_time_ms is not None
                and candle.close_time
                >= breakout_time_ms
                + self.settings.breakout_close_delay_minutes * 60_000
            ):
                liquidation_price = candle.close

                protective_close_triggered = True
                protective_close_time_ms = candle.close_time
                protective_close_price = liquidation_price

                for level_index in sorted(list(lots_by_level)):
                    lot = lots_by_level[level_index]

                    gross_proceeds = lot.quantity * liquidation_price
                    exit_fee = gross_proceeds * fee_fraction
                    net_proceeds = gross_proceeds - exit_fee

                    gross_pnl = (
                        liquidation_price - lot.entry_price
                    ) * lot.quantity
                    net_pnl = gross_pnl - lot.entry_fee - exit_fee

                    cash += net_proceeds
                    fees_paid += exit_fee
                    realized_gross += gross_pnl
                    realized_net += net_pnl
                    sell_fills += 1

                    # Protective liquidation is not a completed
                    # grid round trip.
                    del lots_by_level[level_index]

            # BUY after sells and breakout evaluation.
            # FREEZE/CLOSE/DELAYED_CLOSE cannot create new exposure
            # once confirmed.
            if not frozen:
                for level_index, buy_price in enumerate(levels[:-1]):
                    if buy_price >= start_price:
                        continue
                    if level_index in lots_by_level:
                        continue
                    if candle.low > buy_price:
                        continue

                    # quote_per_buy includes fee, preventing overspend.
                    gross_quote = quote_per_buy / (ONE + fee_fraction)
                    entry_fee = gross_quote * fee_fraction
                    total_cost = gross_quote + entry_fee

                    if cash < total_cost:
                        continue

                    quantity = gross_quote / buy_price

                    cash -= total_cost
                    fees_paid += entry_fee
                    lots_by_level[level_index] = GridLot(
                        entry_price=buy_price,
                        quantity=quantity,
                        entry_fee=entry_fee,
                    )
                    buy_fills += 1

            mark_price = candle.close
            inventory_value = sum(
                (lot.quantity * mark_price for lot in lots_by_level.values()),
                ZERO,
            )
            max_inventory_value = max(max_inventory_value, inventory_value)

            equity = reserve + cash + inventory_value

            if equity > peak_equity:
                peak_equity = equity
            elif peak_equity > ZERO:
                drawdown = (peak_equity - equity) / peak_equity * HUNDRED
                max_drawdown_pct = max(max_drawdown_pct, drawdown)

        final_price = candles[-1].close
        inventory_value = sum(
            (lot.quantity * final_price for lot in lots_by_level.values()),
            ZERO,
        )

        unrealized_pnl = sum(
            (
                lot.quantity * (final_price - lot.entry_price) - lot.entry_fee
                for lot in lots_by_level.values()
            ),
            ZERO,
        )

        final_equity = reserve + cash + inventory_value
        capital_return_pct = (
            (final_equity - self.settings.initial_capital)
            / self.settings.initial_capital
            * HUNDRED
        )

        return GridBacktestResult(
            initial_capital=self.settings.initial_capital,
            final_equity=final_equity,
            cash=reserve + cash,
            inventory_quantity=sum(
                (lot.quantity for lot in lots_by_level.values()),
                ZERO,
            ),
            inventory_value=inventory_value,
            realized_gross_pnl=realized_gross,
            realized_net_pnl=realized_net,
            unrealized_pnl=unrealized_pnl,
            fees_paid=fees_paid,
            buy_fills=buy_fills,
            sell_fills=sell_fills,
            completed_round_trips=round_trips,
            max_inventory_value=max_inventory_value,
            max_drawdown_pct=max_drawdown_pct,
            capital_return_pct=capital_return_pct,
            breakout_triggered=breakout_triggered,
            breakout_direction=breakout_direction,
            breakout_time_ms=breakout_time_ms,
            breakout_policy=self.settings.breakout_policy,
            protective_close_triggered=protective_close_triggered,
            protective_close_time_ms=protective_close_time_ms,
            protective_close_price=protective_close_price,
            levels=levels,
        )

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

    def validate(self) -> None:
        if self.initial_capital <= ZERO:
            raise ValueError("initial_capital must be positive")
        if self.fee_rate_pct_per_side < ZERO:
            raise ValueError("fee_rate_pct_per_side cannot be negative")
        if self.grid_count < 2:
            raise ValueError("grid_count must be at least 2")
        if not ZERO <= self.reserve_pct < HUNDRED:
            raise ValueError("reserve_pct must be in [0, 100)")


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

            # BUY after sells. Never spend unavailable cash.
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
            levels=levels,
        )

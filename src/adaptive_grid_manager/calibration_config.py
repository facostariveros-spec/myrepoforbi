from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal


HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
DEFAULT_HORIZONS_MS = (6 * HOUR_MS, 12 * HOUR_MS, DAY_MS, 2 * DAY_MS, 3 * DAY_MS, 7 * DAY_MS)


@dataclass(frozen=True, slots=True)
class CalibrationSettings:
    horizons_ms: tuple[int, ...] = DEFAULT_HORIZONS_MS
    evaluation_step_ms: int = DAY_MS
    breakout_consecutive_closes: int = 2
    breakout_duration_ms: int = 30 * 60 * 1000
    movement_thresholds_pct: tuple[Decimal, ...] = (
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("1.00"),
    )
    fee_rate_pct_per_side: Decimal = Decimal("0.10")
    minimum_fee_adjusted_move_pct: Decimal = Decimal("0.10")
    historical_spread_pct_assumption: Decimal = Decimal("0.10")
    historical_book_liquidity_quote_assumption: Decimal = Decimal("100000")
    train_ratio: Decimal = Decimal("0.60")
    validation_ratio: Decimal = Decimal("0.20")
    test_ratio: Decimal = Decimal("0.20")
    walk_forward: bool = False
    walk_forward_folds: int = 3

    def validate(self) -> None:
        if not self.horizons_ms or any(value <= 0 for value in self.horizons_ms):
            raise ValueError("forward horizons must be positive")
        if self.evaluation_step_ms <= 0:
            raise ValueError("evaluation step must be positive")
        if self.breakout_consecutive_closes < 1 or self.breakout_duration_ms <= 0:
            raise ValueError("breakout confirmation settings must be positive")
        if any(value <= 0 for value in self.movement_thresholds_pct):
            raise ValueError("movement thresholds must be positive")
        if min(
            self.fee_rate_pct_per_side,
            self.minimum_fee_adjusted_move_pct,
            self.historical_spread_pct_assumption,
            self.historical_book_liquidity_quote_assumption,
        ) < 0:
            raise ValueError("research assumptions must be non-negative")
        if self.train_ratio <= 0 or self.validation_ratio <= 0 or self.test_ratio <= 0:
            raise ValueError("dataset ratios must be positive")
        if self.train_ratio + self.validation_ratio + self.test_ratio != Decimal(1):
            raise ValueError("dataset ratios must sum to 1")
        if self.walk_forward_folds < 1:
            raise ValueError("walk-forward folds must be positive")

    def as_manifest(self) -> dict[str, object]:
        return asdict(self)


def parse_duration(value: str) -> int:
    units = {"m": 60_000, "h": HOUR_MS, "d": DAY_MS}
    if len(value) < 2 or value[-1].lower() not in units:
        raise ValueError(f"invalid duration: {value}")
    amount = int(value[:-1])
    if amount <= 0:
        raise ValueError(f"invalid duration: {value}")
    return amount * units[value[-1].lower()]


def duration_label(milliseconds: int) -> str:
    if milliseconds % DAY_MS == 0:
        return f"{milliseconds // DAY_MS}d"
    if milliseconds % HOUR_MS == 0:
        return f"{milliseconds // HOUR_MS}h"
    return f"{milliseconds // 60_000}m"


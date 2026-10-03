from __future__ import annotations

from decimal import Decimal

from .calibration_config import CalibrationSettings, duration_label
from .indicators import realized_volatility
from .market_models import Candle, safe_api_value


HUNDRED = Decimal(100)


class ForwardOutcomeEngine:
    def __init__(self, settings: CalibrationSettings) -> None:
        self.settings = settings

    @staticmethod
    def forward_slice(
        candles: list[Candle], evaluation_ms: int, horizon_ms: int
    ) -> list[Candle]:
        end_ms = evaluation_ms + horizon_ms
        return [
            candle
            for candle in candles
            if candle.open_time >= evaluation_ms and candle.close_time <= end_ms
        ]

    def evaluate(
        self,
        evaluation_ms: int,
        horizon_ms: int,
        forward_candles: list[Candle],
        proposed_range: dict[str, object] | None,
    ) -> dict[str, object]:
        if not forward_candles:
            return {
                "horizon": duration_label(horizon_ms),
                "horizon_ms": horizon_ms,
                "data_quality_status": "insufficient_forward_history",
            }
        lower = self._decimal(proposed_range.get("proposed_lower")) if proposed_range else None
        upper = self._decimal(proposed_range.get("proposed_upper")) if proposed_range else None
        start_price = self._decimal(proposed_range.get("current_price")) if proposed_range else None
        if lower is None or upper is None or start_price is None or not (0 < lower < upper):
            return {
                "horizon": duration_label(horizon_ms),
                "horizon_ms": horizon_ms,
                "data_quality_status": "range_unavailable",
            }

        closes_inside = sum(lower <= candle.close <= upper for candle in forward_candles)
        first_upper = next(
            (candle.close_time for candle in forward_candles if candle.close > upper), None
        )
        first_lower = next(
            (candle.close_time for candle in forward_candles if candle.close < lower), None
        )
        first_violation = min(
            (value for value in (first_upper, first_lower) if value is not None), default=None
        )
        returned_inside = False
        if first_violation is not None:
            returned_inside = any(
                candle.close_time > first_violation and lower <= candle.close <= upper
                for candle in forward_candles
            )

        confirmed = self._confirmed_breakout(forward_candles, lower, upper)
        midpoint = (lower + upper) / Decimal(2)
        mean_reversion = self._mean_reversion(forward_candles, lower, upper, midpoint)
        opportunity = self._opportunity(forward_candles, start_price, midpoint)
        minimum_low = min(candle.low for candle in forward_candles)
        maximum_high = max(candle.high for candle in forward_candles)
        outcome = {
            "horizon": duration_label(horizon_ms),
            "horizon_ms": horizon_ms,
            "data_quality_status": "ok",
            "forward_candle_count": len(forward_candles),
            "range_durability": {
                "inside_close_percentage": Decimal(closes_inside)
                / Decimal(len(forward_candles))
                * HUNDRED,
                "inside_close_count": closes_inside,
                "first_upper_violation_time_ms": first_upper,
                "first_lower_violation_time_ms": first_lower,
                "time_to_first_violation_ms": None
                if first_violation is None
                else first_violation - evaluation_ms,
                "returned_inside_after_violation": returned_inside,
                "maximum_continuous_time_outside_ms": self._maximum_outside_duration(
                    forward_candles, lower, upper
                ),
                "upper_wick_violation_count": sum(
                    candle.high > upper for candle in forward_candles
                ),
                "lower_wick_violation_count": sum(
                    candle.low < lower for candle in forward_candles
                ),
            },
            "breakout": confirmed,
            "mean_reversion": mean_reversion,
            "grid_opportunity_proxy": opportunity,
            "excursion": {
                "maximum_adverse_excursion_pct": (minimum_low - start_price)
                / start_price
                * HUNDRED,
                "maximum_favorable_excursion_pct": (maximum_high - start_price)
                / start_price
                * HUNDRED,
                "maximum_excursion_above_upper_pct": max(
                    Decimal(0), (maximum_high - upper) / upper * HUNDRED
                ),
                "maximum_excursion_below_lower_pct": max(
                    Decimal(0), (lower - minimum_low) / lower * HUNDRED
                ),
            },
        }
        return safe_api_value(outcome)

    def _confirmed_breakout(
        self, candles: list[Candle], lower: Decimal, upper: Decimal
    ) -> dict[str, object]:
        runs = {"UPPER": [], "LOWER": []}
        confirmed_events = []
        raw_observations = []
        for candle in candles:
            direction = "UPPER" if candle.close > upper else "LOWER" if candle.close < lower else None
            raw_observations.append(
                {
                    "close_time_ms": candle.close_time,
                    "direction": direction or "INSIDE",
                    "close": candle.close,
                    "high": candle.high,
                    "low": candle.low,
                }
            )
            for key in runs:
                if key == direction:
                    runs[key].append(candle)
                else:
                    runs[key] = []
            if direction:
                run = runs[direction]
                duration = run[-1].close_time - run[0].open_time + 1
                if (
                    len(run) >= self.settings.breakout_consecutive_closes
                    or duration >= self.settings.breakout_duration_ms
                ) and not any(event["direction"] == direction for event in confirmed_events):
                    confirmed_events.append(
                        {
                            "direction": direction,
                            "confirmed_time_ms": candle.close_time,
                            "consecutive_closes": len(run),
                            "outside_duration_ms": duration,
                        }
                    )
        confirmed_events.sort(key=lambda item: item["confirmed_time_ms"])
        first = confirmed_events[0] if confirmed_events else None
        return {
            "classification": "NO_BREAKOUT" if first is None else f"{first['direction']}_BREAKOUT",
            "confirmed": first is not None,
            "direction": None if first is None else first["direction"],
            "confirmed_time_ms": None if first is None else first["confirmed_time_ms"],
            "confirmation_rule": {
                "consecutive_closes": self.settings.breakout_consecutive_closes,
                "duration_ms": self.settings.breakout_duration_ms,
                "operator": "OR",
            },
            "confirmed_events": confirmed_events,
            "raw_observations": raw_observations,
        }

    @staticmethod
    def _maximum_outside_duration(
        candles: list[Candle], lower: Decimal, upper: Decimal
    ) -> int:
        longest = 0
        start = None
        end = None
        for candle in candles:
            if candle.close < lower or candle.close > upper:
                if start is None:
                    start = candle.open_time
                end = candle.close_time
                longest = max(longest, end - start + 1)
            else:
                start = end = None
        return longest

    @staticmethod
    def _mean_reversion(
        candles: list[Candle], lower: Decimal, upper: Decimal, midpoint: Decimal
    ) -> dict[str, object]:
        signs = [1 if candle.close > midpoint else -1 if candle.close < midpoint else 0 for candle in candles]
        nonzero = [sign for sign in signs if sign]
        crossings = sum(current != previous for previous, current in zip(nonzero, nonzero[1:]))
        width = upper - lower
        lower_zone = lower + width / Decimal(3)
        upper_zone = upper - width / Decimal(3)
        active: tuple[int, Decimal] | None = None
        returns = 0
        distance = Decimal(0)
        latencies = []
        for candle in candles:
            if active is None:
                if candle.close <= lower_zone or candle.close >= upper_zone:
                    active = (candle.close_time, candle.close)
                continue
            if lower_zone < candle.close < upper_zone:
                entry_time, entry_price = active
                returns += 1
                distance += max(
                    Decimal(0), abs(entry_price - midpoint) - abs(candle.close - midpoint)
                )
                latencies.append(candle.close_time - entry_time)
                active = None
        return {
            "midpoint_crossings": crossings,
            "returns_toward_midpoint": returns,
            "distance_traveled_toward_midpoint": distance,
            "mean_reversion_latency_ms": None
            if not latencies
            else sum(latencies) // len(latencies),
        }

    def _opportunity(
        self, candles: list[Candle], start_price: Decimal, midpoint: Decimal
    ) -> dict[str, object]:
        prices = [start_price] + [candle.close for candle in candles]
        changes = [current - previous for previous, current in zip(prices, prices[1:])]
        percentage_moves = [
            abs(change) / previous * HUNDRED
            for previous, change in zip(prices, changes)
            if previous > 0
        ]
        movement_counts = {
            format(threshold, "f"): sum(move >= threshold for move in percentage_moves)
            for threshold in self.settings.movement_thresholds_pct
        }
        signs = [1 if change > 0 else -1 if change < 0 else 0 for change in changes]
        nonzero = [sign for sign in signs if sign]
        alternating = sum(current != previous for previous, current in zip(nonzero, nonzero[1:]))
        fee_adjusted_threshold = (
            self.settings.fee_rate_pct_per_side * Decimal(2)
            + self.settings.minimum_fee_adjusted_move_pct
        )
        volatility = realized_volatility(candles, len(candles) - 1) if len(candles) >= 2 else None
        midpoint_signs = [
            1 if candle.close > midpoint else -1 if candle.close < midpoint else 0
            for candle in candles
        ]
        midpoint_nonzero = [sign for sign in midpoint_signs if sign]
        potential_oscillations = sum(
            current != previous
            for previous, current in zip(midpoint_nonzero, midpoint_nonzero[1:])
        )
        return {
            "total_absolute_price_travel": sum((abs(change) for change in changes), Decimal(0)),
            "total_absolute_price_travel_pct": sum(percentage_moves, Decimal(0)),
            "realized_forward_volatility": volatility,
            "movement_counts_by_threshold_pct": movement_counts,
            "alternating_direction_movements": alternating,
            "potential_oscillation_count": potential_oscillations,
            "estimated_fee_adjusted_opportunity_count": sum(
                move >= fee_adjusted_threshold for move in percentage_moves
            ),
            "fee_assumption_pct_per_side": self.settings.fee_rate_pct_per_side,
            "fee_adjusted_move_threshold_pct": fee_adjusted_threshold,
            "profitability_claim": False,
        }

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except Exception:
            return None
        return parsed if parsed.is_finite() else None

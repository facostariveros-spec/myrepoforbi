from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .market_models import Candle


ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class PostBreakoutResult:
    direction: str
    breakout_time_ms: int
    confirmation_price: Decimal
    boundary: Decimal
    distance_outside_at_confirmation_pct: Decimal

    reentered: bool
    reentry_time_ms: int | None
    time_to_reentry_ms: int | None

    worst_price: Decimal
    best_price: Decimal
    max_continuation_pct: Decimal
    max_recovery_pct: Decimal

    observation_end_ms: int
    post_breakout_candles: int


def analyze_post_breakout(
    candles: Iterable[Candle],
    *,
    breakout_time_ms: int,
    direction: str,
    lower: Decimal,
    upper: Decimal,
) -> PostBreakoutResult:
    """
    Analyze market behavior from a confirmed breakout forward.

    Causal boundary:
    - breakout_time_ms is the close time of the confirmation candle.
    - Candles closing before breakout_time_ms are ignored.
    - The confirmation candle is used only to establish the known
      confirmation close/reference state.
    - Reentry and subsequent excursion measurements begin only after
      the confirmation candle has closed.

    Reentry definition:
    - LOWER breakout: first subsequent candle CLOSE >= lower.
    - UPPER breakout: first subsequent candle CLOSE <= upper.

    Intrabar LOW/HIGH may be used after confirmation to measure
    excursions, but never to establish an earlier decision.
    """
    if lower <= ZERO or upper <= lower:
        raise ValueError("invalid grid range")

    if direction not in {"LOWER", "UPPER"}:
        raise ValueError("direction must be LOWER or UPPER")

    ordered = sorted(candles, key=lambda candle: candle.close_time)

    eligible = [
        candle
        for candle in ordered
        if candle.close_time >= breakout_time_ms
    ]

    if not eligible:
        raise ValueError("no candles at or after breakout confirmation")

    confirmation = next(
        (
            candle
            for candle in eligible
            if candle.close_time == breakout_time_ms
        ),
        None,
    )

    if confirmation is None:
        raise ValueError("confirmation candle not found")

    confirmation_price = confirmation.close
    boundary = lower if direction == "LOWER" else upper

    if direction == "LOWER":
        if confirmation_price >= lower:
            raise ValueError(
                "LOWER breakout confirmation must close below lower boundary"
            )

        distance_outside = (
            (lower - confirmation_price) / lower * HUNDRED
        )
    else:
        if confirmation_price <= upper:
            raise ValueError(
                "UPPER breakout confirmation must close above upper boundary"
            )

        distance_outside = (
            (confirmation_price - upper) / upper * HUNDRED
        )

    subsequent = [
        candle
        for candle in eligible
        if candle.close_time > breakout_time_ms
    ]

    # Excursions include the confirmation candle as the known starting state.
    analysis_candles = [confirmation] + subsequent

    if direction == "LOWER":
        worst_price = min(candle.low for candle in analysis_candles)
        best_price = max(candle.high for candle in analysis_candles)

        max_continuation_pct = max(
            ZERO,
            (confirmation_price - worst_price)
            / confirmation_price
            * HUNDRED,
        )

        max_recovery_pct = max(
            ZERO,
            (best_price - confirmation_price)
            / confirmation_price
            * HUNDRED,
        )

        reentry_candle = next(
            (
                candle
                for candle in subsequent
                if candle.close >= lower
            ),
            None,
        )

    else:
        worst_price = max(candle.high for candle in analysis_candles)
        best_price = min(candle.low for candle in analysis_candles)

        max_continuation_pct = max(
            ZERO,
            (worst_price - confirmation_price)
            / confirmation_price
            * HUNDRED,
        )

        max_recovery_pct = max(
            ZERO,
            (confirmation_price - best_price)
            / confirmation_price
            * HUNDRED,
        )

        reentry_candle = next(
            (
                candle
                for candle in subsequent
                if candle.close <= upper
            ),
            None,
        )

    reentered = reentry_candle is not None

    reentry_time_ms = (
        reentry_candle.close_time
        if reentry_candle is not None
        else None
    )

    time_to_reentry_ms = (
        reentry_time_ms - breakout_time_ms
        if reentry_time_ms is not None
        else None
    )

    return PostBreakoutResult(
        direction=direction,
        breakout_time_ms=breakout_time_ms,
        confirmation_price=confirmation_price,
        boundary=boundary,
        distance_outside_at_confirmation_pct=distance_outside,
        reentered=reentered,
        reentry_time_ms=reentry_time_ms,
        time_to_reentry_ms=time_to_reentry_ms,
        worst_price=worst_price,
        best_price=best_price,
        max_continuation_pct=max_continuation_pct,
        max_recovery_pct=max_recovery_pct,
        observation_end_ms=analysis_candles[-1].close_time,
        post_breakout_candles=len(subsequent),
    )

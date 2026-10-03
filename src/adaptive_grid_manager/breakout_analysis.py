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


# ---------------------------------------------------------------------------
# OOS post-breakout research runner
# ---------------------------------------------------------------------------

import argparse
import asyncio
import csv
from pathlib import Path

from .config import Settings
from .historical import HistoricalBinanceClient, HistoricalFileCache
from .transport import SafeGetTransport


MINUTE_MS = 60_000
SNAPSHOT_HORIZONS_MS = {
    "15m": 15 * MINUTE_MS,
    "30m": 30 * MINUTE_MS,
    "1h": 60 * MINUTE_MS,
    "2h": 2 * 60 * MINUTE_MS,
    "4h": 4 * 60 * MINUTE_MS,
    "8h": 8 * 60 * MINUTE_MS,
    "12h": 12 * 60 * MINUTE_MS,
}


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _csv_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None:
        return ""
    return value


def _bool_text(value: object) -> bool:
    return str(value).strip().lower() == "true"


def _snapshot_metrics(
    candles: list[Candle],
    *,
    breakout_time_ms: int,
    direction: str,
    confirmation_price: Decimal,
    boundary: Decimal,
    horizon_ms: int,
) -> dict[str, object]:
    """
    Measure only information available between confirmation and the
    requested post-breakout horizon.
    """
    target_ms = breakout_time_ms + horizon_ms

    window = [
        candle
        for candle in candles
        if breakout_time_ms < candle.close_time <= target_ms
    ]

    if not window:
        return {
            "available": False,
            "close": None,
            "return_from_confirmation_pct": None,
            "continuation_pct": None,
            "recovery_pct": None,
            "inside_range": None,
            "close_distance_to_boundary_pct": None,
            "recovery_progress_pct": None,
            "recovery_continuation_ratio": None,
            "closes_toward_range": None,
            "window_candles": 0,
        }

    last = window[-1]

    if direction == "LOWER":
        worst = min(candle.low for candle in window)
        best = max(candle.high for candle in window)

        continuation = max(
            ZERO,
            (confirmation_price - worst)
            / confirmation_price
            * HUNDRED,
        )
        recovery = max(
            ZERO,
            (best - confirmation_price)
            / confirmation_price
            * HUNDRED,
        )
        inside = last.close >= boundary

    else:
        worst = max(candle.high for candle in window)
        best = min(candle.low for candle in window)

        continuation = max(
            ZERO,
            (worst - confirmation_price)
            / confirmation_price
            * HUNDRED,
        )
        recovery = max(
            ZERO,
            (confirmation_price - best)
            / confirmation_price
            * HUNDRED,
        )
        inside = last.close <= boundary

    if direction == "LOWER":
        close_distance_to_boundary = (
            (last.close - boundary) / boundary * HUNDRED
        )
        initial_gap = boundary - confirmation_price
        recovered_gap = last.close - confirmation_price
        closes_toward_range = sum(
            1 for candle in window
            if candle.close > candle.open
        )
    else:
        close_distance_to_boundary = (
            (boundary - last.close) / boundary * HUNDRED
        )
        initial_gap = confirmation_price - boundary
        recovered_gap = confirmation_price - last.close
        closes_toward_range = sum(
            1 for candle in window
            if candle.close < candle.open
        )

    recovery_progress = (
        recovered_gap / initial_gap * HUNDRED
        if initial_gap > ZERO
        else ZERO
    )

    recovery_continuation_ratio = (
        recovery / continuation
        if continuation > ZERO
        else None
    )

    return {
        "available": True,
        "close": last.close,
        "return_from_confirmation_pct": (
            (last.close - confirmation_price)
            / confirmation_price
            * HUNDRED
        ),
        "continuation_pct": continuation,
        "recovery_pct": recovery,
        "inside_range": inside,
        "close_distance_to_boundary_pct": close_distance_to_boundary,
        "recovery_progress_pct": recovery_progress,
        "recovery_continuation_ratio": recovery_continuation_ratio,
        "closes_toward_range": closes_toward_range,
        "window_candles": len(window),
    }


async def run_oos_analysis(args: argparse.Namespace) -> Path:
    economic_path = Path(args.economic)
    output_path = Path(args.output)

    with economic_path.open(newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row.get("status") == "ok"
            and _bool_text(row.get("breakout_triggered"))
        ]

    if not rows:
        raise RuntimeError("no triggered breakout episodes found")

    # Each economic episode should be unique by symbol/evaluation time.
    keys = [
        (row["symbol"], int(row["evaluation_time_ms"]))
        for row in rows
    ]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate breakout episodes in economic input")

    service_settings = Settings.from_env()

    transport = SafeGetTransport(
        connect_timeout_seconds=service_settings.connect_timeout_seconds,
        read_timeout_seconds=service_settings.read_timeout_seconds,
        max_retries=service_settings.max_retries,
        backoff_base_seconds=service_settings.backoff_base_seconds,
        retry_after_cap_seconds=service_settings.retry_after_cap_seconds,
    )

    historical = HistoricalBinanceClient(
        service_settings.endpoints[0][1],
        transport,
        HistoricalFileCache(Path(args.cache_dir)),
    )

    analysis_horizon_ms = max(SNAPSHOT_HORIZONS_MS.values())

    # Download one contiguous 15m range per symbol. This is deliberately
    # extended to 12h AFTER the latest breakout confirmation rather than
    # merely to evaluation_time + 12h.
    by_symbol: dict[str, list[dict[str, str]]] = {}

    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    datasets: dict[str, list[Candle]] = {}

    for symbol, symbol_rows in sorted(by_symbol.items()):
        first_breakout = min(
            int(row["breakout_time_ms"])
            for row in symbol_rows
        )
        last_breakout = max(
            int(row["breakout_time_ms"])
            for row in symbol_rows
        )

        # Include one 15m candle before the earliest confirmation so page/range
        # boundaries cannot accidentally omit the confirmation candle.
        start_ms = max(0, first_breakout - 15 * MINUTE_MS)
        end_ms = last_breakout + analysis_horizon_ms

        print(
            f"downloading {symbol}: "
            f"{len(symbol_rows)} breakouts"
        )

        datasets[symbol] = await historical.candles(
            symbol,
            "15m",
            start_ms,
            end_ms,
        )

    output_rows: list[dict[str, object]] = []

    for n, row in enumerate(rows, start=1):
        symbol = row["symbol"]
        breakout_time_ms = int(row["breakout_time_ms"])
        direction = row["breakout_direction"]
        lower = _decimal(row["lower"])
        upper = _decimal(row["upper"])

        all_candles = datasets[symbol]

        episode_end_ms = breakout_time_ms + analysis_horizon_ms

        episode_candles = [
            candle
            for candle in all_candles
            if candle.close_time >= breakout_time_ms
            and candle.close_time <= episode_end_ms
        ]

        try:
            result = analyze_post_breakout(
                episode_candles,
                breakout_time_ms=breakout_time_ms,
                direction=direction,
                lower=lower,
                upper=upper,
            )
        except ValueError as exc:
            output_rows.append(
                {
                    "symbol": symbol,
                    "evaluation_time_ms": row["evaluation_time_ms"],
                    "breakout_time_ms": breakout_time_ms,
                    "breakout_direction": direction,
                    "status": "analysis_failed",
                    "error": str(exc),
                }
            )
            continue

        out: dict[str, object] = {
            "symbol": symbol,
            "evaluation_time_ms": row["evaluation_time_ms"],
            "dataset_split": row["dataset_split"],
            "economic_horizon": row["horizon"],
            "status": "ok",
            "grid_score": row["grid_score"],
            "score_bucket": row["score_bucket"],
            "price_zone": row["price_zone"],
            "range_inside_pct": row["range_inside_pct"],
            "opportunity_count": row["opportunity_count"],
            "lower": lower,
            "upper": upper,
            "start_price": row["start_price"],
            "range_width_pct": row["range_width_pct"],
            "breakout_time_ms": breakout_time_ms,
            "breakout_direction": direction,
            "confirmation_price": result.confirmation_price,
            "boundary": result.boundary,
            "distance_outside_at_confirmation_pct":
                result.distance_outside_at_confirmation_pct,
            "reentered_12h": result.reentered,
            "reentry_time_ms": result.reentry_time_ms,
            "time_to_reentry_ms": result.time_to_reentry_ms,
            "time_to_reentry_minutes": (
                Decimal(result.time_to_reentry_ms) / Decimal(MINUTE_MS)
                if result.time_to_reentry_ms is not None
                else None
            ),
            "worst_price_12h": result.worst_price,
            "best_price_12h": result.best_price,
            "max_continuation_pct_12h": result.max_continuation_pct,
            "max_recovery_pct_12h": result.max_recovery_pct,
            "post_breakout_candles": result.post_breakout_candles,
            # Preserve economic result for later paired research.
            "static_economic_pnl": row["economic_pnl"],
            "static_return_pct": row["return_pct"],
            "static_unrealized_pnl": row["unrealized_pnl"],
            "static_ending_inventory_value":
                row["ending_inventory_value"],
        }

        for label, horizon_ms in SNAPSHOT_HORIZONS_MS.items():
            snapshot = _snapshot_metrics(
                episode_candles,
                breakout_time_ms=breakout_time_ms,
                direction=direction,
                confirmation_price=result.confirmation_price,
                boundary=result.boundary,
                horizon_ms=horizon_ms,
            )

            prefix = f"post_{label}"

            out[f"{prefix}_available"] = snapshot["available"]
            out[f"{prefix}_close"] = snapshot["close"]
            out[f"{prefix}_return_pct"] = (
                snapshot["return_from_confirmation_pct"]
            )
            out[f"{prefix}_continuation_pct"] = (
                snapshot["continuation_pct"]
            )
            out[f"{prefix}_recovery_pct"] = snapshot["recovery_pct"]
            out[f"{prefix}_inside_range"] = snapshot["inside_range"]

        output_rows.append(out)

        if n % 25 == 0:
            print(f"processed {n}/{len(rows)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fields = sorted(
        {key for row in output_rows for key in row}
    )

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        for row in output_rows:
            writer.writerow(
                {
                    key: _csv_value(row.get(key))
                    for key in fields
                }
            )

    successful = sum(
        row.get("status") == "ok"
        for row in output_rows
    )

    print()
    print("=== POST-BREAKOUT OOS ANALYSIS COMPLETE ===")
    print(f"input breakouts : {len(rows)}")
    print(f"successful      : {successful}")
    print(f"failed          : {len(output_rows) - successful}")
    print(f"requests made   : {historical.requests_made}")
    print(f"cache hits      : {historical.cache_hits}")
    print(f"cache misses    : {historical.cache_misses}")
    print(f"output          : {output_path}")

    return output_path


def build_analysis_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OOS causal post-breakout analysis"
    )
    parser.add_argument("--economic", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--cache-dir",
        default=".calibration_cache",
    )
    return parser


async def _analysis_main() -> None:
    parser = build_analysis_parser()
    args = parser.parse_args()
    await run_oos_analysis(args)


if __name__ == "__main__":
    asyncio.run(_analysis_main())

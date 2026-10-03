from __future__ import annotations

import argparse
import asyncio
import csv
from decimal import Decimal
from pathlib import Path

from .calibration_config import CalibrationSettings, parse_duration
from .config import Settings
from .economic_backtest import (
    GridBacktestSettings,
    StaticGridEconomicBacktester,
)
from .historical import (
    HistoricalBinanceClient,
    HistoricalFileCache,
    HistoricalReplayEngine,
)
from .intelligence import GridCandidateIntelligence
from .intelligence_config import IntelligenceSettings
from .scanner_config import ScannerSettings
from .transport import SafeGetTransport


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def csv_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return "|".join(
            format(x, "f") if isinstance(x, Decimal) else str(x)
            for x in value
        )
    return value


def score_bucket(score: Decimal) -> str:
    if score < Decimal("65"):
        return "60-65"
    if score < Decimal("70"):
        return "65-70"
    if score < Decimal("75"):
        return "70-75"
    if score < Decimal("80"):
        return "75-80"
    return "80+"


async def run(args: argparse.Namespace) -> Path:
    observations_path = Path(args.observations)
    output_path = Path(args.output)

    rows = []
    with observations_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["dataset_split"] != "TEST":
                continue
            if row["horizon"] != args.horizon:
                continue
            if row["outcome_status"] != "ok":
                continue
            if dec(row["grid_score"]) < dec(args.min_score):
                continue
            rows.append(row)

    if not rows:
        raise RuntimeError("no matching observations")

    symbols = sorted({row["symbol"] for row in rows})
    evaluation_times = [int(row["evaluation_time_ms"]) for row in rows]

    horizon_ms = parse_duration(args.horizon)
    min_eval = min(evaluation_times)
    max_eval = max(evaluation_times)

    service_settings = Settings.from_env()
    scanner_settings = ScannerSettings.from_env()
    intelligence_settings = IntelligenceSettings.from_env()

    calibration_settings = CalibrationSettings(
        horizons_ms=(horizon_ms,),
        fee_rate_pct_per_side=dec(args.fee_rate_pct),
    )

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

    intelligence = GridCandidateIntelligence(intelligence_settings)
    replay = HistoricalReplayEngine(
        scanner_settings,
        intelligence,
        calibration_settings,
    )

    backtester = StaticGridEconomicBacktester(
        GridBacktestSettings(
            initial_capital=dec(args.capital),
            fee_rate_pct_per_side=dec(args.fee_rate_pct),
            grid_count=args.grid_count,
            reserve_pct=dec(args.reserve_pct),
        )
    )

    # Same warmup concept used by calibration.
    day_ms = 24 * 60 * 60 * 1000
    warmup_ms = scanner_settings.kline_limit * day_ms
    data_start = max(0, min_eval - warmup_ms)
    data_end = max_eval + horizon_ms

    dataset = {}

    for symbol in symbols:
        dataset[symbol] = {}
        for timeframe in ("15m", "1h", "4h", "1d"):
            dataset[symbol][timeframe] = await historical.candles(
                symbol,
                timeframe,
                data_start,
                data_end,
            )

    results = []

    for n, row in enumerate(rows, start=1):
        symbol = row["symbol"]
        evaluation_ms = int(row["evaluation_time_ms"])

        assessment, errors = replay.assessment_at(
            symbol,
            dataset[symbol],
            evaluation_ms,
        )

        if assessment is None:
            results.append(
                {
                    "symbol": symbol,
                    "evaluation_time_ms": evaluation_ms,
                    "dataset_split": "TEST",
                    "horizon": args.horizon,
                    "status": "assessment_failed",
                    "error": "|".join(errors),
                }
            )
            continue

        proposed = assessment.get("proposed_range")

        if not proposed:
            results.append(
                {
                    "symbol": symbol,
                    "evaluation_time_ms": evaluation_ms,
                    "dataset_split": "TEST",
                    "horizon": args.horizon,
                    "status": "range_unavailable",
                }
            )
            continue

        lower = dec(proposed["proposed_lower"])
        upper = dec(proposed["proposed_upper"])
        start_price = dec(proposed["current_price"])

        forward = [
            candle
            for candle in dataset[symbol]["15m"]
            if candle.open_time >= evaluation_ms
            and candle.close_time <= evaluation_ms + horizon_ms
        ]

        if not forward:
            continue

        try:
            result = backtester.run(
                forward,
                lower=lower,
                upper=upper,
                start_price=start_price,
            )
        except ValueError as exc:
            results.append(
                {
                    "symbol": symbol,
                    "evaluation_time_ms": evaluation_ms,
                    "dataset_split": "TEST",
                    "horizon": args.horizon,
                    "status": "not_executable",
                    "error": str(exc),
                    "grid_score": row["grid_score"],
                    "price_zone": row["price_zone"],
                }
            )
            continue

        economic_pnl = result.final_equity - result.initial_capital

        results.append(
            {
                "symbol": symbol,
                "evaluation_time_ms": evaluation_ms,
                "dataset_split": "TEST",
                "horizon": args.horizon,
                "status": "ok",
                "grid_score": row["grid_score"],
                "score_bucket": score_bucket(dec(row["grid_score"])),
                "price_zone": row["price_zone"],
                "confirmed_breakout": row["confirmed_breakout"],
                "range_inside_pct": row["range_inside_pct"],
                "opportunity_count": row["opportunity_count"],
                "lower": lower,
                "upper": upper,
                "start_price": start_price,
                "range_width_pct": proposed["range_width_pct"],
                "initial_capital": result.initial_capital,
                "final_equity": result.final_equity,
                "economic_pnl": economic_pnl,
                "return_pct": result.capital_return_pct,
                "realized_gross_pnl": result.realized_gross_pnl,
                "realized_net_pnl": result.realized_net_pnl,
                "unrealized_pnl": result.unrealized_pnl,
                "fees_paid": result.fees_paid,
                "buy_fills": result.buy_fills,
                "sell_fills": result.sell_fills,
                "total_fills": result.buy_fills + result.sell_fills,
                "completed_round_trips": result.completed_round_trips,
                "max_inventory_value": result.max_inventory_value,
                "max_drawdown_pct": result.max_drawdown_pct,
                "ending_cash": result.cash,
                "ending_inventory_quantity": result.inventory_quantity,
                "ending_inventory_value": result.inventory_value,
                "grid_count": args.grid_count,
                "reserve_pct": args.reserve_pct,
                "fee_rate_pct_per_side": args.fee_rate_pct,
            }
        )

        if n % 100 == 0:
            print(f"processed {n}/{len(rows)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fields = sorted({key for row in results for key in row})

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow(
                {key: csv_value(row.get(key)) for key in fields}
            )

    ok = sum(row.get("status") == "ok" for row in results)

    print()
    print("=== ECONOMIC VALIDATION COMPLETE ===")
    print(f"input observations : {len(rows)}")
    print(f"successful         : {ok}")
    print(f"other              : {len(results) - ok}")
    print(f"output             : {output_path}")

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-dir", default=".calibration_cache")
    parser.add_argument("--horizon", default="12h")
    parser.add_argument("--min-score", default="60")
    parser.add_argument("--capital", default="500")
    parser.add_argument("--fee-rate-pct", default="0.10")
    parser.add_argument("--grid-count", type=int, default=10)
    parser.add_argument("--reserve-pct", default="5")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

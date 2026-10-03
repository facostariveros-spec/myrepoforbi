from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from . import __version__
from .calibration_config import (
    CalibrationSettings,
    DAY_MS,
    duration_label,
    parse_duration,
)
from .config import Settings
from .historical import (
    HistoricalBinanceClient,
    HistoricalFileCache,
    HistoricalReplayEngine,
    INTERVAL_MS,
)
from .intelligence import GridCandidateIntelligence
from .intelligence_config import IntelligenceSettings
from .outcomes import ForwardOutcomeEngine
from .scanner_config import ScannerSettings, TIMEFRAMES
from .transport import SafeGetTransport
from .validation import (
    COMPONENT_NAMES,
    chronological_split,
    component_statistics,
    fold_statistics,
    regime_statistics,
    risk_bucket_statistics,
    score_bucket_statistics,
    walk_forward_folds,
    zone_statistics,
)


class CalibrationRunner:
    def __init__(
        self,
        historical_client: HistoricalBinanceClient,
        replay: HistoricalReplayEngine,
        outcomes: ForwardOutcomeEngine,
        scanner_settings: ScannerSettings,
        intelligence_settings: IntelligenceSettings,
        calibration_settings: CalibrationSettings,
        *,
        clock: Any = time.time,
    ) -> None:
        self.historical_client = historical_client
        self.replay = replay
        self.outcomes = outcomes
        self.scanner_settings = scanner_settings
        self.intelligence_settings = intelligence_settings
        self.settings = calibration_settings
        self.clock = clock

    async def run(
        self,
        symbols: list[str],
        start_ms: int,
        end_ms: int,
        output_root: Path,
    ) -> Path:
        self.settings.validate()
        normalized_symbols = sorted(set(symbol.upper() for symbol in symbols))
        if not normalized_symbols or any(not symbol.endswith("USDT") for symbol in normalized_symbols):
            raise ValueError("symbols must be non-empty USDT symbols")
        if end_ms <= start_ms:
            raise ValueError("end must be after start")

        warmup_ms = self.scanner_settings.kline_limit * INTERVAL_MS["1d"]
        data_start = max(0, start_ms - warmup_ms)
        data_end = end_ms + max(self.settings.horizons_ms)
        dataset = {}
        errors: list[dict[str, object]] = []
        for symbol in normalized_symbols:
            dataset[symbol] = {}
            for timeframe in TIMEFRAMES:
                try:
                    dataset[symbol][timeframe] = await self.historical_client.candles(
                        symbol, timeframe, data_start, data_end
                    )
                except (RuntimeError, ValueError) as exc:
                    category = str(exc).split(":", 1)[0]
                    errors.append(
                        {"symbol": symbol, "timeframe": timeframe, "category": category}
                    )
                    dataset[symbol][timeframe] = []

        observations = []
        assessment_cache: dict[tuple[str, int], tuple[dict[str, object] | None, list[str]]] = {}
        sample_errors = []
        for evaluation_ms in range(start_ms, end_ms + 1, self.settings.evaluation_step_ms):
            for symbol in normalized_symbols:
                assessment, assessment_errors = self.replay.assessment_at(
                    symbol, dataset[symbol], evaluation_ms
                )
                assessment_cache[(symbol, evaluation_ms)] = (assessment, assessment_errors)
                if assessment is None:
                    sample_errors.append(
                        {
                            "symbol": symbol,
                            "evaluation_time_ms": evaluation_ms,
                            "errors": assessment_errors,
                        }
                    )
                    continue
                proposed_range = assessment.get("proposed_range")
                for horizon_ms in self.settings.horizons_ms:
                    forward = self.outcomes.forward_slice(
                        dataset[symbol]["15m"], evaluation_ms, horizon_ms
                    )
                    outcome = self.outcomes.evaluate(
                        evaluation_ms, horizon_ms, forward, proposed_range
                    )
                    future_time = evaluation_ms + horizon_ms
                    if (symbol, future_time) not in assessment_cache:
                        assessment_cache[(symbol, future_time)] = self.replay.assessment_at(
                            symbol, dataset[symbol], future_time
                        )
                    future_assessment = assessment_cache[(symbol, future_time)][0]
                    observations.append(
                        self._observation(
                            symbol,
                            evaluation_ms,
                            assessment,
                            outcome,
                            None
                            if future_assessment is None
                            else future_assessment["regimes"]["combined_regime"],
                        )
                    )

        unique_times = {item["evaluation_time_ms"] for item in observations}
        if len(unique_times) >= 3:
            output_observations = chronological_split(
                observations,
                self.settings.train_ratio,
                self.settings.validation_ratio,
                self.settings.test_ratio,
            )
        else:
            output_observations = [
                {**item, "dataset_split": "UNASSIGNED"} for item in observations
            ]
        complete = [
            item for item in output_observations if item["outcome_status"] == "ok"
        ]
        folds = (
            walk_forward_folds(
                complete,
                self.settings.train_ratio,
                self.settings.validation_ratio,
                self.settings.test_ratio,
                self.settings.walk_forward_folds,
            )
            if self.settings.walk_forward and complete
            else []
        )

        identity = {
            "symbols": normalized_symbols,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "scanner": asdict(self.scanner_settings),
            "intelligence": asdict(self.intelligence_settings),
            "calibration": asdict(self.settings),
            "code_version": __version__,
        }
        run_id = hashlib.sha256(
            json.dumps(_json_safe(identity), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        created_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        manifest = {
            "run_id": run_id,
            "timestamp": created_at,
            "code_version": __version__,
            "symbols": normalized_symbols,
            "date_range": {"start_ms": start_ms, "end_ms": end_ms},
            "timeframes": list(TIMEFRAMES),
            "forward_horizons": [duration_label(value) for value in self.settings.horizons_ms],
            "grid_score_weights": self.intelligence_settings.weights,
            "scanner_configuration": asdict(self.scanner_settings),
            "intelligence_configuration": asdict(self.intelligence_settings),
            "range_engine_configuration": {
                "range_atr_buffer": self.intelligence_settings.range_atr_buffer,
                "production_logic_reused": True,
            },
            "research_configuration": asdict(self.settings),
            "dataset_split": {
                "method": "chronological_no_shuffle",
                "train": self.settings.train_ratio,
                "validation": self.settings.validation_ratio,
                "test": self.settings.test_ratio,
            },
            "sample_counts": {
                "observations_total": len(observations),
                "observations_complete": len(complete),
                "missing_assessments": len(sample_errors),
            },
            "download_diagnostics": {
                "requests_made": self.historical_client.requests_made,
                "cache_hits": self.historical_client.cache_hits,
                "cache_misses": self.historical_client.cache_misses,
            },
            "errors": errors + self.historical_client.errors + sample_errors,
            "separation_of_concerns": {
                "observation": "enabled",
                "calibration_reporting": "enabled",
                "optimization": "disabled",
                "production_parameter_mutation": "disabled",
            },
        }
        result_sets = {
            "observations": output_observations,
            "score_buckets": score_bucket_statistics(complete),
            "breakout_risk_buckets": risk_bucket_statistics(complete, "breakout_risk"),
            "fomo_risk_buckets": risk_bucket_statistics(complete, "fomo_risk"),
            "component_validation": component_statistics(complete),
            "regime_validation": regime_statistics(complete),
            "zone_validation": zone_statistics(complete),
            "walk_forward": fold_statistics(folds),
        }
        summary = {
            "run_id": run_id,
            "sample_counts": manifest["sample_counts"],
            "score_buckets": result_sets["score_buckets"],
            "breakout_risk_buckets": result_sets["breakout_risk_buckets"],
            "optimization_performed": False,
        }
        run_directory = output_root / run_id
        ResultWriter(run_directory).write(manifest, result_sets, summary)
        return run_directory

    @staticmethod
    def _observation(
        symbol: str,
        evaluation_ms: int,
        assessment: dict[str, object],
        outcome: dict[str, object],
        future_regime: object,
    ) -> dict[str, object]:
        row = {
            "symbol": symbol,
            "evaluation_time_ms": evaluation_ms,
            "horizon": outcome["horizon"],
            "horizon_ms": outcome["horizon_ms"],
            "outcome_status": outcome["data_quality_status"],
            "candidate_state": assessment["candidate_state"],
            "grid_score": assessment["grid_score"],
            "breakout_risk": assessment["risks"]["breakout_risk"]["score"],
            "fomo_risk": assessment["risks"]["fomo_risk"]["score"],
            "initial_regime": assessment["regimes"]["combined_regime"],
            "future_regime": future_regime,
            "price_zone": (assessment.get("proposed_range") or {}).get("price_zone"),
        }
        for component in COMPONENT_NAMES:
            row[component] = assessment["score_components"][component]["score"]
        if outcome["data_quality_status"] == "ok":
            row.update(
                {
                    "range_inside_pct": outcome["range_durability"]["inside_close_percentage"],
                    "confirmed_breakout": outcome["breakout"]["confirmed"],
                    "breakout_direction": outcome["breakout"]["direction"],
                    "time_to_breakout_ms": None
                    if outcome["breakout"]["confirmed_time_ms"] is None
                    else outcome["breakout"]["confirmed_time_ms"] - evaluation_ms,
                    "midpoint_crossings": outcome["mean_reversion"]["midpoint_crossings"],
                    "returns_toward_midpoint": outcome["mean_reversion"]["returns_toward_midpoint"],
                    "oscillation_count": outcome["grid_opportunity_proxy"]["potential_oscillation_count"],
                    "forward_realized_volatility": outcome["grid_opportunity_proxy"]["realized_forward_volatility"],
                    "opportunity_count": outcome["grid_opportunity_proxy"]["estimated_fee_adjusted_opportunity_count"],
                    "adverse_excursion_pct": outcome["excursion"]["maximum_adverse_excursion_pct"],
                    "favorable_excursion_pct": outcome["excursion"]["maximum_favorable_excursion_pct"],
                }
            )
        return row


class ResultWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def write(
        self,
        manifest: dict[str, object],
        result_sets: dict[str, list[dict[str, object]]],
        summary: dict[str, object],
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._json("manifest.json", manifest)
        for name, rows in result_sets.items():
            self._csv(f"{name}.csv", rows)
        self._json("summary.json", summary)
        lines = [
            f"Calibration run: {summary['run_id']}",
            f"Complete observations: {summary['sample_counts']['observations_complete']}",
            "Optimization performed: no",
            "Results are observational and do not establish profitability.",
            "",
            "Grid Score buckets:",
        ]
        lines.extend(
            json.dumps(_json_safe(row), sort_keys=True)
            for row in summary.get("score_buckets", [])
        )
        (self.directory / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _json(self, name: str, payload: object) -> None:
        (self.directory / name).write_text(
            json.dumps(_json_safe(payload), sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def _csv(self, name: str, rows: list[dict[str, object]]) -> None:
        path = self.directory / name
        fieldnames = sorted({key for row in rows for key in row})
        with path.open("w", newline="", encoding="utf-8") as handle:
            if not fieldnames:
                handle.write("")
                return
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: _csv_value(row.get(key))
                        for key in fieldnames
                    }
                )


def _csv_value(value: object) -> object:
    converted = _json_safe(value)
    if isinstance(converted, (dict, list)):
        return json.dumps(converted, sort_keys=True, separators=(",", ":"))
    return converted


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    return value


def _date_ms(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline historical grid-candidate calibration")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD")
    parser.add_argument("--output", default="calibration_results")
    parser.add_argument("--cache-dir", default=".calibration_cache")
    parser.add_argument("--horizons", nargs="+", default=["6h", "12h", "24h", "48h", "72h", "7d"])
    parser.add_argument("--evaluation-step", default="24h")
    parser.add_argument("--train-ratio", default="0.60")
    parser.add_argument("--validation-ratio", default="0.20")
    parser.add_argument("--test-ratio", default="0.20")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--walk-forward-folds", type=int, default=3)
    parser.add_argument("--fee-rate-pct", default="0.10")
    parser.add_argument("--minimum-fee-adjusted-move-pct", default="0.10")
    parser.add_argument("--movement-thresholds-pct", nargs="+", default=["0.25", "0.50", "1.00"])
    parser.add_argument("--historical-spread-pct", default="0.10")
    parser.add_argument("--historical-book-liquidity-quote", default="100000")
    parser.add_argument("--breakout-consecutive-closes", type=int, default=2)
    parser.add_argument("--breakout-duration", default="30m")
    return parser


async def _run_cli(args: argparse.Namespace) -> Path:
    service_settings = Settings.from_env()
    scanner_settings = ScannerSettings.from_env()
    intelligence_settings = IntelligenceSettings.from_env()
    calibration_settings = CalibrationSettings(
        horizons_ms=tuple(parse_duration(value) for value in args.horizons),
        evaluation_step_ms=parse_duration(args.evaluation_step),
        breakout_consecutive_closes=args.breakout_consecutive_closes,
        breakout_duration_ms=parse_duration(args.breakout_duration),
        fee_rate_pct_per_side=Decimal(args.fee_rate_pct),
        minimum_fee_adjusted_move_pct=Decimal(args.minimum_fee_adjusted_move_pct),
        movement_thresholds_pct=tuple(
            Decimal(value) for value in args.movement_thresholds_pct
        ),
        historical_spread_pct_assumption=Decimal(args.historical_spread_pct),
        historical_book_liquidity_quote_assumption=Decimal(
            args.historical_book_liquidity_quote
        ),
        train_ratio=Decimal(args.train_ratio),
        validation_ratio=Decimal(args.validation_ratio),
        test_ratio=Decimal(args.test_ratio),
        walk_forward=args.walk_forward,
        walk_forward_folds=args.walk_forward_folds,
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
    replay = HistoricalReplayEngine(scanner_settings, intelligence, calibration_settings)
    runner = CalibrationRunner(
        historical,
        replay,
        ForwardOutcomeEngine(calibration_settings),
        scanner_settings,
        intelligence_settings,
        calibration_settings,
    )
    try:
        return await runner.run(
            args.symbols, _date_ms(args.start), _date_ms(args.end), Path(args.output)
        )
    finally:
        await transport.close()


def main() -> None:
    args = build_parser().parse_args()
    output = asyncio.run(_run_cli(args))
    print(output)


if __name__ == "__main__":
    main()

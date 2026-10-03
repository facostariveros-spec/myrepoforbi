from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json

from adaptive_grid_manager.calibration import ResultWriter
from adaptive_grid_manager.calibration_config import CalibrationSettings
from adaptive_grid_manager.historical import HistoricalFileCache, HistoricalReplayEngine, INTERVAL_MS
from adaptive_grid_manager.intelligence import GridCandidateIntelligence
from adaptive_grid_manager.intelligence_config import IntelligenceSettings
from adaptive_grid_manager.market_models import Candle
from adaptive_grid_manager.outcomes import ForwardOutcomeEngine
from adaptive_grid_manager.scanner_config import ScannerSettings, TIMEFRAMES
from adaptive_grid_manager.validation import (
    chronological_split,
    decile_bucket,
    grid_score_bucket,
    spearman,
    walk_forward_folds,
)


def scanner_settings():
    return ScannerSettings(
        max_concurrency=2,
        max_limit=10,
        quote_volume_min=Decimal("1000"),
        trade_count_min=10,
        spread_pct_max=Decimal("1"),
        book_liquidity_quote_min=Decimal("100"),
        denylist=frozenset(),
        kline_limit=40,
        minimum_candles=10,
        indicator_period=3,
        volatility_period=5,
        rolling_period=5,
        volume_period=5,
        oscillation_period=5,
        exchange_info_ttl_seconds=10,
        ticker_ttl_seconds=10,
        kline_ttl_seconds=10,
    )


def calibration_settings(**overrides):
    values = {
        "horizons_ms": (6 * 60 * 60 * 1000,),
        "evaluation_step_ms": 60 * 60 * 1000,
        "breakout_consecutive_closes": 2,
        "breakout_duration_ms": 30 * 60 * 1000,
        "train_ratio": Decimal("0.60"),
        "validation_ratio": Decimal("0.20"),
        "test_ratio": Decimal("0.20"),
    }
    values.update(overrides)
    return CalibrationSettings(**values)


def candle(open_time, close, *, interval=15 * 60 * 1000, high=None, low=None):
    value = Decimal(str(close))
    return Candle(
        open_time=open_time,
        close_time=open_time + interval - 1,
        open=value,
        high=Decimal(str(high)) if high is not None else value + Decimal("1"),
        low=Decimal(str(low)) if low is not None else value - Decimal("1"),
        close=value,
        volume=Decimal("10.000000000000000001"),
        quote_volume=value * Decimal("10.000000000000000001"),
        trades=100,
        taker_buy_base_volume=Decimal("5"),
        taker_buy_quote_volume=value * Decimal("5"),
    )


def history_ending_at(evaluation_ms, interval, count=40, offset=0):
    first_open = evaluation_ms - count * interval + 1
    return [
        candle(
            first_open + index * interval,
            Decimal("100") + Decimal((index + offset) % 4) - Decimal("1.5"),
            interval=interval,
        )
        for index in range(count)
    ]


def replay_engine():
    calibration = calibration_settings()
    return HistoricalReplayEngine(
        scanner_settings(),
        GridCandidateIntelligence(IntelligenceSettings()),
        calibration,
    )


def test_historical_cutoff_and_no_future_candle_leakage():
    evaluation_ms = 10_000_000_000
    histories = {
        timeframe: history_ending_at(evaluation_ms, INTERVAL_MS[timeframe])
        for timeframe in TIMEFRAMES
    }
    engine = replay_engine()
    before, errors = engine.assessment_at("BTCUSDT", histories, evaluation_ms)
    assert not errors

    mutated = deepcopy(histories)
    for timeframe in TIMEFRAMES:
        interval = INTERVAL_MS[timeframe]
        mutated[timeframe].append(
            candle(evaluation_ms + 1, "1000000", interval=interval, high="1000001", low="999999")
        )
    after, errors = engine.assessment_at("BTCUSDT", mutated, evaluation_ms)
    assert not errors
    assert before == after
    assert all(
        item.close_time <= evaluation_ms
        for timeframe in TIMEFRAMES
        for item in engine.closed_candles(mutated[timeframe], evaluation_ms)
    )


def test_multi_timeframe_alignment_uses_latest_closed_candle_per_timeframe():
    evaluation_ms = 10_000_000_000
    histories = {}
    expected = {}
    for offset, timeframe in enumerate(TIMEFRAMES):
        histories[timeframe] = history_ending_at(
            evaluation_ms, INTERVAL_MS[timeframe], offset=offset
        )
        expected[timeframe] = histories[timeframe][-1].close
    snapshot, errors = replay_engine().snapshot_at("ETHUSDT", histories, evaluation_ms)
    assert not errors
    assert {
        timeframe: snapshot["timeframes"][timeframe]["current_price"]
        for timeframe in TIMEFRAMES
    } == expected


def test_missing_history_is_reported_without_assessment():
    snapshot, errors = replay_engine().snapshot_at(
        "NEWUSDT", {timeframe: [] for timeframe in TIMEFRAMES}, 1000
    )
    assert snapshot is None
    assert errors == [f"{timeframe}:insufficient_history" for timeframe in TIMEFRAMES]


def test_forward_horizon_slicing_excludes_overlapping_and_late_candles():
    evaluation_ms = 1_000_000
    interval = 15 * 60 * 1000
    candles = [
        candle(evaluation_ms - interval + 1, 100, interval=interval),
        candle(evaluation_ms, 101, interval=interval),
        candle(evaluation_ms + interval + 1, 102, interval=interval),
        candle(evaluation_ms + 2 * interval + 1, 103, interval=interval),
    ]
    selected = ForwardOutcomeEngine.forward_slice(candles, evaluation_ms, 2 * interval)
    assert [item.close for item in selected] == [Decimal("101"), Decimal("102")]


def test_range_durability_breakout_confirmation_mean_reversion_and_excursions():
    evaluation_ms = 1_000_000
    interval = 15 * 60 * 1000
    prices = [95, 100, 105, 111, 112, 100]
    forward = [
        candle(evaluation_ms + 1 + index * interval, price, interval=interval, high=price + 2, low=price - 2)
        for index, price in enumerate(prices)
    ]
    result = ForwardOutcomeEngine(calibration_settings()).evaluate(
        evaluation_ms,
        6 * interval,
        forward,
        {"proposed_lower": "90", "proposed_upper": "110", "current_price": "100"},
    )
    assert result["range_durability"]["inside_close_percentage"] == "66.66666666666666666666666667"
    assert result["range_durability"]["returned_inside_after_violation"] is True
    assert result["breakout"]["classification"] == "UPPER_BREAKOUT"
    assert result["breakout"]["confirmed"] is True
    assert result["mean_reversion"]["midpoint_crossings"] >= 1
    assert result["mean_reversion"]["returns_toward_midpoint"] >= 1
    assert Decimal(result["excursion"]["maximum_favorable_excursion_pct"]) > 0
    assert Decimal(result["excursion"]["maximum_adverse_excursion_pct"]) < 0
    assert Decimal(result["grid_opportunity_proxy"]["total_absolute_price_travel"]) > 0


def test_single_wick_or_single_close_does_not_confirm_breakout():
    evaluation_ms = 1_000_000
    interval = 15 * 60 * 1000
    forward = [
        candle(evaluation_ms + 1, 109, interval=interval, high=120, low=108),
        candle(evaluation_ms + interval + 1, 111, interval=interval, high=112, low=109),
        candle(evaluation_ms + 2 * interval + 1, 105, interval=interval),
    ]
    result = ForwardOutcomeEngine(calibration_settings()).evaluate(
        evaluation_ms,
        3 * interval,
        forward,
        {"proposed_lower": "90", "proposed_upper": "110", "current_price": "100"},
    )
    assert result["range_durability"]["upper_wick_violation_count"] == 2
    assert result["breakout"]["classification"] == "NO_BREAKOUT"


def test_lower_breakout_and_decimal_safe_excursion():
    evaluation_ms = 1_000_000
    interval = 15 * 60 * 1000
    forward = [
        candle(evaluation_ms + 1, "89.999999999999999999", interval=interval),
        candle(evaluation_ms + interval + 1, "88.999999999999999999", interval=interval),
    ]
    result = ForwardOutcomeEngine(calibration_settings()).evaluate(
        evaluation_ms,
        2 * interval,
        forward,
        {
            "proposed_lower": "90.000000000000000000",
            "proposed_upper": "110.000000000000000000",
            "current_price": "100.000000000000000000",
        },
    )
    assert result["breakout"]["classification"] == "LOWER_BREAKOUT"
    assert isinstance(result["excursion"]["maximum_excursion_below_lower_pct"], str)


def test_bucket_assignments_and_spearman():
    assert [grid_score_bucket(value) for value in (44, 45, 59, 60, 74, 75)] == [
        "<45",
        "45-60",
        "45-60",
        "60-75",
        "60-75",
        "75+",
    ]
    assert decile_bucket(0) == "0-10"
    assert decile_bucket(100) == "90-100"
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == Decimal("1")


def test_chronological_split_and_walk_forward_never_shuffle_time():
    observations = [
        {"evaluation_time_ms": index, "horizon": "6h", "grid_score": 50}
        for index in range(10)
    ]
    split = chronological_split(
        observations, Decimal("0.6"), Decimal("0.2"), Decimal("0.2")
    )
    times = {
        name: [item["evaluation_time_ms"] for item in split if item["dataset_split"] == name]
        for name in ("TRAIN", "VALIDATION", "TEST")
    }
    assert max(times["TRAIN"]) < min(times["VALIDATION"])
    assert max(times["VALIDATION"]) < min(times["TEST"])
    folds = walk_forward_folds(
        observations,
        Decimal("0.6"),
        Decimal("0.2"),
        Decimal("0.2"),
        3,
    )
    assert len(folds) == 3
    assert [fold["start_ms"] for fold in folds] == sorted(fold["start_ms"] for fold in folds)
    assert all(fold["start_ms"] <= fold["end_ms"] for fold in folds)


def test_historical_file_cache_round_trip_and_reproducible_outputs(tmp_path):
    cache = HistoricalFileCache(tmp_path / "cache")
    rows = [[0, "1", "2", "0.5", "1", "10", 999, "10", 1, "5", "5", "0"]]
    cache.store("BTCUSDT", "15m", 0, 999, rows)
    assert cache.load("BTCUSDT", "15m", 0, 999) == rows

    manifest = {"run_id": "fixed", "value": Decimal("1.000")}
    sets = {"observations": [{"b": 2, "a": Decimal("1.0")}], "score_buckets": []}
    summary = {"run_id": "fixed", "sample_counts": {"observations_complete": 1}}
    first = tmp_path / "first"
    second = tmp_path / "second"
    ResultWriter(first).write(manifest, sets, summary)
    ResultWriter(second).write(manifest, sets, summary)
    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files
    assert json.loads((first / "manifest.json").read_text())["value"] == "1.000"

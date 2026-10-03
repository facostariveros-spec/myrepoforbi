from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Callable


COMPONENT_NAMES = (
    "range_quality",
    "oscillation_quality",
    "liquidity_quality",
    "volatility_suitability",
    "trend_safety",
    "breakout_safety",
    "data_quality",
)


def grid_score_bucket(value: object) -> str:
    score = _decimal(value) or Decimal(0)
    if score < 45:
        return "<45"
    if score < 60:
        return "45-60"
    if score < 75:
        return "60-75"
    return "75+"


def decile_bucket(value: object) -> str:
    score = max(Decimal(0), min(Decimal(100), _decimal(value) or Decimal(0)))
    lower = min(90, int(score // 10) * 10)
    return f"{lower}-{lower + 10}"


def chronological_split(
    observations: list[dict[str, object]],
    train_ratio: Decimal,
    validation_ratio: Decimal,
    test_ratio: Decimal,
) -> list[dict[str, object]]:
    if train_ratio + validation_ratio + test_ratio != Decimal(1):
        raise ValueError("split ratios must sum to 1")
    timestamps = sorted({int(item["evaluation_time_ms"]) for item in observations})
    if len(timestamps) < 3:
        raise ValueError("at least three evaluation timestamps are required")
    train_end = max(1, int(Decimal(len(timestamps)) * train_ratio))
    validation_end = max(train_end + 1, int(Decimal(len(timestamps)) * (train_ratio + validation_ratio)))
    validation_end = min(validation_end, len(timestamps) - 1)
    train_times = set(timestamps[:train_end])
    validation_times = set(timestamps[train_end:validation_end])
    result = []
    for item in observations:
        timestamp = int(item["evaluation_time_ms"])
        split = "TRAIN" if timestamp in train_times else "VALIDATION" if timestamp in validation_times else "TEST"
        result.append({**item, "dataset_split": split})
    return result


def walk_forward_folds(
    observations: list[dict[str, object]],
    train_ratio: Decimal,
    validation_ratio: Decimal,
    test_ratio: Decimal,
    fold_count: int,
) -> list[dict[str, object]]:
    timestamps = sorted({int(item["evaluation_time_ms"]) for item in observations})
    if fold_count < 1 or len(timestamps) < 3:
        return []
    step = max(1, len(timestamps) // (fold_count + 2))
    window_size = len(timestamps) - (fold_count - 1) * step
    folds = []
    for index in range(fold_count):
        window_times = set(timestamps[index * step : index * step + window_size])
        window = [item for item in observations if int(item["evaluation_time_ms"]) in window_times]
        if len({item["evaluation_time_ms"] for item in window}) < 3:
            continue
        split = chronological_split(window, train_ratio, validation_ratio, test_ratio)
        folds.append(
            {
                "fold": index + 1,
                "start_ms": min(window_times),
                "end_ms": max(window_times),
                "observations": split,
            }
        )
    return folds


def score_bucket_statistics(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    return _group_statistics(observations, lambda item: grid_score_bucket(item["grid_score"]), "score_bucket")


def risk_bucket_statistics(
    observations: list[dict[str, object]], field: str = "breakout_risk"
) -> list[dict[str, object]]:
    return _group_statistics(observations, lambda item: decile_bucket(item[field]), f"{field}_bucket")


def regime_statistics(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = _group_statistics(observations, lambda item: str(item["initial_regime"]), "initial_regime")
    transitions = defaultdict(CounterLike)
    for item in observations:
        key = (str(item["initial_regime"]), str(item.get("future_regime") or "MISSING"), str(item["horizon"]))
        transitions[key].count += 1
    for row in rows:
        initial = row["initial_regime"]
        horizon = row["horizon"]
        row["future_regime_counts"] = {
            future: value.count
            for (source, future, item_horizon), value in sorted(transitions.items())
            if source == initial and item_horizon == horizon
        }
    return rows


def zone_statistics(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    return _group_statistics(observations, lambda item: str(item.get("price_zone") or "MISSING"), "price_zone")


def component_statistics(observations: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    for component in COMPONENT_NAMES:
        for horizon in sorted({str(item["horizon"]) for item in observations}):
            horizon_rows = [item for item in observations if str(item["horizon"]) == horizon]
            present = [item for item in horizon_rows if _decimal(item.get(component)) is not None]
            missing = len(horizon_rows) - len(present)
            correlations = {
                "range_durability_spearman": spearman(
                    [item[component] for item in present],
                    [item.get("range_inside_pct") for item in present],
                ),
                "breakout_spearman": spearman(
                    [item[component] for item in present],
                    [Decimal(1) if item.get("confirmed_breakout") else Decimal(0) for item in present],
                ),
                "opportunity_spearman": spearman(
                    [item[component] for item in present],
                    [item.get("opportunity_count") for item in present],
                ),
            }
            buckets = _group_statistics(
                present, lambda item: decile_bucket(item[component]), "component_bucket"
            )
            for bucket in buckets:
                output.append(
                    {
                        "component": component,
                        "horizon": horizon,
                        "component_present_count": len(present),
                        "missing_data_count": missing,
                        **correlations,
                        **bucket,
                    }
                )
    return output


def spearman(x_values: list[object], y_values: list[object]) -> Decimal | None:
    pairs = [
        (x, y)
        for raw_x, raw_y in zip(x_values, y_values)
        if (x := _decimal(raw_x)) is not None and (y := _decimal(raw_y)) is not None
    ]
    if len(pairs) < 3:
        return None
    x_ranks = _ranks([item[0] for item in pairs])
    y_ranks = _ranks([item[1] for item in pairs])
    x_mean = sum(x_ranks, Decimal(0)) / Decimal(len(x_ranks))
    y_mean = sum(y_ranks, Decimal(0)) / Decimal(len(y_ranks))
    covariance = sum(
        ((x - x_mean) * (y - y_mean) for x, y in zip(x_ranks, y_ranks)), Decimal(0)
    )
    x_variance = sum(((value - x_mean) ** 2 for value in x_ranks), Decimal(0))
    y_variance = sum(((value - y_mean) ** 2 for value in y_ranks), Decimal(0))
    if x_variance == 0 or y_variance == 0:
        return None
    return covariance / (x_variance * y_variance).sqrt()


def fold_statistics(folds: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for fold in folds:
        for split in ("TRAIN", "VALIDATION", "TEST"):
            items = [item for item in fold["observations"] if item["dataset_split"] == split]
            summary = _summarize(items)
            rows.append(
                {
                    "fold": fold["fold"],
                    "start_ms": fold["start_ms"],
                    "end_ms": fold["end_ms"],
                    "dataset_split": split,
                    **summary,
                }
            )
    for split in ("TRAIN", "VALIDATION", "TEST"):
        items = [
            item
            for fold in folds
            for item in fold["observations"]
            if item["dataset_split"] == split
        ]
        rows.append({"fold": "AGGREGATED", "dataset_split": split, **_summarize(items)})
    return rows


def _group_statistics(
    observations: list[dict[str, object]],
    group: Callable[[dict[str, object]], str],
    group_name: str,
) -> list[dict[str, object]]:
    grouped = defaultdict(list)
    for item in observations:
        grouped[(str(item["horizon"]), group(item))].append(item)
    output = []
    for (horizon, bucket), items in sorted(grouped.items()):
        output.append({"horizon": horizon, group_name: bucket, **_summarize(items)})
    return output


def _summarize(items: list[dict[str, object]]) -> dict[str, object]:
    count = len(items)
    if not items:
        return {"sample_count": 0}
    breakouts = [item for item in items if item.get("confirmed_breakout")]
    upper = [item for item in breakouts if item.get("breakout_direction") == "UPPER"]
    lower = [item for item in breakouts if item.get("breakout_direction") == "LOWER"]
    breakout_times = [_decimal(item.get("time_to_breakout_ms")) for item in breakouts]
    return {
        "sample_count": count,
        "mean_range_durability_pct": _mean(items, "range_inside_pct"),
        "confirmed_breakout_rate_pct": Decimal(len(breakouts)) / Decimal(count) * 100,
        "upper_breakout_rate_pct": Decimal(len(upper)) / Decimal(count) * 100,
        "lower_breakout_rate_pct": Decimal(len(lower)) / Decimal(count) * 100,
        "median_time_to_breakout_ms": _median([value for value in breakout_times if value is not None]),
        "range_survival_rate_pct": Decimal(count - len(breakouts)) / Decimal(count) * 100,
        "mean_midpoint_crossings": _mean(items, "midpoint_crossings"),
        "mean_oscillation_count": _mean(items, "oscillation_count"),
        "mean_forward_realized_volatility": _mean(items, "forward_realized_volatility"),
        "mean_grid_opportunity_proxy": _mean(items, "opportunity_count"),
        "mean_adverse_excursion_pct": _mean(items, "adverse_excursion_pct"),
        "mean_favorable_excursion_pct": _mean(items, "favorable_excursion_pct"),
        "mean_returns_toward_midpoint": _mean(items, "returns_toward_midpoint"),
    }


def _mean(items: list[dict[str, object]], field: str) -> Decimal | None:
    values = [_decimal(item.get(field)) for item in items]
    present = [value for value in values if value is not None]
    return None if not present else sum(present, Decimal(0)) / Decimal(len(present))


def _median(values: list[Decimal]) -> Decimal | None:
    return None if not values else Decimal(str(median(values)))


def _ranks(values: list[Decimal]) -> list[Decimal]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [Decimal(0)] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal(2)
        for position in range(index, end):
            ranks[indexed[position][0]] = average_rank
        index = end
    return ranks


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


class CounterLike:
    def __init__(self) -> None:
        self.count = 0

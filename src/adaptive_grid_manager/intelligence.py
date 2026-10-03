from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from .intelligence_config import IntelligenceSettings
from .market_models import safe_api_value
from .scanner_config import TIMEFRAMES


ZERO = Decimal(0)
HUNDRED = Decimal(100)
TIMEFRAME_WEIGHTS = {
    "15m": Decimal("0.15"),
    "1h": Decimal("0.25"),
    "4h": Decimal("0.35"),
    "1d": Decimal("0.25"),
}


def clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = HUNDRED) -> Decimal:
    return min(high, max(low, value))


def linear_score(value: Decimal, poor: Decimal, good: Decimal) -> Decimal:
    if good == poor:
        return HUNDRED if value >= good else ZERO
    return clamp((value - poor) / (good - poor) * HUNDRED)


def inverse_score(value: Decimal, good: Decimal, poor: Decimal) -> Decimal:
    return HUNDRED - linear_score(value, good, poor)


class GridCandidateIntelligence:
    """Pure, deterministic interpretation of scanner MarketSnapshots."""

    def __init__(self, settings: IntelligenceSettings | None = None) -> None:
        self.settings = settings or IntelligenceSettings.from_env()

    def assess(self, snapshot: dict[str, object]) -> dict[str, object]:
        symbol = snapshot.get("symbol")
        timeframes = snapshot.get("timeframes")
        if not isinstance(symbol, str) or not isinstance(timeframes, dict):
            return self._invalid_assessment(snapshot, "malformed_market_snapshot")

        regimes = {
            timeframe: self._classify_timeframe(timeframes.get(timeframe))
            for timeframe in TIMEFRAMES
        }
        combined = self._combine_regimes(regimes)
        risks = self._risks(timeframes, regimes, combined)
        component_details = self._components(snapshot, timeframes, regimes, risks)

        weights_total = sum(self.settings.weights.values())
        contributions = {}
        score = ZERO
        for name, weight in self.settings.weights.items():
            contribution = component_details[name]["score"] * weight / weights_total
            contributions[name] = contribution
            component_details[name]["contribution"] = contribution
            score += contribution
        score = clamp(score)

        hard_rejections = []
        if component_details["data_quality"]["score"] < self.settings.hard_data_quality_min:
            hard_rejections.append("insufficient_data_quality")
        if component_details["liquidity_quality"]["score"] < self.settings.hard_liquidity_min:
            hard_rejections.append("insufficient_liquidity_quality")
        if risks["breakout_risk"]["score"] >= self.settings.hard_breakout_risk:
            hard_rejections.append("breakout_risk_above_hard_limit")

        if hard_rejections or score < self.settings.watch_min:
            state = "REJECT"
        elif score >= self.settings.strong_candidate_min:
            state = "STRONG_CANDIDATE"
        elif score >= self.settings.candidate_min:
            state = "CANDIDATE"
        else:
            state = "WATCH"

        positive, negative = self._explanations(component_details, combined, risks)
        proposed_range = None
        if state in {"CANDIDATE", "STRONG_CANDIDATE"}:
            proposed_range = self._propose_range(timeframes, component_details, combined)
            if proposed_range is None:
                hard_rejections.append("range_proposal_unavailable")
                state = "REJECT"

        assessment = {
            "symbol": symbol,
            "base_asset": snapshot.get("base_asset"),
            "quote_asset": snapshot.get("quote_asset"),
            "candidate_state": state,
            "grid_score": score,
            "score_components": component_details,
            "component_contributions": contributions,
            "regimes": {"timeframes": regimes, **combined},
            "risks": risks,
            "positive_reasons": positive,
            "negative_reasons": negative,
            "rejection_reasons": hard_rejections,
            "proposed_range": proposed_range,
            "prefilter_metrics": snapshot.get("prefilter_metrics"),
        }
        return safe_api_value(assessment)

    def rank(self, assessments: list[dict[str, object]]) -> list[dict[str, object]]:
        accepted = [item for item in assessments if item["candidate_state"] != "REJECT"]

        def number(value: object) -> Decimal:
            return self._decimal(value) or ZERO

        accepted.sort(
            key=lambda item: (
                -number(item["grid_score"]),
                -number(item["score_components"]["range_quality"]["score"]),
                -number(item["score_components"]["oscillation_quality"]["score"]),
                -number(item["score_components"]["liquidity_quality"]["score"]),
                number(item["risks"]["breakout_risk"]["score"]),
                number(item["risks"]["fomo_risk"]["score"]),
                -number(item["score_components"]["data_quality"]["score"]),
                item["symbol"],
            )
        )
        for index, item in enumerate(accepted, start=1):
            item["rank"] = index
            item["ranking_reasons"] = [
                f"grid_score={item['grid_score']}",
                f"range_quality={item['score_components']['range_quality']['score']}",
                f"oscillation_quality={item['score_components']['oscillation_quality']['score']}",
                f"breakout_risk={item['risks']['breakout_risk']['score']}",
                f"fomo_risk={item['risks']['fomo_risk']['score']}",
            ]
        return accepted

    def propose_range_for_observation(
        self, snapshot: dict[str, object], assessment: dict[str, object]
    ) -> dict[str, object] | None:
        """Expose the existing structural range for offline outcome measurement."""
        timeframes = snapshot.get("timeframes")
        components = assessment.get("score_components")
        regimes = assessment.get("regimes")
        if not all(isinstance(value, dict) for value in (timeframes, components, regimes)):
            return None
        result = self._propose_range(timeframes, components, regimes)
        return safe_api_value(result) if result else None

    def _classify_timeframe(self, metrics: object) -> dict[str, object]:
        if not isinstance(metrics, dict) or metrics.get("data_quality_status") != "ok":
            return {
                "regime": "TRANSITIONAL",
                "confidence": ZERO,
                "evidence": {},
                "reasons": ["insufficient_timeframe_data"],
            }
        adx_value = self._metric(metrics, "adx")
        rsi_value = self._metric(metrics, "rsi")
        atr_pct = self._metric(metrics, "atr_percentage")
        volatility = self._metric(metrics, "realized_volatility")
        width = self._metric(metrics, "range_width_percentage")
        crossings = self._metric(metrics, "oscillation_crossings")
        position = self._price_position(metrics)
        volume_ratio = self._nested_metric(metrics, "volume_statistics", "latest_volume_ratio")
        evidence = {
            "adx": adx_value,
            "rsi": rsi_value,
            "atr_percentage": atr_pct,
            "realized_volatility": volatility,
            "range_width_percentage": width,
            "oscillation_crossings": crossings,
            "price_position_percentage": position,
            "latest_volume_ratio": volume_ratio,
        }
        if any(value is None for value in evidence.values()):
            return {
                "regime": "TRANSITIONAL",
                "confidence": Decimal("20"),
                "evidence": evidence,
                "reasons": ["incomplete_indicator_evidence"],
            }

        candidates: list[tuple[str, int, list[str]]] = []
        high_vol_reasons = []
        if atr_pct >= self.settings.atr_pct_high:
            high_vol_reasons.append("atr_percentage_elevated")
        if volatility >= self.settings.realized_volatility_high:
            high_vol_reasons.append("realized_volatility_elevated")
        if width >= self.settings.range_width_max:
            high_vol_reasons.append("rolling_range_wide")
        if len(high_vol_reasons) >= 2:
            candidates.append(("HIGH_VOLATILITY", len(high_vol_reasons), high_vol_reasons))

        low_vol_reasons = []
        if atr_pct <= self.settings.atr_pct_low:
            low_vol_reasons.append("atr_percentage_low")
        if volatility <= self.settings.realized_volatility_low:
            low_vol_reasons.append("realized_volatility_low")
        if width <= self.settings.range_width_target / 2:
            low_vol_reasons.append("rolling_range_narrow")
        if len(low_vol_reasons) >= 2:
            candidates.append(("LOW_VOLATILITY", len(low_vol_reasons), low_vol_reasons))

        ranging_reasons = []
        if adx_value <= self.settings.adx_ranging_max:
            ranging_reasons.append("adx_supports_range")
        if crossings >= self.settings.crossings_target:
            ranging_reasons.append("repeated_range_crossings")
        if Decimal("35") <= rsi_value <= Decimal("65"):
            ranging_reasons.append("rsi_not_directionally_extreme")
        if Decimal("15") <= position <= Decimal("85"):
            ranging_reasons.append("price_inside_range")
        if len(ranging_reasons) >= 3:
            candidates.append(("RANGING", len(ranging_reasons), ranging_reasons))

        up_reasons = []
        if adx_value >= self.settings.adx_trending_min:
            up_reasons.append("adx_confirms_directional_strength")
        if rsi_value >= self.settings.rsi_directional_high:
            up_reasons.append("rsi_has_upward_bias")
        if position >= Decimal("65"):
            up_reasons.append("price_near_upper_range")
        if len(up_reasons) >= 3:
            candidates.append(("TRENDING_UP", len(up_reasons), up_reasons))

        down_reasons = []
        if adx_value >= self.settings.adx_trending_min:
            down_reasons.append("adx_confirms_directional_strength")
        if rsi_value <= self.settings.rsi_directional_low:
            down_reasons.append("rsi_has_downward_bias")
        if position <= Decimal("35"):
            down_reasons.append("price_near_lower_range")
        if len(down_reasons) >= 3:
            candidates.append(("TRENDING_DOWN", len(down_reasons), down_reasons))

        if not candidates:
            regime, strength, reasons = "TRANSITIONAL", 1, ["mixed_or_weak_regime_evidence"]
        else:
            priority = {
                "HIGH_VOLATILITY": 5,
                "TRENDING_UP": 4,
                "TRENDING_DOWN": 4,
                "RANGING": 3,
                "LOW_VOLATILITY": 2,
            }
            regime, strength, reasons = max(
                candidates, key=lambda item: (item[1], priority[item[0]])
            )
        confidence = clamp(Decimal(strength) / Decimal(4) * HUNDRED)
        return {"regime": regime, "confidence": confidence, "evidence": evidence, "reasons": reasons}

    def _combine_regimes(self, regimes: dict[str, dict[str, object]]) -> dict[str, object]:
        votes: Counter[str] = Counter()
        for timeframe, result in regimes.items():
            votes[result["regime"]] += TIMEFRAME_WEIGHTS[timeframe] * result["confidence"] / HUNDRED
        ordered = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        top_regime, top_vote = ordered[0] if ordered else ("TRANSITIONAL", ZERO)
        short = {regimes["15m"]["regime"], regimes["1h"]["regime"]}
        long = {regimes["4h"]["regime"], regimes["1d"]["regime"]}
        conflicts = []
        if ("TRENDING_UP" in short and "TRENDING_DOWN" in long) or (
            "TRENDING_DOWN" in short and "TRENDING_UP" in long
        ):
            conflicts.append("short_long_direction_conflict")
        if "RANGING" in long and short & {"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"}:
            conflicts.append("short_move_against_long_range")
        if "RANGING" in short and long & {"TRENDING_UP", "TRENDING_DOWN"}:
            conflicts.append("short_range_against_long_trend")
        combined_regime = top_regime if top_vote >= Decimal("0.45") and not conflicts else "TRANSITIONAL"
        return {
            "combined_regime": combined_regime,
            "combined_confidence": clamp(top_vote * HUNDRED),
            "regime_conflicts": conflicts,
            "weighted_regime_evidence": dict(votes),
        }

    def _risks(
        self,
        timeframes: dict[str, object],
        regimes: dict[str, dict[str, object]],
        combined: dict[str, object],
    ) -> dict[str, object]:
        short_metrics = [timeframes.get(name, {}) for name in ("15m", "1h")]
        long_metrics = [timeframes.get(name, {}) for name in ("4h", "1d")]
        short_rsi = self._average([self._metric(item, "rsi") for item in short_metrics])
        short_adx = self._average([self._metric(item, "adx") for item in short_metrics])
        positions = [self._price_position(item) for item in short_metrics]
        edge = max((self._edge_risk(value) for value in positions if value is not None), default=ZERO)
        short_atr = self._average([self._metric(item, "atr_percentage") for item in short_metrics])
        long_atr = self._average([self._metric(item, "atr_percentage") for item in long_metrics])
        short_vol = self._average([self._metric(item, "realized_volatility") for item in short_metrics])
        long_vol = self._average([self._metric(item, "realized_volatility") for item in long_metrics])
        volume_ratio = max(
            (
                self._nested_metric(item, "volume_statistics", "latest_volume_ratio") or ZERO
                for item in short_metrics
            ),
            default=ZERO,
        )
        atr_expansion = self._ratio_risk(short_atr, long_atr)
        volatility_expansion = self._ratio_risk(short_vol, long_vol)
        adx_risk = linear_score(short_adx or ZERO, self.settings.adx_ranging_max, Decimal("50"))
        rsi_risk = ZERO
        if short_rsi is not None:
            if short_rsi > Decimal("50"):
                rsi_risk = linear_score(short_rsi, self.settings.rsi_directional_high, Decimal("90"))
            else:
                rsi_risk = linear_score(
                    Decimal("50") - short_rsi,
                    Decimal("50") - self.settings.rsi_directional_low,
                    Decimal("40"),
                )
        volume_risk = linear_score(
            volume_ratio,
            self.settings.abnormal_volume_ratio,
            self.settings.abnormal_volume_ratio * Decimal("2"),
        )
        directional_count = sum(
            regimes[name]["regime"] in {"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"}
            for name in TIMEFRAMES
        )
        directional_risk = Decimal(directional_count) / Decimal(4) * HUNDRED
        conflict_risk = HUNDRED if combined["regime_conflicts"] else ZERO

        breakout = clamp(
            edge * Decimal("0.25")
            + adx_risk * Decimal("0.15")
            + atr_expansion * Decimal("0.15")
            + volatility_expansion * Decimal("0.10")
            + volume_risk * Decimal("0.10")
            + directional_risk * Decimal("0.15")
            + conflict_risk * Decimal("0.10")
        )
        fomo = clamp(
            rsi_risk * Decimal("0.20")
            + adx_risk * Decimal("0.20")
            + edge * Decimal("0.15")
            + max(atr_expansion, volatility_expansion) * Decimal("0.15")
            + volume_risk * Decimal("0.15")
            + max(directional_risk, conflict_risk) * Decimal("0.15")
        )
        evidence = {
            "short_rsi": short_rsi,
            "rsi_extreme_risk": rsi_risk,
            "short_adx": short_adx,
            "edge_risk": edge,
            "atr_expansion_risk": atr_expansion,
            "volatility_expansion_risk": volatility_expansion,
            "abnormal_volume_risk": volume_risk,
            "directional_persistence_risk": directional_risk,
            "timeframe_conflict_risk": conflict_risk,
        }
        risk_evidence = {name: value for name, value in evidence.items() if name != "short_rsi"}
        breakout_reasons = [
            name
            for name, value in risk_evidence.items()
            if name != "rsi_extreme_risk" and value is not None and value >= Decimal("50")
        ]
        fomo_reasons = [
            name for name, value in risk_evidence.items() if value is not None and value >= Decimal("50")
        ]
        return {
            "breakout_risk": {"score": breakout, "reasons": breakout_reasons, "evidence": evidence},
            "fomo_risk": {"score": fomo, "reasons": fomo_reasons, "evidence": evidence},
        }

    def _components(
        self,
        snapshot: dict[str, object],
        timeframes: dict[str, object],
        regimes: dict[str, dict[str, object]],
        risks: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        metrics = [timeframes.get(name, {}) for name in TIMEFRAMES]
        widths = [self._metric(item, "range_width_percentage") for item in metrics]
        positions = [self._price_position(item) for item in metrics]
        range_scores = [
            self._target_score(value, self.settings.range_width_target, self.settings.range_width_max)
            for value in widths
            if value is not None
        ]
        interior_scores = [
            HUNDRED - self._edge_risk(value) for value in positions if value is not None
        ]
        ranging_share = Decimal(
            sum(regimes[name]["regime"] == "RANGING" for name in TIMEFRAMES)
        ) / Decimal(4) * HUNDRED
        range_quality = self._average(range_scores + interior_scores + [ranging_share]) or ZERO

        crossings = [self._metric(item, "oscillation_crossings") for item in metrics]
        crossing_scores = [
            linear_score(value, ZERO, self.settings.crossings_target)
            for value in crossings
            if value is not None
        ]
        oscillation = self._average(crossing_scores) or ZERO

        prefilter = snapshot.get("prefilter_metrics")
        liquidity, liquidity_raw, liquidity_reasons = self._liquidity(prefilter)

        atr_values = [self._metric(item, "atr_percentage") for item in metrics]
        volatility_scores = [
            self._target_score(value, self.settings.atr_pct_target, self.settings.atr_pct_high)
            for value in atr_values
            if value is not None
        ]
        volatility_suitability = self._average(volatility_scores) or ZERO

        adx_values = [self._metric(item, "adx") for item in metrics]
        trend_scores = [
            inverse_score(value, self.settings.adx_ranging_max, Decimal("55"))
            for value in adx_values
            if value is not None
        ]
        trend_safety = self._average(trend_scores) or ZERO
        breakout_safety = HUNDRED - self._decimal(risks["breakout_risk"]["score"])

        quality_values = [
            HUNDRED if isinstance(item, dict) and item.get("data_quality_status") == "ok" else ZERO
            for item in metrics
        ]
        candle_values = [self._metric(item, "candle_count") for item in metrics]
        candle_consistency = HUNDRED
        if candle_values and all(value is not None for value in candle_values):
            maximum = max(candle_values)
            minimum = min(candle_values)
            candle_consistency = HUNDRED if maximum == 0 else minimum / maximum * HUNDRED
        data_quality = (self._average(quality_values) or ZERO) * Decimal("0.8") + candle_consistency * Decimal("0.2")

        return {
            "range_quality": self._component(range_quality, {"range_widths": widths, "price_positions": positions, "ranging_timeframe_share": ranging_share}, ["multi_timeframe_width_position_and_regime"]),
            "oscillation_quality": self._component(oscillation, {"crossings": crossings}, ["range_midpoint_crossing_frequency"]),
            "liquidity_quality": self._component(liquidity, liquidity_raw, liquidity_reasons),
            "volatility_suitability": self._component(volatility_suitability, {"atr_percentages": atr_values}, ["distance_from_provisional_atr_target"]),
            "trend_safety": self._component(trend_safety, {"adx_values": adx_values}, ["inverse_multi_timeframe_directional_strength"]),
            "breakout_safety": self._component(breakout_safety, {"breakout_risk": risks["breakout_risk"]["score"]}, ["inverse_composite_breakout_risk"]),
            "data_quality": self._component(data_quality, {"timeframe_quality": quality_values, "candle_counts": candle_values}, ["timeframe_completeness_and_candle_consistency"]),
        }

    def _liquidity(self, metrics: object) -> tuple[Decimal, dict[str, object], list[str]]:
        if not isinstance(metrics, dict):
            return ZERO, {"depth_measurement": "not_measured"}, ["missing_prefilter_liquidity"]
        quote_volume = self._metric(metrics, "quote_volume_24h") or ZERO
        trades = self._metric(metrics, "trades_24h") or ZERO
        spread = self._metric(metrics, "spread_percentage")
        book_proxy = self._metric(metrics, "book_liquidity_quote_proxy") or ZERO
        # Log-like saturation without float math: each doubling adds a fixed step.
        volume_score = self._doubling_score(
            quote_volume, self.settings.liquidity_quote_volume_reference
        )
        trades_score = self._doubling_score(trades, self.settings.liquidity_trades_reference)
        book_score = self._doubling_score(book_proxy, self.settings.liquidity_book_reference)
        spread_score = ZERO if spread is None else inverse_score(
            spread, self.settings.liquidity_spread_good, self.settings.liquidity_spread_poor
        )
        preliminary = self._average([volume_score, trades_score, book_score, spread_score]) or ZERO
        score = min(self.settings.liquidity_score_cap_without_depth, preliminary)
        reasons = ["full_order_book_depth_not_measured"]
        return score, {
            "cheap_prefilter": metrics,
            "execution_quality_source": "top_of_book_proxy",
            "depth_measurement": "not_measured",
        }, reasons

    def _propose_range(
        self,
        timeframes: dict[str, object],
        components: dict[str, dict[str, object]],
        combined: dict[str, object],
    ) -> dict[str, object] | None:
        weights = {"1h": Decimal("0.20"), "4h": Decimal("0.45"), "1d": Decimal("0.35")}
        lows = []
        highs = []
        atrs = []
        for timeframe, weight in weights.items():
            item = timeframes.get(timeframe)
            low = self._metric(item, "rolling_low")
            high = self._metric(item, "rolling_high")
            atr_value = self._metric(item, "atr")
            if low is None or high is None or atr_value is None:
                return None
            lows.append(low * weight)
            highs.append(high * weight)
            atrs.append(atr_value * weight)
        support = sum(lows, ZERO)
        resistance = sum(highs, ZERO)
        atr_buffer = sum(atrs, ZERO) * self.settings.range_atr_buffer
        lower = max(ZERO, support - atr_buffer)
        upper = resistance + atr_buffer
        current = self._metric(timeframes.get("15m"), "current_price")
        if current is None or upper <= lower:
            return None
        position = (current - lower) / (upper - lower) * HUNDRED
        if position < 0 or position > 100:
            zone = "OUTSIDE_RANGE"
        elif position <= Decimal("33.333333"):
            zone = "LOWER_ZONE"
        elif position <= Decimal("66.666667"):
            zone = "MID_ZONE"
        else:
            zone = "UPPER_ZONE"
        confidence = self._average(
            [
                self._decimal(components["range_quality"]["score"]),
                self._decimal(components["trend_safety"]["score"]),
                self._decimal(components["breakout_safety"]["score"]),
                self._decimal(components["data_quality"]["score"]),
            ]
        )
        return {
            "proposed_lower": lower,
            "proposed_upper": upper,
            "current_price": current,
            "range_width_pct": (upper - lower) / ((upper + lower) / 2) * HUNDRED,
            "price_position_pct": position,
            "price_zone": zone,
            "range_confidence": confidence,
            "range_reasons": [
                "weighted_1h_4h_1d_rolling_support_resistance",
                "atr_buffer_applied",
                f"combined_regime={combined['combined_regime']}",
            ],
        }

    def _invalid_assessment(self, snapshot: dict[str, object], reason: str) -> dict[str, object]:
        zero_components = {
            name: {"score": "0", "raw_metrics": {}, "reasons": [reason], "contribution": "0"}
            for name in self.settings.weights
        }
        return {
            "symbol": snapshot.get("symbol"),
            "base_asset": snapshot.get("base_asset"),
            "quote_asset": snapshot.get("quote_asset"),
            "candidate_state": "REJECT",
            "grid_score": "0",
            "score_components": zero_components,
            "component_contributions": {name: "0" for name in self.settings.weights},
            "regimes": {"timeframes": {}, "combined_regime": "TRANSITIONAL", "combined_confidence": "0", "regime_conflicts": [reason]},
            "risks": {"breakout_risk": {"score": "100", "reasons": [reason]}, "fomo_risk": {"score": "100", "reasons": [reason]}},
            "positive_reasons": [],
            "negative_reasons": [reason],
            "rejection_reasons": [reason],
            "proposed_range": None,
            "prefilter_metrics": snapshot.get("prefilter_metrics"),
        }

    @staticmethod
    def _component(score: Decimal, raw: dict[str, object], reasons: list[str] | None = None) -> dict[str, object]:
        return {"score": clamp(score), "raw_metrics": raw, "reasons": reasons or []}

    def _explanations(self, components, combined, risks):
        positive = [f"{name}_strong" for name, value in components.items() if value["score"] >= 70]
        negative = [f"{name}_weak" for name, value in components.items() if value["score"] < 45]
        if combined["combined_regime"] == "RANGING":
            positive.append("multi_timeframe_range_evidence")
        if combined["regime_conflicts"]:
            negative.extend(combined["regime_conflicts"])
        if risks["breakout_risk"]["score"] >= 60:
            negative.append("elevated_breakout_risk")
        if risks["fomo_risk"]["score"] >= 60:
            negative.append("elevated_fomo_risk")
        return positive, negative

    def _target_score(self, value: Decimal, target: Decimal, maximum: Decimal) -> Decimal:
        if value <= target:
            return linear_score(value, ZERO, target)
        return inverse_score(value, target, maximum)

    def _edge_risk(self, position: Decimal | None) -> Decimal:
        if position is None:
            return HUNDRED
        distance = abs(position - Decimal("50"))
        return linear_score(distance, Decimal("25"), Decimal("55"))

    def _ratio_risk(self, short: Decimal | None, long: Decimal | None) -> Decimal:
        if short is None or long is None or long <= 0:
            return ZERO
        return linear_score(
            short / long,
            self.settings.expansion_ratio,
            self.settings.expansion_ratio * Decimal("2"),
        )

    @staticmethod
    def _doubling_score(value: Decimal, baseline: Decimal) -> Decimal:
        if baseline <= 0 or value <= 0:
            return ZERO
        score = Decimal("50")
        ratio = value / baseline
        while ratio >= 2 and score < 100:
            score += 15
            ratio /= 2
        if ratio < 1:
            score *= ratio
        return clamp(score)

    @classmethod
    def _metric(cls, metrics: object, name: str) -> Decimal | None:
        return cls._decimal(metrics.get(name)) if isinstance(metrics, dict) else None

    @classmethod
    def _nested_metric(cls, metrics: object, group: str, name: str) -> Decimal | None:
        if not isinstance(metrics, dict) or not isinstance(metrics.get(group), dict):
            return None
        return cls._decimal(metrics[group].get(name))

    @classmethod
    def _price_position(cls, metrics: object) -> Decimal | None:
        current = cls._metric(metrics, "current_price")
        low = cls._metric(metrics, "rolling_low")
        high = cls._metric(metrics, "rolling_high")
        if current is None or low is None or high is None or high <= low:
            return None
        return (current - low) / (high - low) * HUNDRED

    @staticmethod
    def _average(values: list[Decimal | None]) -> Decimal | None:
        present = [value for value in values if value is not None]
        return None if not present else sum(present, ZERO) / Decimal(len(present))

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None


class CandidateService:
    def __init__(self, scanner: Any, intelligence: GridCandidateIntelligence) -> None:
        self._scanner = scanner
        self._intelligence = intelligence

    async def candidates(self, limit: int | None = None) -> dict[str, object]:
        scan = await self._scanner.scan(limit=None)
        assessments = [self._intelligence.assess(snapshot) for snapshot in scan["snapshots"]]
        ranked = self._intelligence.rank(assessments)
        rejected = [item for item in assessments if item["candidate_state"] == "REJECT"]
        rejected.extend(
            {
                "symbol": item.get("symbol"),
                "candidate_state": "REJECT",
                "rejection_reasons": item.get("reasons", []),
                "rejection_stage": item.get("stage"),
            }
            for item in scan["rejections"]
        )
        if limit is not None:
            ranked = ranked[:limit]
        states = Counter(item["candidate_state"] for item in assessments)
        return {
            "timestamp": scan["scan_timestamp"],
            "analyzed_count": scan["analyzed_count"],
            "ranked_candidates": ranked,
            "rejected_candidates": rejected,
            "diagnostics": {
                "scanner": scan["diagnostics"],
                "assessed_count": len(assessments),
                "candidate_states": dict(sorted(states.items())),
                "scanner_rejection_count": len(scan["rejections"]),
            },
        }

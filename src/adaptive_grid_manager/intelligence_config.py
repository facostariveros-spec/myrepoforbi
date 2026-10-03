from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class IntelligenceSettings:
    adx_ranging_max: Decimal = Decimal("22")
    adx_trending_min: Decimal = Decimal("28")
    rsi_directional_high: Decimal = Decimal("58")
    rsi_directional_low: Decimal = Decimal("42")
    rsi_extreme_high: Decimal = Decimal("72")
    rsi_extreme_low: Decimal = Decimal("28")
    atr_pct_low: Decimal = Decimal("0.25")
    atr_pct_target: Decimal = Decimal("1.25")
    atr_pct_high: Decimal = Decimal("3.5")
    realized_volatility_low: Decimal = Decimal("0.002")
    realized_volatility_high: Decimal = Decimal("0.035")
    range_width_target: Decimal = Decimal("8")
    range_width_max: Decimal = Decimal("30")
    crossings_target: Decimal = Decimal("6")
    abnormal_volume_ratio: Decimal = Decimal("2")
    expansion_ratio: Decimal = Decimal("1.6")
    strong_candidate_min: Decimal = Decimal("75")
    candidate_min: Decimal = Decimal("60")
    watch_min: Decimal = Decimal("45")
    hard_liquidity_min: Decimal = Decimal("25")
    hard_data_quality_min: Decimal = Decimal("70")
    hard_breakout_risk: Decimal = Decimal("85")
    range_atr_buffer: Decimal = Decimal("0.5")
    liquidity_quote_volume_reference: Decimal = Decimal("1000000")
    liquidity_trades_reference: Decimal = Decimal("1000")
    liquidity_book_reference: Decimal = Decimal("1000")
    liquidity_spread_good: Decimal = Decimal("0.05")
    liquidity_spread_poor: Decimal = Decimal("0.50")
    liquidity_score_cap_without_depth: Decimal = Decimal("85")
    weights: dict[str, Decimal] = field(
        default_factory=lambda: {
            "range_quality": Decimal("0.22"),
            "oscillation_quality": Decimal("0.18"),
            "liquidity_quality": Decimal("0.15"),
            "volatility_suitability": Decimal("0.12"),
            "trend_safety": Decimal("0.15"),
            "breakout_safety": Decimal("0.13"),
            "data_quality": Decimal("0.05"),
        }
    )

    @classmethod
    def from_env(cls) -> "IntelligenceSettings":
        base = cls()
        weight_names = tuple(base.weights)
        weights = {
            name: _decimal_env(
                f"GRID_WEIGHT_{name.upper()}", format(base.weights[name], "f")
            )
            for name in weight_names
        }
        if any(value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("Grid Score weights must be non-negative with a positive sum")
        return cls(
            adx_ranging_max=_decimal_env("REGIME_ADX_RANGING_MAX", "22"),
            adx_trending_min=_decimal_env("REGIME_ADX_TRENDING_MIN", "28"),
            rsi_directional_high=_decimal_env("REGIME_RSI_DIRECTIONAL_HIGH", "58"),
            rsi_directional_low=_decimal_env("REGIME_RSI_DIRECTIONAL_LOW", "42"),
            rsi_extreme_high=_decimal_env("RISK_RSI_EXTREME_HIGH", "72"),
            rsi_extreme_low=_decimal_env("RISK_RSI_EXTREME_LOW", "28"),
            atr_pct_low=_decimal_env("REGIME_ATR_PCT_LOW", "0.25"),
            atr_pct_target=_decimal_env("GRID_ATR_PCT_TARGET", "1.25"),
            atr_pct_high=_decimal_env("REGIME_ATR_PCT_HIGH", "3.5"),
            realized_volatility_low=_decimal_env("REGIME_REALIZED_VOL_LOW", "0.002"),
            realized_volatility_high=_decimal_env("REGIME_REALIZED_VOL_HIGH", "0.035"),
            range_width_target=_decimal_env("GRID_RANGE_WIDTH_TARGET", "8"),
            range_width_max=_decimal_env("GRID_RANGE_WIDTH_MAX", "30"),
            crossings_target=_decimal_env("GRID_CROSSINGS_TARGET", "6"),
            abnormal_volume_ratio=_decimal_env("RISK_ABNORMAL_VOLUME_RATIO", "2"),
            expansion_ratio=_decimal_env("RISK_EXPANSION_RATIO", "1.6"),
            strong_candidate_min=_decimal_env("GRID_STRONG_CANDIDATE_MIN", "75"),
            candidate_min=_decimal_env("GRID_CANDIDATE_MIN", "60"),
            watch_min=_decimal_env("GRID_WATCH_MIN", "45"),
            hard_liquidity_min=_decimal_env("GRID_HARD_LIQUIDITY_MIN", "25"),
            hard_data_quality_min=_decimal_env("GRID_HARD_DATA_QUALITY_MIN", "70"),
            hard_breakout_risk=_decimal_env("GRID_HARD_BREAKOUT_RISK", "85"),
            range_atr_buffer=_decimal_env("RANGE_ATR_BUFFER", "0.5"),
            liquidity_quote_volume_reference=_decimal_env(
                "LIQUIDITY_QUOTE_VOLUME_REFERENCE", "1000000"
            ),
            liquidity_trades_reference=_decimal_env("LIQUIDITY_TRADES_REFERENCE", "1000"),
            liquidity_book_reference=_decimal_env("LIQUIDITY_BOOK_REFERENCE", "1000"),
            liquidity_spread_good=_decimal_env("LIQUIDITY_SPREAD_GOOD_PCT", "0.05"),
            liquidity_spread_poor=_decimal_env("LIQUIDITY_SPREAD_POOR_PCT", "0.50"),
            liquidity_score_cap_without_depth=_decimal_env(
                "LIQUIDITY_SCORE_CAP_WITHOUT_DEPTH", "85"
            ),
            weights=weights,
        )

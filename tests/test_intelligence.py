from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal
import json

from adaptive_grid_manager.app import ConnectivityApp
from adaptive_grid_manager.config import Settings
from adaptive_grid_manager.intelligence import CandidateService, GridCandidateIntelligence
from adaptive_grid_manager.intelligence_config import IntelligenceSettings


TIMEFRAMES = ("15m", "1h", "4h", "1d")


def run(coro):
    return asyncio.run(coro)


def metrics(
    *,
    price="100",
    low="90",
    high="110",
    atr="1.2",
    atr_pct="1.2",
    rsi="50",
    adx="15",
    volatility="0.01",
    width="22.2222",
    crossings=8,
    volume_ratio="1.2",
    quality="ok",
    candles=200,
):
    return {
        "current_price": price,
        "atr": atr,
        "atr_percentage": atr_pct,
        "rsi": rsi,
        "adx": adx,
        "realized_volatility": volatility,
        "rolling_high": high,
        "rolling_low": low,
        "range_width_percentage": width,
        "volume_statistics": {
            "average_volume": "1000",
            "median_volume": "900",
            "average_quote_volume": "100000",
            "latest_volume_ratio": volume_ratio,
        },
        "oscillation_crossings": crossings,
        "candle_count": candles,
        "data_quality_status": quality,
    }


def snapshot(symbol="RANGEUSDT", timeframe_metrics=None, *, liquidity="normal"):
    timeframe_metrics = timeframe_metrics or {name: metrics() for name in TIMEFRAMES}
    if liquidity == "low":
        prefilter = {
            "quote_volume_24h": "10",
            "trades_24h": 1,
            "bid": "99",
            "ask": "100",
            "spread_percentage": "0.5",
            "book_liquidity_quote_proxy": "10",
        }
    else:
        prefilter = {
            "quote_volume_24h": "8000000",
            "trades_24h": 8000,
            "bid": "99.95",
            "ask": "100.05",
            "spread_percentage": "0.1",
            "book_liquidity_quote_proxy": "100000",
        }
    return {
        "symbol": symbol,
        "base_asset": symbol.removesuffix("USDT"),
        "quote_asset": "USDT",
        "prefilter_metrics": prefilter,
        "prefilter_status": "passed",
        "rejection_reasons": [],
        "timeframes": timeframe_metrics,
    }


def trend(direction="up"):
    if direction == "up":
        return metrics(price="109", rsi="78", adx="45", atr="2.5", atr_pct="2.5", volatility="0.025", crossings=1, volume_ratio="2.5")
    return metrics(price="91", rsi="22", adx="45", atr="2.5", atr_pct="2.5", volatility="0.025", crossings=1, volume_ratio="2.5")


def test_clear_ranging_market_is_explainable_candidate():
    result = GridCandidateIntelligence(IntelligenceSettings()).assess(snapshot())
    assert result["regimes"]["combined_regime"] == "RANGING"
    assert result["candidate_state"] in {"CANDIDATE", "STRONG_CANDIDATE"}
    assert "multi_timeframe_range_evidence" in result["positive_reasons"]
    assert result["proposed_range"] is not None


def test_strong_uptrend_and_downtrend_are_directional_and_grid_unsafe():
    engine = GridCandidateIntelligence(IntelligenceSettings())
    up = engine.assess(snapshot("UPUSDT", {name: trend("up") for name in TIMEFRAMES}))
    down = engine.assess(snapshot("DOWNUSDT", {name: trend("down") for name in TIMEFRAMES}))
    assert up["regimes"]["combined_regime"] == "TRENDING_UP"
    assert down["regimes"]["combined_regime"] == "TRENDING_DOWN"
    assert Decimal(up["score_components"]["trend_safety"]["score"]) < 45
    assert Decimal(down["score_components"]["trend_safety"]["score"]) < 45


def test_generic_breakout_pattern_has_high_breakout_and_fomo_risk():
    frames = {name: metrics() for name in TIMEFRAMES}
    for name in ("15m", "1h"):
        frames[name] = metrics(
            price="116",
            rsi="86",
            adx="52",
            atr="5",
            atr_pct="5",
            volatility="0.06",
            width="40",
            crossings=0,
            volume_ratio="4",
        )
    result = GridCandidateIntelligence(IntelligenceSettings()).assess(snapshot("FASTUSDT", frames))
    assert Decimal(result["risks"]["breakout_risk"]["score"]) >= 80
    assert Decimal(result["risks"]["fomo_risk"]["score"]) >= 70
    assert result["candidate_state"] == "REJECT"
    assert "short_move_against_long_range" in result["regimes"]["regime_conflicts"]


def test_false_breakout_like_and_overbought_ranging_are_not_rejected_on_rsi_alone():
    frames = {name: metrics() for name in TIMEFRAMES}
    frames["15m"] = metrics(price="111", rsi="76", adx="18", atr_pct="1.4", volatility="0.012", crossings=8)
    result = GridCandidateIntelligence(IntelligenceSettings()).assess(snapshot("FALSEMOVEUSDT", frames))
    assert result["candidate_state"] != "REJECT"
    assert Decimal(result["risks"]["breakout_risk"]["score"]) < 60
    assert Decimal(result["risks"]["fomo_risk"]["score"]) < 60


def test_high_and_low_volatility_regimes():
    engine = GridCandidateIntelligence(IntelligenceSettings())
    high = metrics(atr="5", atr_pct="5", volatility="0.06", width="40", rsi="50", adx="20", crossings=2)
    low = metrics(atr="0.1", atr_pct="0.1", volatility="0.001", width="3", rsi="70", adx="15", crossings=1)
    high_result = engine.assess(snapshot("HIGHVOLUSDT", {name: high for name in TIMEFRAMES}))
    low_result = engine.assess(snapshot("LOWVOLUSDT", {name: low for name in TIMEFRAMES}))
    assert high_result["regimes"]["combined_regime"] == "HIGH_VOLATILITY"
    assert low_result["regimes"]["combined_regime"] == "LOW_VOLATILITY"


def test_conflicting_timeframes_become_transitional_with_explicit_conflict():
    frames = {"15m": trend("up"), "1h": trend("up"), "4h": trend("down"), "1d": trend("down")}
    result = GridCandidateIntelligence(IntelligenceSettings()).assess(snapshot("CONFLICTUSDT", frames))
    assert result["regimes"]["combined_regime"] == "TRANSITIONAL"
    assert "short_long_direction_conflict" in result["regimes"]["regime_conflicts"]


def test_low_liquidity_and_insufficient_data_are_hard_rejections():
    engine = GridCandidateIntelligence(IntelligenceSettings())
    low_liquidity = engine.assess(snapshot("THINUSDT", liquidity="low"))
    bad_frames = {name: metrics(quality="insufficient_history", candles=5) for name in TIMEFRAMES}
    insufficient = engine.assess(snapshot("NEWUSDT", bad_frames))
    assert low_liquidity["candidate_state"] == "REJECT"
    assert "insufficient_liquidity_quality" in low_liquidity["rejection_reasons"]
    assert insufficient["candidate_state"] == "REJECT"
    assert "insufficient_data_quality" in insufficient["rejection_reasons"]


def test_all_scores_and_risks_are_bounded_and_output_is_finite_json():
    result = GridCandidateIntelligence(IntelligenceSettings()).assess(snapshot())
    assert Decimal("0") <= Decimal(result["grid_score"]) <= Decimal("100")
    for component in result["score_components"].values():
        assert Decimal("0") <= Decimal(component["score"]) <= Decimal("100")
    for risk in result["risks"].values():
        assert Decimal("0") <= Decimal(risk["score"]) <= Decimal("100")
    json.dumps(result, allow_nan=False)


def test_ranking_is_deterministic_and_uses_safety_tie_breakers():
    engine = GridCandidateIntelligence(IntelligenceSettings())
    first = engine.assess(snapshot("ZZZUSDT"))
    second = engine.assess(snapshot("AAAUSDT"))
    ranked_once = [item["symbol"] for item in engine.rank([first, second])]
    ranked_twice = [item["symbol"] for item in engine.rank([deepcopy(second), deepcopy(first)])]
    assert ranked_once == ["AAAUSDT", "ZZZUSDT"]
    assert ranked_twice == ranked_once


def test_range_calculation_and_price_zones():
    engine = GridCandidateIntelligence(IntelligenceSettings())
    middle = engine.assess(snapshot("MIDUSDT"))
    assert Decimal(middle["proposed_range"]["proposed_lower"]) < Decimal("100")
    assert Decimal(middle["proposed_range"]["proposed_upper"]) > Decimal("100")
    assert middle["proposed_range"]["price_zone"] == "MID_ZONE"

    lower_frames = {name: metrics(price="91") for name in TIMEFRAMES}
    lower = engine.assess(snapshot("LOWERUSDT", lower_frames))
    assert lower["proposed_range"]["price_zone"] == "LOWER_ZONE"

    outside_frames = {name: metrics() for name in TIMEFRAMES}
    outside_frames["15m"] = metrics(price="130", low="120", high="140")
    outside = engine.assess(snapshot("OUTSIDEUSDT", outside_frames))
    assert outside["candidate_state"] in {"CANDIDATE", "STRONG_CANDIDATE"}
    assert outside["proposed_range"]["price_zone"] == "OUTSIDE_RANGE"


def test_candidate_service_preserves_scanner_diagnostics_and_limit():
    class StubScanner:
        async def scan(self, limit=None):
            assert limit is None
            return {
                "scan_timestamp": "2026-01-01T00:00:00+00:00",
                "analyzed_count": 2,
                "snapshots": [snapshot("BBBUsdt".upper()), snapshot("AAAUSDT")],
                "rejections": [{"symbol": "BADUSDT", "stage": "universe", "reasons": ["inactive_symbol"]}],
                "diagnostics": {"binance_requests_made": 11},
            }

    response = run(CandidateService(StubScanner(), GridCandidateIntelligence(IntelligenceSettings())).candidates(limit=1))
    assert len(response["ranked_candidates"]) == 1
    assert response["analyzed_count"] == 2
    assert response["diagnostics"]["scanner"]["binance_requests_made"] == 11
    assert response["rejected_candidates"][-1]["symbol"] == "BADUSDT"


def test_candidate_api_endpoint_and_limit():
    class StubCandidateService:
        async def candidates(self, limit=None):
            return {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "analyzed_count": 3,
                "ranked_candidates": [{"symbol": "BTCUSDT"}][:limit],
                "rejected_candidates": [],
                "diagnostics": {"requested_limit": limit},
            }

    cfg = Settings(
        endpoints=(("test", "https://binance.test"),),
        authenticated_endpoint="https://binance.test",
        api_key=None,
        api_secret=None,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_retries=0,
        backoff_base_seconds=0.1,
        retry_after_cap_seconds=1,
        recv_window_ms=5000,
    )
    app = ConnectivityApp(cfg, object(), None, StubCandidateService())

    async def request():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "GET",
                "path": "/scanner/candidates",
                "query_string": b"limit=1",
            },
            receive,
            send,
        )
        return sent[0]["status"], json.loads(sent[1]["body"])

    status, body = run(request())
    assert status == 200
    assert body["diagnostics"]["requested_limit"] == 1

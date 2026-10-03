from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from adaptive_grid_manager.app import ConnectivityApp
from adaptive_grid_manager.binance import BinanceClient
from adaptive_grid_manager.config import Settings
from adaptive_grid_manager.transport import SafeGetTransport


def run(coro):
    return asyncio.run(coro)


def settings(*, enabled=True, key="test-key", secret="test-secret", retries=3):
    return Settings(
        endpoints=(("test", "https://binance.test"),),
        authenticated_endpoint="https://binance.test",
        api_key=key,
        api_secret=secret,
        connect_timeout_seconds=0.1,
        read_timeout_seconds=0.1,
        max_retries=retries,
        backoff_base_seconds=0.001,
        retry_after_cap_seconds=0.01,
        recv_window_ms=5000,
        test_order_enabled=enabled,
    )


def exchange_info(
    *,
    symbol="BTCUSDT",
    quote="USDT",
    spot=True,
    price_filter=None,
    lot_filter=None,
    notional_filter=None,
):
    filters = [
        price_filter
        or {
            "filterType": "PRICE_FILTER",
            "minPrice": "1.00",
            "maxPrice": "1000000.00",
            "tickSize": "0.10",
        },
        lot_filter
        or {
            "filterType": "LOT_SIZE",
            "minQty": "0.001",
            "maxQty": "100.000",
            "stepSize": "0.001",
        },
        notional_filter or {"filterType": "MIN_NOTIONAL", "minNotional": "10.00"},
    ]
    return {
        "symbols": [
            {
                "symbol": symbol,
                "status": "TRADING",
                "quoteAsset": quote,
                "isSpotTradingAllowed": spot,
                "permissions": ["SPOT"] if spot else [],
                "filters": filters,
            }
        ]
    }


def make_client(handler, *, cfg=None):
    raw = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_sleep(_delay):
        return None

    active_settings = cfg or settings()
    tx = SafeGetTransport(
        connect_timeout_seconds=active_settings.connect_timeout_seconds,
        read_timeout_seconds=active_settings.read_timeout_seconds,
        max_retries=active_settings.max_retries,
        backoff_base_seconds=active_settings.backoff_base_seconds,
        retry_after_cap_seconds=active_settings.retry_after_cap_seconds,
        client=raw,
        sleep=no_sleep,
    )
    return BinanceClient(active_settings, tx, clock_ms=lambda: 123456789), raw


async def call_order_endpoint(app, payload):
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    async def send(message):
        sent.append(message)

    await app(
        {"type": "http", "method": "POST", "path": "/connectivity/binance/order-test"},
        receive,
        send,
    )
    return sent[0]["status"], json.loads(sent[1]["body"])


def valid_payload(side="BUY"):
    return {"symbol": "BTCUSDT", "side": side, "quantity": "0.010", "price": "20000.00"}


def test_feature_disabled_does_not_contact_binance():
    def handler(request):
        raise AssertionError("Binance must not be contacted")

    cfg = settings(enabled=False)
    client, raw = make_client(handler, cfg=cfg)
    status, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    assert status == 403
    assert body["success"] is False
    assert body["error_category"] == "feature_disabled"


def test_feature_flag_is_enabled_only_by_explicit_true(monkeypatch):
    monkeypatch.setenv("BINANCE_TEST_ORDER_ENABLED", "true")
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert Settings.from_env().test_order_enabled is True
    monkeypatch.setenv("BINANCE_TEST_ORDER_ENABLED", "1")
    assert Settings.from_env().test_order_enabled is False


def test_enabled_without_credentials_does_not_contact_binance():
    def handler(request):
        raise AssertionError("Binance must not be contacted")

    cfg = settings(key=None, secret=None)
    client, raw = make_client(handler, cfg=cfg)
    _, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    assert body["success"] is False
    assert body["error_category"] == "credentials_absent"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_valid_mocked_limit_order(side):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=exchange_info())
        return httpx.Response(200, json={})

    cfg = settings()
    client, raw = make_client(handler, cfg=cfg)
    status, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload(side)))
    run(raw.aclose())
    assert status == 200
    assert body == {
        "success": True,
        "symbol": "BTCUSDT",
        "side": side,
        "order_type": "LIMIT",
        "validation_status": "passed",
        "binance_http_status": 200,
        "latency_ms": body["latency_ms"],
        "error_category": None,
        "error_code": None,
    }
    assert requests[-1].method == "POST"
    assert requests[-1].url.path == "/api/v3/order/test"
    assert requests[-1].url.params["type"] == "LIMIT"
    assert requests[-1].url.params["side"] == side


@pytest.mark.parametrize(
    ("info", "category"),
    [
        (exchange_info(quote="BTC"), "non_usdt_quote"),
        (exchange_info(spot=False), "non_spot_or_not_tradable"),
        (
            exchange_info(
                price_filter={
                    "filterType": "PRICE_FILTER",
                    "minPrice": "1.00",
                    "maxPrice": "1000000.00",
                    "tickSize": "0.30",
                }
            ),
            "price_filter_rejection",
        ),
        (
            exchange_info(
                lot_filter={
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "100.000",
                    "stepSize": "0.003",
                }
            ),
            "lot_size_rejection",
        ),
        (
            exchange_info(
                notional_filter={"filterType": "MIN_NOTIONAL", "minNotional": "500.00"}
            ),
            "min_notional_rejection",
        ),
        (
            exchange_info(
                notional_filter={
                    "filterType": "NOTIONAL",
                    "minNotional": "10.00",
                    "maxNotional": "100.00",
                }
            ),
            "notional_rejection",
        ),
    ],
)
def test_local_symbol_and_filter_rejections_never_post(info, category):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=info)

    cfg = settings()
    client, raw = make_client(handler, cfg=cfg)
    status, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    assert status == 200
    assert body["success"] is False
    assert body["validation_status"] == "rejected"
    assert body["error_category"] == category
    assert [request.method for request in requests] == ["GET"]


def test_binance_permission_rejection_is_sanitized():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=exchange_info())
        return httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key"})

    cfg = settings()
    client, raw = make_client(handler, cfg=cfg)
    _, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    assert body["success"] is False
    assert body["validation_status"] == "passed"
    assert body["error_category"] == "binance_rejection"
    assert body["error_code"] == -2015
    assert "msg" not in body


@pytest.mark.parametrize(
    ("post_outcome", "category", "status"),
    [
        ("timeout", "timeout", None),
        (429, "rate_limited", 429),
        (503, "server_error", 503),
    ],
)
def test_post_failures_are_not_retried(post_outcome, category, status):
    post_calls = 0

    def handler(request):
        nonlocal post_calls
        if request.method == "GET":
            return httpx.Response(200, json=exchange_info())
        post_calls += 1
        if post_outcome == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(post_outcome, json={"code": -1000, "msg": "sensitive"})

    cfg = settings(retries=5)
    client, raw = make_client(handler, cfg=cfg)
    _, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    assert post_calls == 1
    assert body["error_category"] == category
    assert body["binance_http_status"] == status


def test_real_order_path_is_never_called():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v3/order":
            raise AssertionError("real order endpoint called")
        if request.method == "GET":
            return httpx.Response(200, json=exchange_info())
        return httpx.Response(200, json={})

    client, raw = make_client(handler)
    result = run(client.test_limit_order(**valid_payload()))
    run(raw.aclose())
    assert result["success"] is True
    assert paths == ["/api/v3/exchangeInfo", "/api/v3/order/test"]


def test_secrets_signatures_and_signed_query_never_appear_in_logs_or_response(caplog):
    key = "sensitive-api-key"
    secret = "sensitive-api-secret"
    captured_signature = None
    captured_query = None

    def handler(request):
        nonlocal captured_signature, captured_query
        if request.method == "GET":
            return httpx.Response(200, json=exchange_info())
        captured_signature = request.url.params["signature"]
        captured_query = request.url.query.decode()
        return httpx.Response(
            400,
            json={"code": -1100, "msg": f"{key} {secret} {captured_signature} {captured_query}"},
        )

    cfg = settings(key=key, secret=secret)
    client, raw = make_client(handler, cfg=cfg)
    with caplog.at_level(logging.WARNING):
        _, body = run(call_order_endpoint(ConnectivityApp(cfg, client), valid_payload()))
    run(raw.aclose())
    exposed = caplog.text + json.dumps(body)
    assert captured_signature
    assert captured_query
    assert key not in exposed
    assert secret not in exposed
    assert captured_signature not in exposed
    assert captured_query not in exposed

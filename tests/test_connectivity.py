from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

import httpx

from adaptive_grid_manager.app import ConnectivityApp
from adaptive_grid_manager.binance import BinanceClient
from adaptive_grid_manager.config import Settings
from adaptive_grid_manager.signing import HmacSha256Signer
from adaptive_grid_manager.transport import SafeGetTransport


def run(coro):
    return asyncio.run(coro)


def settings(*, key=None, secret=None, retries=0) -> Settings:
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
    )


def transport(handler, *, retries=0, sleeps=None):
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def fake_sleep(delay):
        if sleeps is not None:
            sleeps.append(delay)

    return SafeGetTransport(
        connect_timeout_seconds=0.1,
        read_timeout_seconds=0.1,
        max_retries=retries,
        backoff_base_seconds=0.001,
        retry_after_cap_seconds=0.01,
        client=async_client,
        sleep=fake_sleep,
    ), async_client


async def call_app(app, path):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": "GET", "path": path}, receive, send)
    status = sent[0]["status"]
    body = json.loads(sent[1]["body"])
    return status, body


def test_public_endpoint_success():
    def handler(request):
        payload = {"serverTime": 123456789} if request.url.path.endswith("time") else {"symbols": []}
        return httpx.Response(200, json=payload)

    tx, raw = transport(handler)
    client = BinanceClient(settings(), tx)
    result = run(client.public_diagnostics())
    run(raw.aclose())
    assert result[0]["reachable"] is True
    assert result[0]["checks"][0]["server_time"] == 123456789


def test_timeout_is_categorized_after_bounded_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret response", request=request)

    tx, raw = transport(handler, retries=1)
    result = run(tx.get("https://binance.test", "/api/v3/time"))
    run(raw.aclose())
    assert calls == 2
    assert result.error_category == "timeout"


def test_429_respects_retry_after_and_is_bounded():
    calls = 0
    sleeps = []

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"msg": "sensitive"})

    tx, raw = transport(handler, retries=1, sleeps=sleeps)
    result = run(tx.get("https://binance.test", "/api/v3/time"))
    run(raw.aclose())
    assert calls == 2
    assert sleeps == [0.01]
    assert result.error_category == "rate_limited"


def test_5xx_retries_then_reports_server_error():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"msg": "unavailable"})

    tx, raw = transport(handler, retries=2)
    result = run(tx.get("https://binance.test", "/api/v3/exchangeInfo"))
    run(raw.aclose())
    assert calls == 3
    assert result.error_category == "server_error"


def test_malformed_binance_response():
    def handler(request):
        return httpx.Response(200, text="not-json")

    tx, raw = transport(handler)
    result = run(tx.get("https://binance.test", "/api/v3/time"))
    run(raw.aclose())
    assert result.error_category == "malformed_response"


def test_signing_is_deterministic():
    parameters = {"timestamp": 1499827319559, "recvWindow": 5000}
    signed = HmacSha256Signer("test-secret").sign(parameters)
    expected = hmac.new(
        b"test-secret", urlencode(parameters).encode(), hashlib.sha256
    ).hexdigest()
    assert signed == {**parameters, "signature": expected}


def test_credentials_absent_disables_authenticated_diagnostics():
    def handler(request):
        raise AssertionError("transport must not be called")

    tx, raw = transport(handler)
    result = run(BinanceClient(settings(), tx).authenticated_diagnostics())
    run(raw.aclose())
    assert result == {"enabled": False, "reason": "credentials_absent"}


def test_credentials_present_perform_only_read_only_gets():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("account"):
            return httpx.Response(200, json={"balances": []})
        if request.url.path.endswith("openOrders"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"enableReading": True, "enableSpotAndMarginTrading": False})

    tx, raw = transport(handler)
    client = BinanceClient(settings(key="test-key", secret="test-secret"), tx, clock_ms=lambda: 123)
    result = run(client.authenticated_diagnostics())
    run(raw.aclose())
    assert result["enabled"] is True
    assert result["reachable"] is True
    assert [request.method for request in requests] == ["GET", "GET", "GET"]
    assert all(request.headers["X-MBX-APIKEY"] == "test-key" for request in requests)
    assert all("signature" in request.url.params for request in requests)


def test_secrets_and_signatures_never_appear_in_logs_or_api_response(caplog):
    key = "highly-sensitive-key"
    secret = "highly-sensitive-secret"
    signature = HmacSha256Signer(secret).sign({"timestamp": 123})["signature"]

    def handler(request):
        return httpx.Response(400, json={"msg": f"{key} {secret} {signature}"})

    tx, raw = transport(handler)
    cfg = settings(key=key, secret=secret)
    client = BinanceClient(cfg, tx, clock_ms=lambda: 123)
    app = ConnectivityApp(cfg, client)
    with caplog.at_level(logging.WARNING):
        status, body = run(call_app(app, "/connectivity/binance"))
    run(raw.aclose())
    combined = caplog.text + json.dumps(body)
    assert status == 200
    assert key not in combined
    assert secret not in combined
    assert signature not in combined


def test_unexpected_client_exception_is_sanitized(caplog):
    secret = "exception-secret"

    class FailingClient:
        async def public_diagnostics(self):
            raise RuntimeError(secret)

    cfg = settings()
    app = ConnectivityApp(cfg, FailingClient())
    with caplog.at_level(logging.ERROR):
        status, body = run(call_app(app, "/connectivity/binance"))
    combined = caplog.text + json.dumps(body)
    assert status == 500
    assert body == {"status": "error", "error": "internal_error"}
    assert secret not in combined


def test_health_endpoint():
    cfg = settings()
    app = ConnectivityApp(cfg, object())
    status, body = run(call_app(app, "/health"))
    assert status == 200
    assert body["status"] == "ok"

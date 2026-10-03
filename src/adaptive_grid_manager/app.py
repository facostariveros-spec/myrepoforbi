from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from . import __version__
from .binance import BinanceClient
from .config import Settings
from .intelligence import CandidateService, GridCandidateIntelligence
from .market_data import BinanceMarketDataClient
from .scanner import MarketScanner
from .scanner_config import ScannerSettings
from .transport import SafeGetTransport


logger = logging.getLogger(__name__)


class ConnectivityApp:
    def __init__(
        self,
        settings: Settings | None = None,
        client: BinanceClient | None = None,
        scanner: MarketScanner | None = None,
        candidate_service: CandidateService | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.scanner_settings = ScannerSettings.from_env()
        self._transport = None
        if client is None:
            self._transport = SafeGetTransport(
                connect_timeout_seconds=self.settings.connect_timeout_seconds,
                read_timeout_seconds=self.settings.read_timeout_seconds,
                max_retries=self.settings.max_retries,
                backoff_base_seconds=self.settings.backoff_base_seconds,
                retry_after_cap_seconds=self.settings.retry_after_cap_seconds,
            )
            client = BinanceClient(self.settings, self._transport)
            market_data = BinanceMarketDataClient(
                self.settings.endpoints[0][1],
                self._transport,
                self.scanner_settings,
            )
            scanner = scanner or MarketScanner(market_data, self.scanner_settings)
        self.client = client
        self.scanner = scanner
        self.candidate_service = candidate_service or (
            CandidateService(scanner, GridCandidateIntelligence()) if scanner is not None else None
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    if self._transport:
                        await self._transport.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            
        if scope["type"] != "http":
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method == "GET" and path == "/health":
            await self._respond(send, 200, {"status": "ok", "version": __version__})
        elif method == "GET" and path == "/connectivity/binance":
            try:
                public = await self.client.public_diagnostics()
                authenticated = await self.client.authenticated_diagnostics()
                healthy = all(endpoint["reachable"] for endpoint in public)
                await self._respond(
                    send,
                    200,
                    {"status": "ok" if healthy else "degraded", "public": public, "authenticated": authenticated},
                )
            except Exception:
                # Exceptions from remote/client boundaries may contain request details.
                # Keep the public log event useful without interpolating exception text.
                logger.error("Unexpected connectivity diagnostic failure")
                await self._respond(send, 500, {"status": "error", "error": "internal_error"})
        elif method == "POST" and path == "/connectivity/binance/order-test":
            try:
                request = await self._read_json(receive)
                if not isinstance(request, dict):
                    await self._respond(send, 400, {"success": False, "error_category": "invalid_request"})
                    return
                result = await self.client.test_limit_order(
                    symbol=request.get("symbol"),
                    side=request.get("side"),
                    quantity=request.get("quantity"),
                    price=request.get("price"),
                )
                status = 403 if result["error_category"] == "feature_disabled" else 200
                await self._respond(send, status, result)
            except (ValueError, json.JSONDecodeError):
                await self._respond(send, 400, {"success": False, "error_category": "invalid_request"})
            except Exception:
                logger.error("Unexpected Binance test-order diagnostic failure")
                await self._respond(send, 500, {"success": False, "error_category": "internal_error"})
        elif method == "GET" and path == "/scanner/scan":
            try:
                if self.scanner is None:
                    await self._respond(send, 503, {"error": "scanner_unavailable"})
                    return
                limit = self._scan_limit(scope.get("query_string", b""))
                await self._respond(send, 200, await self.scanner.scan(limit=limit))
            except ValueError:
                await self._respond(send, 400, {"error": "invalid_limit"})
            except Exception:
                logger.error("Unexpected scanner failure")
                await self._respond(send, 500, {"error": "internal_error"})
        elif method == "GET" and path == "/scanner/candidates":
            try:
                if self.candidate_service is None:
                    await self._respond(send, 503, {"error": "candidate_intelligence_unavailable"})
                    return
                limit = self._scan_limit(scope.get("query_string", b""))
                await self._respond(
                    send, 200, await self.candidate_service.candidates(limit=limit)
                )
            except ValueError:
                await self._respond(send, 400, {"error": "invalid_limit"})
            except Exception:
                logger.error("Unexpected candidate intelligence failure")
                await self._respond(send, 500, {"error": "internal_error"})
        elif path in {
            "/health",
            "/connectivity/binance",
            "/connectivity/binance/order-test",
            "/scanner/scan",
            "/scanner/candidates",
        }:
            await self._respond(send, 405, {"error": "method_not_allowed"})
        else:
            await self._respond(send, 404, {"error": "not_found"})

    @staticmethod
    async def _read_json(receive: Any) -> object:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > 16_384:
                raise ValueError("request too large")
            if not message.get("more_body", False):
                break
        return json.loads(body)

    def _scan_limit(self, query_string: bytes) -> int | None:
        values = parse_qs(query_string.decode("ascii"), keep_blank_values=True)
        if set(values) - {"limit"} or len(values.get("limit", [])) > 1:
            raise ValueError("unsupported query")
        if "limit" not in values:
            return None
        raw = values["limit"][0]
        if not raw.isdigit():
            raise ValueError("invalid limit")
        limit = int(raw)
        if not 1 <= limit <= self.scanner_settings.max_limit:
            raise ValueError("invalid limit")
        return limit

    @staticmethod
    async def _respond(send: Any, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload})


app = ConnectivityApp()

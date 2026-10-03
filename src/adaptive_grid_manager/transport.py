from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import logging
import time
from datetime import datetime, timezone

import httpx


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TransportResult:
    status_code: int | None
    latency_ms: float
    data: object | None
    error_category: str | None
    error_code: int | None = None
    attempts: int = 1


class SafeGetTransport:
    """Retriable safe GET transport plus one fixed, non-retrying test POST."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        retry_after_cap_seconds: float,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._retry_after_cap = retry_after_cap_seconds
        self._sleep = sleep

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        base_url: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> TransportResult:
        started = time.perf_counter()
        status_code: int | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(
                    f"{base_url}{path}", params=params, headers=headers
                )
                status_code = response.status_code
                if self._should_retry(status_code) and attempt < self._max_retries:
                    await self._sleep(self._retry_delay(response, attempt))
                    continue
                latency = (time.perf_counter() - started) * 1000
                if not 200 <= status_code < 300:
                    category = "rate_limited" if status_code == 429 else (
                        "server_error" if status_code >= 500 else "http_error"
                    )
                    logger.warning(
                        "Binance request failed path=%s status=%s category=%s",
                        path,
                        status_code,
                        category,
                    )
                    return TransportResult(
                        status_code, latency, None, category, attempts=attempt + 1
                    )
                try:
                    data = response.json()
                except ValueError:
                    logger.warning("Binance response malformed path=%s status=%s", path, status_code)
                    return TransportResult(
                        status_code,
                        latency,
                        None,
                        "malformed_response",
                        attempts=attempt + 1,
                    )
                return TransportResult(
                    status_code, latency, data, None, attempts=attempt + 1
                )
            except httpx.TimeoutException:
                if attempt < self._max_retries:
                    await self._sleep(self._backoff_base * (2**attempt))
                    continue
                category = "timeout"
            except httpx.TransportError:
                if attempt < self._max_retries:
                    await self._sleep(self._backoff_base * (2**attempt))
                    continue
                category = "network_error"

            latency = (time.perf_counter() - started) * 1000
            logger.warning("Binance request failed path=%s status=%s category=%s", path, status_code, category)
            return TransportResult(
                status_code, latency, None, category, attempts=attempt + 1
            )

        raise AssertionError("unreachable")

    async def post_test_order(
        self,
        base_url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> TransportResult:
        """Send the single permitted POST operation exactly once."""
        path = "/api/v3/order/test"
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{base_url}{path}", params=params, headers=headers
            )
            latency = (time.perf_counter() - started) * 1000
            if not 200 <= response.status_code < 300:
                category = "rate_limited" if response.status_code == 429 else (
                    "server_error" if response.status_code >= 500 else "binance_rejection"
                )
                error_code = self._sanitized_error_code(response)
                logger.warning(
                    "Binance test order rejected path=%s status=%s category=%s code=%s",
                    path,
                    response.status_code,
                    category,
                    error_code,
                )
                return TransportResult(
                    response.status_code, latency, None, category, error_code
                )
            try:
                data = response.json()
            except ValueError:
                logger.warning(
                    "Binance response malformed path=%s status=%s", path, response.status_code
                )
                return TransportResult(
                    response.status_code, latency, None, "malformed_response"
                )
            if not isinstance(data, dict):
                return TransportResult(
                    response.status_code, latency, None, "malformed_response"
                )
            return TransportResult(response.status_code, latency, data, None)
        except httpx.TimeoutException:
            category = "timeout"
        except httpx.TransportError:
            category = "network_error"

        latency = (time.perf_counter() - started) * 1000
        logger.warning(
            "Binance test order failed path=%s status=None category=%s", path, category
        )
        return TransportResult(None, latency, None, category)

    @staticmethod
    def _sanitized_error_code(response: httpx.Response) -> int | None:
        try:
            data = response.json()
        except ValueError:
            return None
        code = data.get("code") if isinstance(data, dict) else None
        return code if isinstance(code, int) and not isinstance(code, bool) else None

    @staticmethod
    def _should_retry(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        if response.status_code == 429:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    delay = float(raw)
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(raw)
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=timezone.utc)
                        delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        delay = self._backoff_base * (2**attempt)
                return max(0.0, min(delay, self._retry_after_cap))
        return self._backoff_base * (2**attempt)

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
import re
import time

from .config import Settings
from .signing import HmacSha256Signer
from .transport import SafeGetTransport, TransportResult


class BinanceClient:
    """Binance diagnostics with one non-executing test-order operation."""

    _SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}$")

    def __init__(
        self,
        settings: Settings,
        transport: SafeGetTransport,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._signer = HmacSha256Signer(settings.api_secret) if settings.api_secret else None

    async def public_diagnostics(self) -> list[dict[str, object]]:
        results = []
        for name, base_url in self._settings.endpoints:
            time_check = await self._public_check(base_url, "/api/v3/time", "time")
            exchange_check = await self._public_check(
                base_url, "/api/v3/exchangeInfo", "exchange_info"
            )
            results.append(
                {
                    "endpoint": name,
                    "base_url": base_url,
                    "reachable": time_check["reachable"] and exchange_check["reachable"],
                    "checks": [time_check, exchange_check],
                }
            )
        return results

    async def authenticated_diagnostics(self) -> dict[str, object]:
        if not self._settings.authenticated_diagnostics_enabled or not self._signer:
            return {"enabled": False, "reason": "credentials_absent"}

        checks = []
        for name, path, validator in (
            ("account", "/api/v3/account", self._validate_account),
            ("open_orders", "/api/v3/openOrders", self._validate_open_orders),
            ("api_restrictions", "/sapi/v1/account/apiRestrictions", self._validate_restrictions),
        ):
            params = self._signer.sign(
                {"timestamp": self._clock_ms(), "recvWindow": self._settings.recv_window_ms}
            )
            result = await self._transport.get(
                self._settings.authenticated_endpoint,
                path,
                params=params,
                headers={"X-MBX-APIKEY": self._settings.api_key or ""},
            )
            checks.append(self._auth_result(name, result, validator))
        return {"enabled": True, "reachable": all(c["reachable"] for c in checks), "checks": checks}

    async def test_limit_order(
        self, *, symbol: object, side: object, quantity: object, price: object
    ) -> dict[str, object]:
        normalized_symbol = symbol.strip().upper() if isinstance(symbol, str) else None
        normalized_side = side.strip().upper() if isinstance(side, str) else None
        result = self._test_order_result(
            success=False,
            symbol=normalized_symbol,
            side=normalized_side,
            validation_status="not_run",
            error_category=None,
        )

        if not self._settings.test_order_enabled:
            return {**result, "error_category": "feature_disabled"}
        if not self._settings.authenticated_diagnostics_enabled or not self._signer:
            return {**result, "error_category": "credentials_absent"}
        if not normalized_symbol or not self._SYMBOL_PATTERN.fullmatch(normalized_symbol):
            return {**result, "validation_status": "rejected", "error_category": "invalid_symbol"}
        if normalized_side not in {"BUY", "SELL"}:
            return {**result, "validation_status": "rejected", "error_category": "invalid_side"}

        parsed_quantity = self._decimal(quantity)
        parsed_price = self._decimal(price)
        if parsed_quantity is None or parsed_quantity <= 0:
            return {**result, "validation_status": "rejected", "error_category": "invalid_quantity"}
        if parsed_price is None or parsed_price <= 0:
            return {**result, "validation_status": "rejected", "error_category": "invalid_price"}

        exchange_info = await self._transport.get(
            self._settings.authenticated_endpoint,
            "/api/v3/exchangeInfo",
            params={"symbol": normalized_symbol},
        )
        if exchange_info.error_category:
            return {
                **result,
                "validation_status": "rejected",
                "binance_http_status": exchange_info.status_code,
                "latency_ms": round(exchange_info.latency_ms, 2),
                "error_category": f"exchange_info_{exchange_info.error_category}",
                "error_code": exchange_info.error_code,
            }

        validation_error = self._validate_symbol_filters(
            exchange_info.data, normalized_symbol, parsed_quantity, parsed_price
        )
        if validation_error:
            return {
                **result,
                "validation_status": "rejected",
                "binance_http_status": exchange_info.status_code,
                "latency_ms": round(exchange_info.latency_ms, 2),
                "error_category": validation_error,
            }

        signed = self._signer.sign(
            {
                "symbol": normalized_symbol,
                "side": normalized_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": self._decimal_text(parsed_quantity),
                "price": self._decimal_text(parsed_price),
                "recvWindow": self._settings.recv_window_ms,
                "timestamp": self._clock_ms(),
            }
        )
        post_result = await self._transport.post_test_order(
            self._settings.authenticated_endpoint,
            params=signed,
            headers={"X-MBX-APIKEY": self._settings.api_key or ""},
        )
        return self._test_order_result(
            success=post_result.error_category is None,
            symbol=normalized_symbol,
            side=normalized_side,
            validation_status="passed",
            binance_http_status=post_result.status_code,
            latency_ms=round(post_result.latency_ms, 2),
            error_category=post_result.error_category,
            error_code=post_result.error_code,
        )

    @classmethod
    def _validate_symbol_filters(
        cls,
        data: object,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
    ) -> str | None:
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            return "malformed_exchange_info"
        matches = [item for item in data["symbols"] if isinstance(item, dict) and item.get("symbol") == symbol]
        if len(matches) != 1:
            return "symbol_not_found"
        item = matches[0]
        if item.get("quoteAsset") != "USDT":
            return "non_usdt_quote"
        if item.get("status") != "TRADING" or not cls._is_spot_allowed(item):
            return "non_spot_or_not_tradable"
        filters = item.get("filters")
        if not isinstance(filters, list):
            return "malformed_exchange_info"
        by_type = {
            entry.get("filterType"): entry
            for entry in filters
            if isinstance(entry, dict) and isinstance(entry.get("filterType"), str)
        }
        price_filter = by_type.get("PRICE_FILTER")
        lot_filter = by_type.get("LOT_SIZE")
        if not isinstance(price_filter, dict) or not isinstance(lot_filter, dict):
            return "required_filter_missing"
        if not cls._within_filter(price, price_filter, "minPrice", "maxPrice", "tickSize"):
            return "price_filter_rejection"
        if not cls._within_filter(quantity, lot_filter, "minQty", "maxQty", "stepSize"):
            return "lot_size_rejection"

        notional = price * quantity
        min_notional = by_type.get("MIN_NOTIONAL")
        if isinstance(min_notional, dict):
            minimum = cls._decimal(min_notional.get("minNotional"))
            if minimum is None or notional < minimum:
                return "min_notional_rejection"
        notional_filter = by_type.get("NOTIONAL")
        if isinstance(notional_filter, dict):
            minimum = cls._decimal(notional_filter.get("minNotional"))
            maximum = cls._decimal(notional_filter.get("maxNotional"))
            if minimum is None or maximum is None or notional < minimum or (
                maximum > 0 and notional > maximum
            ):
                return "notional_rejection"
        return None

    @staticmethod
    def _is_spot_allowed(symbol_info: dict[str, object]) -> bool:
        if symbol_info.get("isSpotTradingAllowed") is True:
            return True
        permissions = symbol_info.get("permissions")
        if isinstance(permissions, list) and "SPOT" in permissions:
            return True
        permission_sets = symbol_info.get("permissionSets")
        return isinstance(permission_sets, list) and any(
            isinstance(group, list) and "SPOT" in group for group in permission_sets
        )

    @classmethod
    def _within_filter(
        cls,
        value: Decimal,
        filter_data: dict[str, object],
        minimum_name: str,
        maximum_name: str,
        increment_name: str,
    ) -> bool:
        minimum = cls._decimal(filter_data.get(minimum_name))
        maximum = cls._decimal(filter_data.get(maximum_name))
        increment = cls._decimal(filter_data.get(increment_name))
        if minimum is None or maximum is None or increment is None:
            return False
        minimum_ok = minimum == 0 or value >= minimum
        maximum_ok = maximum == 0 or value <= maximum
        increment_ok = increment == 0 or (increment > 0 and value % increment == 0)
        return minimum_ok and maximum_ok and increment_ok

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _test_order_result(
        *,
        success: bool,
        symbol: str | None,
        side: str | None,
        validation_status: str,
        binance_http_status: int | None = None,
        latency_ms: float | None = None,
        error_category: str | None,
        error_code: int | None = None,
    ) -> dict[str, object]:
        return {
            "success": success,
            "symbol": symbol,
            "side": side,
            "order_type": "LIMIT",
            "validation_status": validation_status,
            "binance_http_status": binance_http_status,
            "latency_ms": latency_ms,
            "error_category": error_category,
            "error_code": error_code,
        }

    async def _public_check(self, base_url: str, path: str, name: str) -> dict[str, object]:
        result = await self._transport.get(base_url, path)
        valid = result.error_category is None
        server_time = None
        if valid and name == "time":
            valid = isinstance(result.data, dict) and isinstance(result.data.get("serverTime"), int)
            if valid:
                server_time = result.data["serverTime"]
        elif valid and name == "exchange_info":
            valid = isinstance(result.data, dict) and isinstance(result.data.get("symbols"), list)
        error = result.error_category or (None if valid else "malformed_response")
        return {
            "name": name,
            "reachable": valid,
            "http_status": result.status_code,
            "latency_ms": round(result.latency_ms, 2),
            "server_time": server_time,
            "error_category": error,
        }

    @staticmethod
    def _auth_result(name: str, result: TransportResult, validator: Callable[[object], bool]) -> dict[str, object]:
        valid = result.error_category is None and validator(result.data)
        return {
            "name": name,
            "reachable": valid,
            "http_status": result.status_code,
            "latency_ms": round(result.latency_ms, 2),
            "error_category": result.error_category or (None if valid else "malformed_response"),
        }

    @staticmethod
    def _validate_account(data: object) -> bool:
        return isinstance(data, dict) and isinstance(data.get("balances"), list)

    @staticmethod
    def _validate_open_orders(data: object) -> bool:
        return isinstance(data, list)

    @staticmethod
    def _validate_restrictions(data: object) -> bool:
        return isinstance(data, dict) and any(
            key in data for key in ("enableReading", "enableSpotAndMarginTrading", "ipRestrict")
        )

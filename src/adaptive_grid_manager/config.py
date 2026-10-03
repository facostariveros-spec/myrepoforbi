from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_ENDPOINTS = (
    ("binance-data", "https://data-api.binance.vision"),
    ("binance-gcp", "https://api-gcp.binance.com"),
    ("binance-global", "https://api.binance.com"),
)


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    endpoints: tuple[tuple[str, str], ...]
    authenticated_endpoint: str
    api_key: str | None
    api_secret: str | None
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float
    retry_after_cap_seconds: float
    recv_window_ms: int
    test_order_enabled: bool = False

    @property
    def authenticated_diagnostics_enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("BINANCE_API_KEY") or None
        api_secret = os.getenv("BINANCE_API_SECRET") or None
        if bool(api_key) != bool(api_secret):
            api_key = api_secret = None

        endpoint_overrides = []
        for name, default_url in DEFAULT_ENDPOINTS:
            env_name = f"BINANCE_ENDPOINT_{name.upper().replace('-', '_')}"
            endpoint_overrides.append((name, os.getenv(env_name, default_url).rstrip("/")))

        return cls(
            endpoints=tuple(endpoint_overrides),
            authenticated_endpoint=os.getenv(
                "BINANCE_AUTHENTICATED_ENDPOINT", "https://api.binance.com"
            ).rstrip("/"),
            api_key=api_key,
            api_secret=api_secret,
            connect_timeout_seconds=_positive_float("BINANCE_CONNECT_TIMEOUT_SECONDS", 2.0),
            read_timeout_seconds=_positive_float("BINANCE_READ_TIMEOUT_SECONDS", 5.0),
            max_retries=_bounded_int("BINANCE_MAX_RETRIES", 2, 0, 5),
            backoff_base_seconds=_positive_float("BINANCE_BACKOFF_BASE_SECONDS", 0.25),
            retry_after_cap_seconds=_positive_float("BINANCE_RETRY_AFTER_CAP_SECONDS", 5.0),
            recv_window_ms=_bounded_int("BINANCE_RECV_WINDOW_MS", 5_000, 1, 60_000),
            test_order_enabled=os.getenv("BINANCE_TEST_ORDER_ENABLED", "").lower() == "true",
        )

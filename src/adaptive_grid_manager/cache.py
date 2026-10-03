from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TtlCache:
    """Small process-local TTL cache; loss on restart is intentional."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry[object]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        now = self._clock()
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at <= now:
            self._entries.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return entry.value

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        self._entries[key] = _Entry(value=value, expires_at=self._clock() + ttl_seconds)


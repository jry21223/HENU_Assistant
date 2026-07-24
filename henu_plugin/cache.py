"""Thread-safe TTL caches used by the LangBot delivery layer.

Cached values are copied on both write and read. Delivery adapters mutate result
objects while trimming them for QQ, so returning the stored object directly would
silently corrupt subsequent cached responses.
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    created_at: float
    ttl_seconds: float

    def is_expired(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current - self.created_at > self.ttl_seconds


class TTLCache(Generic[T]):
    """Small in-process TTL cache with mutation isolation."""

    def __init__(
        self,
        default_ttl: float = 300.0,
        max_size: int = 1000,
        cleanup_interval: float = 60.0,
    ) -> None:
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._cache: dict[str, CacheEntry[T]] = {}
        self._lock = threading.RLock()
        self._default_ttl = float(default_ttl)
        self._max_size = int(max_size)
        self._cleanup_interval = max(0.0, float(cleanup_interval))
        self._last_cleanup = time.time()

    @staticmethod
    def _copy(value: T) -> T:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return self._copy(entry.value)

    def set(self, key: str, value: T, ttl_seconds: float | None = None) -> None:
        ttl = self._default_ttl if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0:
            self.delete(key)
            return
        with self._lock:
            self._cache[key] = CacheEntry(
                value=self._copy(value),
                created_at=time.time(),
                ttl_seconds=ttl,
            )
            self._maybe_cleanup()

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def invalidate_pattern(self, prefix: str) -> int:
        with self._lock:
            keys = [key for key in self._cache if key.startswith(prefix)]
            for key in keys:
                del self._cache[key]
            return len(keys)

    def _maybe_cleanup(self) -> None:
        now = time.time()
        if (
            now - self._last_cleanup < self._cleanup_interval
            and len(self._cache) < self._max_size
        ):
            return

        self._last_cleanup = now
        expired = [
            key for key, entry in self._cache.items() if entry.is_expired(now=now)
        ]
        for key in expired:
            del self._cache[key]

        if len(self._cache) > self._max_size:
            oldest = sorted(
                self._cache.items(), key=lambda item: item[1].created_at
            )
            for key, _ in oldest[: len(self._cache) - self._max_size]:
                del self._cache[key]

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], T],
        ttl_seconds: float | None = None,
    ) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value, ttl_seconds)
        return self._copy(value)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            expired = sum(
                1 for entry in self._cache.values() if entry.is_expired(now=now)
            )
            return {
                "total_entries": len(self._cache),
                "expired_entries": expired,
                "valid_entries": len(self._cache) - expired,
                "max_size": self._max_size,
                "default_ttl": self._default_ttl,
            }


# Account information may be cached. Time-bearing runtime contexts must not be.
RUNTIME_CONTEXT_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=30.0, max_size=500
)
ACCOUNT_CONTEXT_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=300.0, max_size=500
)
SCHEDULE_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=300.0, max_size=200
)
LIBRARY_QUERY_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=120.0, max_size=200
)
SEMINAR_QUERY_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=60.0, max_size=200
)
# Retained for compatibility; hardened service code does not cache wall-clock time.
SERVER_TIME_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=1.0, max_size=50
)


def invalidate_user_cache(user_key: str) -> None:
    for cache in (
        RUNTIME_CONTEXT_CACHE,
        ACCOUNT_CONTEXT_CACHE,
        SCHEDULE_CACHE,
        LIBRARY_QUERY_CACHE,
        SEMINAR_QUERY_CACHE,
    ):
        cache.invalidate_pattern(f"user:{user_key}:")


def invalidate_account_cache(user_key: str) -> None:
    RUNTIME_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    ACCOUNT_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    SCHEDULE_CACHE.invalidate_pattern(f"user:{user_key}:")


def invalidate_schedule_cache(user_key: str) -> None:
    SCHEDULE_CACHE.invalidate_pattern(f"user:{user_key}:")
    RUNTIME_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")


def clear_all_caches() -> None:
    for cache in (
        RUNTIME_CONTEXT_CACHE,
        ACCOUNT_CONTEXT_CACHE,
        SCHEDULE_CACHE,
        LIBRARY_QUERY_CACHE,
        SEMINAR_QUERY_CACHE,
        SERVER_TIME_CACHE,
    ):
        cache.clear()


def get_cache_stats() -> dict[str, Any]:
    return {
        "runtime_context": RUNTIME_CONTEXT_CACHE.stats(),
        "account_context": ACCOUNT_CONTEXT_CACHE.stats(),
        "schedule": SCHEDULE_CACHE.stats(),
        "library": LIBRARY_QUERY_CACHE.stats(),
        "seminar": SEMINAR_QUERY_CACHE.stats(),
        "server_time": SERVER_TIME_CACHE.stats(),
    }

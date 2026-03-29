"""TTL-based cache for runtime context and API responses.

Provides thread-safe caching with configurable TTL to reduce file I/O and API calls.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """A single cache entry with timestamp."""
    value: T
    created_at: float
    ttl_seconds: float

    def is_expired(self) -> bool:
        """Check if entry has exceeded TTL."""
        return time.time() - self.created_at > self.ttl_seconds


class TTLCache(Generic[T]):
    """Thread-safe TTL cache with automatic expiration."""

    def __init__(
        self,
        default_ttl: float = 300.0,
        max_size: int = 1000,
        cleanup_interval: float = 60.0,
    ):
        """
        Args:
            default_ttl: Default TTL in seconds (default: 5 minutes)
            max_size: Maximum number of entries before cleanup
            cleanup_interval: Interval for automatic cleanup in seconds
        """
        self._cache: dict[str, CacheEntry[T]] = {}
        self._lock = threading.RLock()
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()

    def get(self, key: str) -> T | None:
        """Get cached value if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value

    def set(
        self,
        key: str,
        value: T,
        ttl_seconds: float | None = None,
    ) -> None:
        """Set cached value with optional custom TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        with self._lock:
            self._cache[key] = CacheEntry(
                value=value,
                created_at=time.time(),
                ttl_seconds=ttl,
            )
            self._maybe_cleanup()

    def delete(self, key: str) -> bool:
        """Delete a specific cache entry."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def invalidate_pattern(self, prefix: str) -> int:
        """Invalidate all entries matching key prefix."""
        with self._lock:
            keys_to_delete = [
                k for k in self._cache.keys() if k.startswith(prefix)
            ]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    def _maybe_cleanup(self) -> None:
        """Cleanup expired entries if interval elapsed or size exceeded."""
        now = time.time()
        if (
            now - self._last_cleanup < self._cleanup_interval
            and len(self._cache) < self._max_size
        ):
            return

        self._last_cleanup = now
        expired_keys = [
            k for k, v in self._cache.items() if v.is_expired()
        ]
        for key in expired_keys:
            del self._cache[key]

        # If still over max_size, remove oldest entries
        if len(self._cache) > self._max_size:
            sorted_entries = sorted(
                self._cache.items(),
                key=lambda x: x[1].created_at,
            )
            entries_to_remove = len(self._cache) - self._max_size
            for key, _ in sorted_entries[:entries_to_remove]:
                del self._cache[key]

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], T],
        ttl_seconds: float | None = None,
    ) -> T:
        """Get cached value or compute and cache if missing/expired."""
        cached = self.get(key)
        if cached is not None:
            return cached

        value = compute_fn()
        self.set(key, value, ttl_seconds)
        return value

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            now = time.time()
            expired = sum(1 for v in self._cache.values() if v.is_expired())
            return {
                "total_entries": len(self._cache),
                "expired_entries": expired,
                "valid_entries": len(self._cache) - expired,
                "max_size": self._max_size,
                "default_ttl": self._default_ttl,
            }


# Global cache instances with different TTL configurations

# Runtime context cache: 5 minutes (account binding rarely changes)
RUNTIME_CONTEXT_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=300.0,
    max_size=500,
)

# Account context cache: 5 minutes
ACCOUNT_CONTEXT_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=300.0,
    max_size=500,
)

# Schedule data cache: 10 minutes (synced daily, rarely changes)
SCHEDULE_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=600.0,
    max_size=200,
)

# Library query cache: 2 minutes (reservation status changes frequently)
LIBRARY_QUERY_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=120.0,
    max_size=200,
)

# Seminar rooms cache: 5 minutes (availability changes moderately)
SEMINAR_QUERY_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=300.0,
    max_size=200,
)

# Server time cache: 30 seconds (needs frequent refresh)
SERVER_TIME_CACHE: TTLCache[dict[str, Any]] = TTLCache(
    default_ttl=30.0,
    max_size=50,
)


def invalidate_user_cache(user_key: str) -> None:
    """Invalidate all caches for a specific user."""
    RUNTIME_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    ACCOUNT_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    SCHEDULE_CACHE.invalidate_pattern(f"user:{user_key}:")
    LIBRARY_QUERY_CACHE.invalidate_pattern(f"user:{user_key}:")
    SEMINAR_QUERY_CACHE.invalidate_pattern(f"user:{user_key}:")


def invalidate_account_cache(user_key: str) -> None:
    """Invalidate caches when account is updated."""
    RUNTIME_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    ACCOUNT_CONTEXT_CACHE.invalidate_pattern(f"user:{user_key}:")
    SCHEDULE_CACHE.invalidate_pattern(f"user:{user_key}:")


def invalidate_schedule_cache(user_key: str) -> None:
    """Invalidate caches when schedule is synced."""
    SCHEDULE_CACHE.invalidate_pattern(f"user:{user_key}:")
    RUNTIME_CONTEXT_CACHE.delete(f"user:{user_key}:runtime_context")


def clear_all_caches() -> None:
    """Clear all global caches."""
    RUNTIME_CONTEXT_CACHE.clear()
    ACCOUNT_CONTEXT_CACHE.clear()
    SCHEDULE_CACHE.clear()
    LIBRARY_QUERY_CACHE.clear()
    SEMINAR_QUERY_CACHE.clear()
    SERVER_TIME_CACHE.clear()


def get_cache_stats() -> dict[str, Any]:
    """Get statistics for all caches."""
    return {
        "runtime_context": RUNTIME_CONTEXT_CACHE.stats(),
        "account_context": ACCOUNT_CONTEXT_CACHE.stats(),
        "schedule": SCHEDULE_CACHE.stats(),
        "library": LIBRARY_QUERY_CACHE.stats(),
        "seminar": SEMINAR_QUERY_CACHE.stats(),
        "server_time": SERVER_TIME_CACHE.stats(),
    }
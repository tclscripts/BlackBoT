# optimized_cache.py - Advanced TTL Cache implementations for BlackBoT
# Replaces the manual cleanup approach with automatic memory management

import time
import threading
import weakref
from collections import OrderedDict
from typing import Optional, Any, Callable, Dict, Tuple
from dataclasses import dataclass
from functools import wraps
import gc


@dataclass
class CacheStats:
    """Cache performance statistics"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expired: int = 0
    size: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total > 0 else 0.0

    def reset(self):
        self.hits = self.misses = self.evictions = self.expired = self.size = 0


class TTLCacheEntry:
    """Cache entry with automatic expiration using weakref callbacks"""

    __slots__ = ('value', 'timestamp', 'ttl', '_cleanup_ref', '_cache_ref', '_key')

    def __init__(self, value: Any, ttl: float, cache_ref: 'TTLCache', key: Any):
        self.value = value
        self.timestamp = time.time()
        self.ttl = ttl
        self._key = key

        # Store weak reference to cache for cleanup
        self._cache_ref = weakref.ref(cache_ref)

        # Create weakref callback for automatic cleanup when value is collected
        if hasattr(value, '__weakref__'):
            try:
                self._cleanup_ref = weakref.ref(value, self._value_cleanup_callback)
            except TypeError:
                # Value doesn't support weak references
                self._cleanup_ref = None
        else:
            self._cleanup_ref = None

    def _value_cleanup_callback(self, weak_value_ref):
        """Called when the cached value is garbage collected"""
        cache = self._cache_ref()
        if cache is not None:
            cache._remove_if_expired(self._key, silent=True)

    @property
    def is_expired(self) -> bool:
        """Check if entry has expired"""
        return (time.time() - self.timestamp) > self.ttl

    @property
    def remaining_ttl(self) -> float:
        """Get remaining time to live in seconds"""
        return max(0, self.ttl - (time.time() - self.timestamp))


class OptimizedTTLCache:
    """
    Optimized TTL Cache with:
    - Weakref callbacks for automatic cleanup
    - Lazy expiration (no background threads)
    - LRU eviction policy
    - Thread-safe operations
    - Performance statistics
    - Memory-efficient storage
    """

    def __init__(self, maxlen: int = 2000, ttl: float = 6 * 3600,
                 enable_stats: bool = True, auto_trim_threshold: float = 0.1):
        self._data: OrderedDict[Any, TTLCacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self.maxlen = maxlen
        self.default_ttl = ttl
        self.auto_trim_threshold = auto_trim_threshold  # Trim when 10% operations trigger it
        self._stats = CacheStats() if enable_stats else None
        self._operation_count = 0

    def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        """Set a key-value pair with optional custom TTL"""
        ttl = ttl or self.default_ttl

        with self._lock:
            # Create new cache entry with weakref callback
            entry = TTLCacheEntry(value, ttl, self, key)

            # Remove old entry if exists
            if key in self._data:
                del self._data[key]
                if self._stats:
                    self._stats.evictions += 1

            # Add new entry (moves to end for LRU)
            self._data[key] = entry

            if self._stats:
                self._stats.size = len(self._data)

            # Perform maintenance if needed
            self._maybe_trim()

    def get(self, key: Any, default: Any = None) -> Any:
        """Get value by key, returning default if not found or expired"""
        with self._lock:
            entry = self._data.get(key)

            if entry is None:
                if self._stats:
                    self._stats.misses += 1
                return default

            if entry.is_expired:
                # Lazy expiration - remove when accessed
                del self._data[key]
                if self._stats:
                    self._stats.expired += 1
                    self._stats.misses += 1
                    self._stats.size = len(self._data)
                return default

            # Move to end for LRU (most recently used)
            self._data.move_to_end(key)

            if self._stats:
                self._stats.hits += 1

            return entry.value

    def get_with_ttl(self, key: Any) -> Tuple[Any, float]:
        """Get value and remaining TTL"""
        with self._lock:
            entry = self._data.get(key)

            if entry is None or entry.is_expired:
                return None, 0.0

            # Move to end for LRU
            self._data.move_to_end(key)

            if self._stats:
                self._stats.hits += 1

            return entry.value, entry.remaining_ttl

    def get_valid(self, key: Any) -> Any:
        """Alias for get() to maintain compatibility with original TTLCache"""
        return self.get(key)

    def delete(self, key: Any) -> bool:
        """Delete a key from cache, return True if key existed"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                if self._stats:
                    self._stats.size = len(self._data)
                return True
            return False

    def _remove_if_expired(self, key: Any, silent: bool = False) -> bool:
        """Remove key if expired, called by weakref callbacks"""
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry and entry.is_expired:
                    del self._data[key]
                    if self._stats and not silent:
                        self._stats.expired += 1
                        self._stats.size = len(self._data)
                    return True
                return False
        except:
            # Ignore errors during cleanup
            return False

    def _maybe_trim(self) -> None:
        """Perform cache maintenance if thresholds are met"""
        self._operation_count += 1

        # Auto-trim based on operation count (avoid constant trimming)
        trim_interval = max(100, int(self.maxlen * self.auto_trim_threshold))

        if self._operation_count % trim_interval == 0:
            self._trim_expired()

        # Always enforce size limit
        if len(self._data) > self.maxlen:
            self._evict_lru()

    def _trim_expired(self) -> int:
        """Remove all expired entries, return count of removed items"""
        expired_count = 0
        now = time.time()

        # Iterate over copy to avoid modification during iteration
        for key, entry in list(self._data.items()):
            if (now - entry.timestamp) > entry.ttl:
                del self._data[key]
                expired_count += 1

        if self._stats and expired_count > 0:
            self._stats.expired += expired_count
            self._stats.size = len(self._data)

        return expired_count

    def _evict_lru(self) -> int:
        """Evict least recently used items to stay within maxlen"""
        evicted_count = 0

        while len(self._data) > self.maxlen:
            # Remove from beginning (oldest in LRU order)
            self._data.popitem(last=False)
            evicted_count += 1

        if self._stats and evicted_count > 0:
            self._stats.evictions += evicted_count
            self._stats.size = len(self._data)

        return evicted_count

    def clear(self) -> None:
        """Clear all entries"""
        with self._lock:
            self._data.clear()
            if self._stats:
                self._stats.size = 0
                self._stats.evictions += 1  # Count clear as mass eviction

    def cleanup(self) -> Dict[str, int]:
        """Manual cleanup - trim expired and return statistics"""
        with self._lock:
            expired = self._trim_expired()
            evicted = self._evict_lru()

            # Force garbage collection to trigger weakref callbacks
            collected = gc.collect()

            return {
                'expired_removed': expired,
                'lru_evicted': evicted,
                'gc_collected': collected,
                'current_size': len(self._data)
            }

    def keys(self):
        """Get all valid (non-expired) keys"""
        with self._lock:
            now = time.time()
            valid_keys = []
            for key, entry in self._data.items():
                if (now - entry.timestamp) <= entry.ttl:
                    valid_keys.append(key)
            return valid_keys

    def items(self):
        """Get all valid (non-expired) key-value pairs"""
        with self._lock:
            now = time.time()
            valid_items = []
            for key, entry in self._data.items():
                if (now - entry.timestamp) <= entry.ttl:
                    valid_items.append((key, entry.value))
            return valid_items

    def __len__(self) -> int:
        """Get total number of entries (including potentially expired)"""
        return len(self._data)

    def __contains__(self, key: Any) -> bool:
        """Check if key exists and is not expired"""
        return self.get(key) is not None

    @property
    def stats(self) -> Optional[CacheStats]:
        """Get cache performance statistics"""
        if self._stats:
            # Update current size
            self._stats.size = len(self._data)
        return self._stats

    def reset_stats(self) -> None:
        """Reset performance statistics"""
        if self._stats:
            self._stats.reset()


class SmartTTLCache(OptimizedTTLCache):
    """
    Enhanced TTL Cache with additional smart features:
    - Adaptive TTL based on access patterns
    - Automatic background cleanup (optional) - IMPROVED
    - Cache warming strategies
    - Advanced eviction policies
    """

    def __init__(self, maxlen: int = 2000, ttl: float = 6 * 3600,
                 adaptive_ttl: bool = True, background_cleanup: bool = False,
                 cleanup_interval: float = 300.0,
                 **kwargs):
        super().__init__(maxlen, ttl, **kwargs)
        self.adaptive_ttl = adaptive_ttl
        self._access_patterns: Dict[Any, list] = {}  # Track access times
        self._background_cleanup = background_cleanup
        self._cleanup_interval = cleanup_interval
        self._cleanup_thread = None
        self._cleanup_stop_event = None

        if background_cleanup:
            self._start_background_cleanup()

    def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        """Enhanced set with adaptive TTL"""
        if ttl is None and self.adaptive_ttl:
            ttl = self._calculate_adaptive_ttl(key)

        super().set(key, value, ttl)

        # Track access pattern
        if self.adaptive_ttl:
            self._track_access(key)

    def get(self, key: Any, default: Any = None) -> Any:
        """Enhanced get with access tracking"""
        result = super().get(key, default)

        # Track successful access for adaptive TTL
        if result != default and self.adaptive_ttl:
            self._track_access(key)

        return result

    def _track_access(self, key: Any) -> None:
        """Track access patterns for adaptive TTL calculation"""
        now = time.time()

        if key not in self._access_patterns:
            self._access_patterns[key] = []

        access_list = self._access_patterns[key]
        access_list.append(now)

        # Keep only recent access history (last 10 accesses)
        if len(access_list) > 10:
            access_list.pop(0)

    def _calculate_adaptive_ttl(self, key: Any) -> float:
        """Calculate adaptive TTL based on access patterns"""
        if key not in self._access_patterns:
            return self.default_ttl

        accesses = self._access_patterns[key]
        if len(accesses) < 2:
            return self.default_ttl

        # Calculate access frequency
        time_span = accesses[-1] - accesses[0]
        access_frequency = len(accesses) / max(time_span, 1)  # accesses per second

        # Adaptive TTL: more frequent access = longer TTL
        # Base TTL * (1 + log(frequency + 1))
        import math
        adaptive_multiplier = 1 + math.log(access_frequency + 1)
        adaptive_ttl = self.default_ttl * min(adaptive_multiplier, 3.0)  # Cap at 3x

        return adaptive_ttl

    def _start_background_cleanup(self) -> None:
        """
        IMPROVED: Start background cleanup with proper thread management.
        Uses a single long-running thread instead of creating new timers.
        """
        if self._cleanup_thread is not None:
            # Already running
            return

        self._cleanup_stop_event = threading.Event()

        def cleanup_loop():
            """Long-running cleanup loop with interruptible wait"""
            from core.log import get_logger
            logger = get_logger("cache_cleanup")

            logger.debug(f"Background cleanup started for cache (interval={self._cleanup_interval}s)")

            while not self._cleanup_stop_event.is_set():
                try:
                    # Perform cleanup
                    stats = self.cleanup()
                    if stats['expired_removed'] > 0 or stats['lru_evicted'] > 0:
                        logger.debug(
                            f"Cache cleanup: removed {stats['expired_removed']} expired, "
                            f"evicted {stats['lru_evicted']} LRU entries, "
                            f"current size: {stats['current_size']}"
                        )
                except Exception as e:
                    logger.warning(f"Cache cleanup failed: {e}", exc_info=True)

                # Interruptible wait - can be stopped immediately
                if self._cleanup_stop_event.wait(timeout=self._cleanup_interval):
                    # Stop was requested
                    break

            logger.debug("Background cleanup stopped")

        self._cleanup_thread = threading.Thread(
            target=cleanup_loop,
            name=f"cache_cleanup_{id(self)}",
            daemon=True
        )
        self._cleanup_thread.start()

    def stop_background_cleanup(self) -> None:
        """IMPROVED: Stop background cleanup thread gracefully"""
        if self._cleanup_stop_event is not None:
            self._cleanup_stop_event.set()

        if self._cleanup_thread is not None:
            try:
                # Wait for thread to finish (max 2 seconds)
                self._cleanup_thread.join(timeout=2.0)
                if self._cleanup_thread.is_alive():
                    from core.log import get_logger
                    logger = get_logger("cache_cleanup")
                    logger.warning(
                        f"Cleanup thread {self._cleanup_thread.name} did not stop within 2 seconds"
                    )
            except Exception as e:
                from core.log import get_logger
                logger = get_logger("cache_cleanup")
                logger.error(f"Error stopping cleanup thread: {e}")
            finally:
                self._cleanup_thread = None
                self._cleanup_stop_event = None

    def warm_cache(self, warming_function: Callable[[Any], Any],
                   keys: list, ttl: Optional[float] = None) -> int:
        """Warm cache with data from warming function"""
        warmed_count = 0

        for key in keys:
            try:
                value = warming_function(key)
                if value is not None:
                    self.set(key, value, ttl)
                    warmed_count += 1
            except:
                continue  # Skip failed warming attempts

        return warmed_count

    def __del__(self):
        """Cleanup background thread on destruction"""
        try:
            self.stop_background_cleanup()
        except:
            pass


# Decorator for easy caching
def ttl_cached(ttl: float = 3600, maxsize: int = 1000,
               cache_instance: Optional[OptimizedTTLCache] = None):
    """
    Decorator for caching function results with TTL

    Args:
        ttl: Time to live in seconds
        maxsize: Maximum cache size
        cache_instance: Optional existing cache instance to use
    """

    def decorator(func):
        # Create cache instance if not provided
        cache = cache_instance or OptimizedTTLCache(maxlen=maxsize, ttl=ttl)

        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from arguments
            key = (args, tuple(sorted(kwargs.items())))

            # Try to get cached result
            result = cache.get(key)
            if result is not None:
                return result

            # Calculate and cache result
            result = func(*args, **kwargs)
            cache.set(key, result)

            return result

        # Expose cache for manual management
        wrapper.cache = cache
        wrapper.cache_stats = cache.stats
        wrapper.clear_cache = cache.clear

        return wrapper

    return decorator
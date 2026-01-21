"""
cache_manager.py - Unified Cache Management for BlackBoT
Provides optimized cache wrappers with backward compatibility
"""

import threading
from typing import Any, Optional, Set, Dict, Callable

# Try multiple import paths for flexibility
try:
    from core.optimized_cache import SmartTTLCache
except ImportError:
    try:
        from optimized_cache import SmartTTLCache
    except ImportError:
        import sys
        import os

        # Add parent directory to path
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from optimized_cache import SmartTTLCache


class LoggedUsersCache:
    """
    Optimized cache for logged_in_users with backward compatibility

    Replaces: self.logged_in_users = {}

    Usage:
        self.logged_in_users = LoggedUsersCache()

        # Works like dict:
        self.logged_in_users[userId] = {"hosts": [...], "nick": "..."}
        if userId in self.logged_in_users: ...
        user_data = self.logged_in_users[userId]

        # New helper methods:
        self.logged_in_users.update_user(userId, {"nick": "newnick"})
        self.logged_in_users.is_logged_in(userId, host_mask)
    """

    def __init__(self, maxlen=5000, ttl=48 * 3600):
        self._cache = SmartTTLCache(
            maxlen=maxlen,
            ttl=ttl,
            adaptive_ttl=True,
            enable_stats=True,
            background_cleanup=False
        )
        self._lock = threading.RLock()

    def __getitem__(self, userId):
        """Dict-like access: self.logged_in_users[userId]"""
        user_data = self._cache.get(userId)
        if user_data is None:
            raise KeyError(userId)
        return user_data

    def __delitem__(self, host):
        """Enable del cache[host] syntax"""
        with self._lock:
            if not self._cache.delete(host):
                raise KeyError(host)

    def __setitem__(self, userId, value):
        """Dict-like assignment: self.logged_in_users[userId] = {...}"""
        self._cache.set(userId, value)

    def __contains__(self, userId):
        """Dict-like 'in' check: if userId in self.logged_in_users"""
        return self._cache.get(userId) is not None

    def get(self, userId, default=None):
        """Dict-like get with default"""
        return self._cache.get(userId, default)

    def pop(self, userId, default=None):
        """Dict-like pop"""
        user_data = self._cache.get(userId, default)
        self._cache.delete(userId)
        return user_data

    def setdefault(self, userId, default=None):
        """Dict-like setdefault"""
        with self._lock:
            existing = self._cache.get(userId)
            if existing is not None:
                return existing
            self._cache.set(userId, default)
            return default

    def items(self):
        """Dict-like items() iterator"""
        return self._cache.items()

    def keys(self):
        """Dict-like keys() iterator"""
        return self._cache.keys()

    def values(self):
        """Dict-like values() iterator"""
        return [v for k, v in self._cache.items()]

    def clear(self):
        """Clear all entries"""
        self._cache.clear()

    def __len__(self):
        """Number of entries"""
        return len(self._cache)

    # === Helper Methods ===

    def update_user(self, userId, updates: Dict[str, Any]):
        """
        Thread-safe atomic update of user data

        Args:
            userId: User ID
            updates: Dict of fields to update

        Example:
            cache.update_user(userId, {"nick": "newnick"})
        """
        with self._lock:
            user_data = self._cache.get(userId, {"hosts": [], "nick": "", "nicks": set()})
            user_data.update(updates)
            self._cache.set(userId, user_data)
            return user_data

    def modify_user(self, userId, modifier: Callable):
        """
        Thread-safe functional update

        Args:
            userId: User ID
            modifier: Callable(user_data) -> modified_user_data

        Example:
            cache.modify_user(userId, lambda u: {**u, "nick": "new"})
        """
        with self._lock:
            user_data = self._cache.get(userId, {"hosts": [], "nick": "", "nicks": set()})
            modified = modifier(user_data)
            self._cache.set(userId, modified)
            return modified

    def add_host(self, userId, host_mask, nick=None):
        """
        Add host to user's host list

        Args:
            userId: User ID
            host_mask: Host mask to add
            nick: Optional nick to set/update
        """
        with self._lock:
            user_data = self._cache.get(userId, {"hosts": [], "nick": "", "nicks": set()})

            if host_mask not in user_data["hosts"]:
                user_data["hosts"].append(host_mask)

            if nick:
                user_data["nick"] = nick
                user_data.setdefault("nicks", set()).add(nick)

            self._cache.set(userId, user_data)
            return user_data

    def remove_host(self, userId, host_mask):
        """Remove host from user's host list"""
        with self._lock:
            user_data = self._cache.get(userId)
            if user_data and host_mask in user_data.get("hosts", []):
                user_data["hosts"].remove(host_mask)
                if user_data["hosts"]:
                    self._cache.set(userId, user_data)
                else:
                    # No more hosts, remove user
                    self._cache.delete(userId)

    def is_logged_in(self, userId, host_mask=None):
        """
        Check if user is logged in

        Args:
            userId: User ID
            host_mask: Optional host to verify

        Returns:
            bool: True if logged in (and from host if specified)
        """
        user_data = self._cache.get(userId)
        if user_data is None:
            return False

        if host_mask is None:
            return True

        return host_mask in user_data.get("hosts", [])

    @property
    def stats(self):
        """Get cache statistics"""
        return self._cache.stats


class KnownUsersCache:
    """
    Optimized cache for known_users with set-like interface

    Replaces: self.known_users = set()  # (channel, nick)

    Usage:
        self.known_users = KnownUsersCache()

        # Works like set:
        self.known_users.add((channel, nick))
        if (channel, nick) in self.known_users: ...
        self.known_users.discard((channel, nick))
        for channel, nick in self.known_users: ...
    """

    def __init__(self, maxlen=10000, ttl=12 * 3600):
        self._cache = SmartTTLCache(
            maxlen=maxlen,
            ttl=ttl,
            adaptive_ttl=False,
            enable_stats=True,
            background_cleanup=False
        )

    def _make_key(self, item):
        """Convert (channel, nick) tuple to cache key"""
        if isinstance(item, tuple) and len(item) == 2:
            channel, nick = item
            return f"{channel}:{nick}"
        raise ValueError(f"Expected (channel, nick) tuple, got {item}")

    def add(self, item):
        """Set-like add: known_users.add((channel, nick))"""
        key = self._make_key(item)
        self._cache.set(key, True)

    def discard(self, item):
        """Set-like discard: known_users.discard((channel, nick))"""
        key = self._make_key(item)
        self._cache.delete(key)

    def remove(self, item):
        """Set-like remove (raises KeyError if not found)"""
        key = self._make_key(item)
        if self._cache.get(key) is None:
            raise KeyError(item)
        self._cache.delete(key)

    def __contains__(self, item):
        """Set-like 'in' check: if (channel, nick) in known_users"""
        key = self._make_key(item)
        return self._cache.get(key) is not None

    def clear(self):
        """Clear all entries"""
        self._cache.clear()

    def __iter__(self):
        """Iterate over (channel, nick) tuples"""
        for key in self._cache.keys():
            if ':' in key:
                channel, nick = key.split(':', 1)
                yield (channel, nick)

    def __len__(self):
        """Number of entries"""
        return len(self._cache)

    # === Helper Methods ===

    def filter_by_channel(self, channel):
        """Get all nicks in a channel"""
        return [nick for ch, nick in self if ch.lower() == channel.lower()]

    def filter_by_nick(self, nick):
        """Get all channels where nick is known"""
        return [ch for ch, n in self if n.lower() == nick.lower()]

    def remove_nick(self, nick):
        """Remove nick from all channels"""
        to_remove = [(ch, n) for ch, n in self if n.lower() == nick.lower()]
        for item in to_remove:
            self.discard(item)
        return len(to_remove)

    def remove_channel(self, channel):
        """Remove all users from a channel"""
        to_remove = [(ch, n) for ch, n in self if ch.lower() == channel.lower()]
        for item in to_remove:
            self.discard(item)
        return len(to_remove)

    @property
    def stats(self):
        """Get cache statistics"""
        return self._cache.stats


class HostToNicksCache:
    """
    Optimized cache for host_to_nicks mapping

    Replaces: self.host_to_nicks = defaultdict(set)

    Usage:
        self.host_to_nicks = HostToNicksCache()

        # Works like defaultdict(set):
        self.host_to_nicks[host].add(nick)
        nicks = self.host_to_nicks[host]
        if host in self.host_to_nicks: ...
    """

    def __init__(self, maxlen=3000, ttl=24 * 3600):
        self._cache = SmartTTLCache(
            maxlen=maxlen,
            ttl=ttl,
            adaptive_ttl=True,
            enable_stats=True,
            background_cleanup=False
        )
        self._lock = threading.RLock()

    def __getitem__(self, host):
        """
        Dict-like access with defaultdict behavior
        Returns a HostNickSet that automatically syncs with cache
        """
        return HostNickSet(self, host)

    def __contains__(self, host):
        """Check if host exists"""
        return self._cache.get(host) is not None

    def get(self, host, default=None):
        """Get nicks for host"""
        nicks = self._cache.get(host)
        return nicks if nicks is not None else (default or set())

    def keys(self):
        """Get all hosts"""
        return self._cache.keys()

    def items(self):
        """Get all (host, nicks) pairs"""
        return [(k, v) for k, v in self._cache.items()]

    def values(self):
        """Get all nick sets"""
        return [v for k, v in self._cache.items()]

    def clear(self):
        """Clear all mappings"""
        self._cache.clear()

    def __len__(self):
        """Number of hosts tracked"""
        return len(self._cache)

    # === Helper Methods ===

    def add_nick(self, host, nick):
        """Add nick to host mapping"""
        with self._lock:
            nicks = self._cache.get(host, set())
            nicks.add(nick)
            self._cache.set(host, nicks)

    def remove_nick(self, host, nick):
        """Remove nick from host mapping"""
        with self._lock:
            nicks = self._cache.get(host)
            if nicks and nick in nicks:
                nicks.discard(nick)
                if nicks:
                    self._cache.set(host, nicks)
                else:
                    self._cache.delete(host)

    def find_hosts_for_nick(self, nick):
        """Find all hosts associated with a nick"""
        return [host for host, nicks in self._cache.items() if nick in nicks]

    @property
    def stats(self):
        """Get cache statistics"""
        return self._cache.stats


class HostNickSet:
    """Helper class for transparent defaultdict(set) behavior"""

    def __init__(self, parent_cache, host):
        self._parent = parent_cache
        self._host = host

    def add(self, nick):
        """Add nick to this host's set"""
        self._parent.add_nick(self._host, nick)

    def discard(self, nick):
        """Remove nick from this host's set"""
        self._parent.remove_nick(self._host, nick)

    def __contains__(self, nick):
        """Check if nick is in this host's set"""
        nicks = self._parent._cache.get(self._host, set())
        return nick in nicks

    def __iter__(self):
        """Iterate over nicks for this host"""
        nicks = self._parent._cache.get(self._host, set())
        return iter(nicks)

    def __len__(self):
        """Number of nicks for this host"""
        nicks = self._parent._cache.get(self._host, set())
        return len(nicks)


def get_cache_statistics_report(bot_instance):
    """
    Generate a comprehensive cache statistics report

    Args:
        bot_instance: BlackBoT instance

    Returns:
        list: Formatted lines for IRC output
    """
    lines = []
    lines.append("📊 Cache Statistics:")

    caches = [
        ('user_cache', 'User Cache (nick→userId)'),
        ('logged_in_users', 'Logged Users'),
        ('known_users', 'Known Users (chan:nick)'),
        ('host_to_nicks', 'Host→Nick Mapping'),
    ]

    for attr_name, display_name in caches:
        cache = getattr(bot_instance, attr_name, None)
        if not cache:
            continue

        stats = getattr(cache, 'stats', None)
        if not stats:
            continue

        cache_obj = cache._cache if hasattr(cache, '_cache') else cache
        maxlen = getattr(cache_obj, 'maxlen', '?')

        line = (
            f"  • {display_name}: "
            f"{stats.size}/{maxlen} entries, "
            f"hit rate {stats.hit_rate:.1%} "
            f"({stats.hits}H/{stats.misses}M), "
            f"evicted {stats.evictions}, "
            f"expired {stats.expired}"
        )
        lines.append(line)

    return lines
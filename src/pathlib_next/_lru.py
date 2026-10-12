"""A thread-safe memoizing LRU cache."""

from __future__ import annotations

import collections
import typing as _ty
from threading import RLock

__all__ = ["LRU"]

try:
    ParamSpec = _ty.ParamSpec  # 3.10+
except AttributeError:
    try:
        from typing_extensions import ParamSpec
    except ImportError:

        class ParamSpec(_ty.TypeVar, _root=True):
            """Minimal `ParamSpec` stand-in for 3.9 without `typing_extensions`.

            Only the `.args` / `.kwargs` attributes are needed: they appear in
            annotations that must merely *evaluate*, and a plain `TypeVar` has
            neither. `typing_extensions` is not a runtime dependency, so the
            fallback keeps a bare 3.9 install importable.
            """

            @property
            def args(self):
                return self

            @property
            def kwargs(self):
                return self


K = ParamSpec("K")


V = _ty.TypeVar("V")


class LRU(_ty.Generic[K, V]):
    """Thread-safe memoizing LRU cache over a function, callable like the
    function itself; `invalidate(*args)` evicts and recomputes an entry,
    `discard(*args)` evicts without recomputing.

    `on_evict(key, value)`, when given, is called for every value the cache
    drops -- LRU overflow, a `maxsize` shrink, `invalidate()`/`discard()`,
    and the losing result of two concurrent misses on one key -- so a cache
    of live resources (connections) can close them instead of leaking them.
    It runs outside the lock, and an exception it raises is suppressed:
    eviction is a side effect of an unrelated lookup, which must not fail
    because a stale resource would not close cleanly.

    The computation itself runs outside the lock, so concurrent misses on
    one key may each call `func`; the first stored result wins and is
    returned to every caller, and the others are passed to `on_evict`.

    `maxsize` follows `functools.lru_cache`: `None` is unbounded, and `0` (or
    less) stores nothing, so `func` runs on every call and its result is
    returned without reaching `on_evict`.
    """

    def __init__(
        self,
        func: _ty.Callable[K, V],
        maxsize: int | None = 128,
        on_evict: "_ty.Callable[[tuple, V], object] | None" = None,
    ):
        self.cache = collections.OrderedDict()
        self.func = func
        self._maxsize = maxsize
        self.lock = RLock()
        self.on_evict = on_evict

    def _dispose(self, evicted: "list[tuple[tuple, V]]") -> None:
        on_evict = self.on_evict
        if on_evict is None:
            return
        for key, value in evicted:
            try:
                on_evict(key, value)
            except Exception:
                pass

    @property
    def maxsize(self):
        return self._maxsize

    @maxsize.setter
    def maxsize(self, maxsize: int | None):
        cache = self.cache
        evicted = []
        with self.lock:
            self._maxsize = maxsize
            while maxsize is not None and len(cache) > max(maxsize, 0):
                evicted.append(cache.popitem(last=False))
        self._dispose(evicted)

    def __call__(self, *args: K.args) -> V:
        cache = self.cache
        with self.lock:
            if args in cache:
                cache.move_to_end(args)
                return cache[args]
        result = self.func(*args)
        if self._maxsize is not None and self._maxsize <= 0:
            return result
        evicted = []
        with self.lock:
            if args in cache:
                # A concurrent miss stored its result first: keep that one,
                # so every caller shares a single value.
                existing = cache[args]
                cache.move_to_end(args)
                if existing is not result:
                    evicted.append((args, result))
                result = existing
            else:
                cache[args] = result
                if self._maxsize is not None and len(cache) > self._maxsize:
                    evicted.append(cache.popitem(last=False))
        self._dispose(evicted)
        return result

    def discard(self, *args: K.args) -> bool:
        """Drop the entry for `args` (passing it to `on_evict`) without
        recomputing it. Returns whether an entry was present."""
        with self.lock:
            if args not in self.cache:
                return False
            value = self.cache.pop(args)
        self._dispose([(args, value)])
        return True

    def invalidate(self, *args: K.args) -> V:
        self.discard(*args)
        return self(*args)

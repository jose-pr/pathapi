"""The idle connections of a transport: one per thread and server."""

from __future__ import annotations

import collections as _collections
import itertools as _itertools
import socket as _socket
import threading as _threading
import typing as _ty
import weakref as _weakref

MAX_IDLE_PER_THREAD = 8
"""Most idle connections one thread keeps open; opening another closes the one
used longest ago."""


class Connection:
    """An open `http.client.HTTPConnection` and what the pool knows of it."""

    __slots__ = ("http", "key", "scope", "released_at", "generation", "__weakref__")

    def __init__(self, http, key: tuple, scope: int, generation: int):
        self.http = http
        self.key = key
        self.scope = scope
        self.generation = generation
        self.released_at = 0.0

    def close(self, interrupt: bool = False) -> None:
        """Closes the socket. `interrupt` also shuts it down first, which wakes
        a thread blocked on it."""
        http = self.http
        sock = getattr(http, "sock", None)
        try:
            if interrupt and sock is not None:
                try:
                    sock.shutdown(_socket.SHUT_RDWR)
                except OSError:
                    pass
        finally:
            try:
                http.close()
            except Exception:
                pass


_SCOPE_IDS = _itertools.count(1)
_THREAD = _threading.local()
_LIVE_SCOPES: "set[int]" = set()
_POOLS: "_weakref.WeakSet[Pool]" = _weakref.WeakSet()


def _drop_scope(scope: int) -> None:
    _LIVE_SCOPES.discard(scope)
    for pool in list(_POOLS):
        pool.drop_scope(scope)


class _ThreadScope:
    """Held in the thread's own storage, so it is released when the thread ends
    and takes the thread's idle connections with it."""

    def __init__(self):
        self.id = next(_SCOPE_IDS)
        _LIVE_SCOPES.add(self.id)
        _weakref.finalize(self, _drop_scope, self.id).atexit = False


def scope_id() -> int:
    """The calling thread's scope. Not the thread identifier, which the
    operating system reuses for a later thread."""
    scope = getattr(_THREAD, "scope", None)
    if scope is None:
        scope = _THREAD.scope = _ThreadScope()
    return scope.id


class Pool:
    """Idle connections by (thread scope, server), and every connection a
    transport has open, so `close()` reaches the ones in use too. A connection
    is taken out of the pool for the length of a response and given back once
    the body has been read."""

    def __init__(self):
        self._lock = _threading.Lock()
        self._idle: "dict[int, _collections.OrderedDict[tuple, Connection]]" = {}
        self._open: "set[Connection]" = set()
        self._generation = 0
        _POOLS.add(self)

    @property
    def generation(self) -> int:
        return self._generation

    def take(self, key: tuple, scope: int) -> "Connection | None":
        """The idle connection of the calling thread to `key`, removed from the
        pool, or `None`."""
        with self._lock:
            return self._idle.get(scope, {}).pop(key, None)

    def register(self, connection: Connection) -> None:
        with self._lock:
            self._open.add(connection)

    def give_back(self, connection: Connection, now: float) -> None:
        """Keeps `connection` idle for the next request of its thread, or
        closes it when its thread has ended, the transport was closed since it
        was opened, or the thread already holds one to the same server."""
        connection.released_at = now
        evicted = []
        with self._lock:
            keep = (
                connection.scope in _LIVE_SCOPES
                and connection.generation == self._generation
            )
            if keep:
                idle = self._idle.setdefault(
                    connection.scope, _collections.OrderedDict()
                )
                if connection.key in idle:
                    keep = False
                else:
                    idle[connection.key] = connection
                    while len(idle) > MAX_IDLE_PER_THREAD:
                        evicted.append(idle.popitem(last=False)[1])
            if not keep:
                self._open.discard(connection)
                evicted.append(connection)
            for old in evicted:
                self._open.discard(old)
        for old in evicted:
            old.close()

    def discard(self, connection: Connection) -> None:
        """Closes a connection that is not to be used again."""
        with self._lock:
            self._open.discard(connection)
        connection.close()

    def drop_scope(self, scope: int) -> None:
        with self._lock:
            idle = self._idle.pop(scope, {})
            for connection in idle.values():
                self._open.discard(connection)
        for connection in list(idle.values()):
            connection.close()

    def close(self) -> None:
        """Closes every connection, idle or in use. Harmless when called again;
        a later request connects afresh."""
        with self._lock:
            self._generation += 1
            doomed = list(self._open)
            self._open.clear()
            self._idle.clear()
        for connection in doomed:
            connection.close(interrupt=True)


def close_pool(pool: Pool) -> None:
    pool.close()


def finalize_with(owner: object, pool: Pool) -> "_ty.Any":
    """Closes `pool` when `owner` is collected, so a transport that was never
    closed does not leave sockets for the interpreter to report."""
    handle = _weakref.finalize(owner, close_pool, pool)
    handle.atexit = False
    return handle

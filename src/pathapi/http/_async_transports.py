"""The async HTTP transports that exist, which one is the default, and the one
that runs a synchronous transport on worker threads."""

from __future__ import annotations

import asyncio as _asyncio
import os as _os
import threading as _threading
import typing as _ty

from ._async_response import AsyncHTTPResponse
from ._response import HTTPResponse
from ._transports import _SUGGESTABLE, _entries, default_transport

ASYNC_TRANSPORT_GROUP = "pathapi.http_async_transports"
"""The entry-point group of the async HTTP transports. An entry's name is the
transport's name and its value names a class that is built with no
arguments."""

ASYNC_TRANSPORT_ENV = "PATHAPI_HTTP_ASYNC_TRANSPORT"
"""Names the transport an `AsyncHTTPClient` builds for itself: `builtin`, or
the name of one that an installed distribution declares in
`ASYNC_TRANSPORT_GROUP`. Read when that transport is first needed; unset
means `builtin`."""

_BUILTIN = "builtin"


def _declared_async() -> "dict[str, _ty.Any]":
    return _entries(ASYNC_TRANSPORT_GROUP)


def async_transport_names() -> "tuple[str, ...]":
    """The names of the async HTTP transports, sorted: `builtin` and every
    entry of `ASYNC_TRANSPORT_GROUP` of an installed distribution. Nothing is
    imported."""
    return tuple(sorted({_BUILTIN, *_declared_async()}))


class _NotATransport(ValueError, ImportError):
    """No async transport answers to a name; also an `ImportError`, as for the
    synchronous registry."""


def _not_a_transport(subject: str, name: object) -> str:
    text = (
        f"{subject} is not an async HTTP transport "
        f"(expected one of {', '.join(map(repr, async_transport_names()))}); "
        f"an async transport registers in the entry-point group "
        f"{ASYNC_TRANSPORT_GROUP!r}"
    )
    if isinstance(name, str) and _SUGGESTABLE.fullmatch(name):
        text += (
            f": look for a distribution named {'pathapi-' + name.lower()!r} "
            "(a name to look for, not a known package)"
        )
    return text


def get_async_transport(name: str) -> type:
    """The class of the async transport called `name` (case-sensitive),
    loading its entry point. `builtin` is always `ThreadedTransport`: no entry
    point replaces it.

    Raises `ValueError` (also an `ImportError`) for any other name, naming the
    choices, the entry-point group and a distribution name to look for."""
    if name == _BUILTIN:
        return ThreadedTransport
    entry = _declared_async().get(name) if isinstance(name, str) else None
    if entry is not None:
        found = entry.load()
        if not callable(found):
            raise TypeError(f"the async HTTP transport {name!r} is not a class")
        return found
    raise _NotATransport(_not_a_transport(repr(name), name))


def default_async_transport():
    """A new async transport of the kind `PATHAPI_HTTP_ASYNC_TRANSPORT` names.
    A value that is empty, differently cased or unknown is `ValueError` (also
    an `ImportError`) naming the choices, the entry-point group and a
    distribution name to look for."""
    value = _os.environ.get(ASYNC_TRANSPORT_ENV)
    kind = _BUILTIN if value is None else value
    try:
        transport_class = get_async_transport(kind)
    except _NotATransport:
        raise _NotATransport(
            _not_a_transport(f"{ASYNC_TRANSPORT_ENV}={kind!r}", kind)
        ) from None
    return transport_class()


class _ThreadedBody:
    """The body of a streamed reply, read on a worker thread."""

    def __init__(self, response: HTTPResponse):
        self._response = response

    async def read(self, amount: int) -> bytes:
        return await _asyncio.to_thread(self._response._next, amount)

    async def aclose(self) -> None:
        await _asyncio.to_thread(self._response.close)


class ThreadedTransport:
    """An async transport that runs a synchronous one on worker threads: each
    `send()` and each read of a streamed body is one call of the synchronous
    transport on a thread of the event loop's default executor, so "async"
    here means an executor, not non-blocking sockets. Every synchronous
    transport can be used from an event loop through it.

    `transport` is the synchronous transport to run; without one the
    transport `PATHAPI_HTTP_TRANSPORT` names is built on first use and closed
    by `aclose()`. One that was given stays the caller's."""

    def __init__(self, transport: "_ty.Any" = None):
        if transport is not None and not callable(getattr(transport, "send", None)):
            raise TypeError("a transport is an object with a send() method")
        self._transport = transport
        self._built = None
        self._lock = _threading.Lock()

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    @property
    def transport(self):
        """The synchronous transport: the one given, or one built on first
        use."""
        with self._lock:
            if self._transport is None:
                self._built = self._transport = default_transport()
            return self._transport

    async def __aenter__(self) -> "ThreadedTransport":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Closes the synchronous transport this one built; one it was given
        stays the caller's. Harmless when called again."""
        with self._lock:
            built, self._built = self._built, None
            if built is not None:
                self._transport = None
        if built is not None:
            await _asyncio.to_thread(built.close)

    def _send(self, method, url, headers, body, timeout, stream):
        response = self.transport.send(
            method, url, headers=headers, body=body, timeout=timeout, stream=stream
        )
        if stream:
            source = _ThreadedBody(response)
        else:
            # `send(stream=False)` has read the body: it is in memory now.
            try:
                source = response.read()
            finally:
                response.close()
        return AsyncHTTPResponse(
            response.status,
            response.reason,
            response.headers,
            response.url,
            source,
            decode=False,
        )

    async def send(
        self,
        method: str,
        url: str,
        *,
        headers: "_ty.Mapping[str, str]",
        body: "bytes | _ty.BinaryIO | _ty.Iterable[bytes] | None" = None,
        timeout: "float | tuple | None",
        stream: bool = False,
    ) -> AsyncHTTPResponse:
        """One request, one response: `HTTPTransport.send()` run on a worker
        thread, with the same arguments and the same errors. A caller that is
        cancelled while the thread works gets its reply closed when it
        arrives."""
        loop = _asyncio.get_running_loop()
        work = loop.run_in_executor(
            None, self._send, method, url, headers, body, timeout, stream
        )
        try:
            return await _asyncio.shield(work)
        except _asyncio.CancelledError:
            work.add_done_callback(_close_late)
            raise


def _close_late(work) -> None:
    """Close the reply of a request whose caller is gone."""
    if work.cancelled() or work.exception() is not None:
        return
    source = work.result()._source
    if isinstance(source, _ThreadedBody):
        source._response.close()

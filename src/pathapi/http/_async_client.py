"""`AsyncHTTPClient`: `HTTPClient` for an event loop."""

from __future__ import annotations

import inspect as _inspect
import threading as _threading
import typing as _ty

from .. import __version__
from . import _async_follow, _follow, _settings
from ._async_response import AsyncHTTPResponse
from ._async_transports import default_async_transport
from ._client import _UNSET, DEFAULT_TIMEOUT
from ._deadline import Deadline, check_deadline
from ._headers import check_headers, check_url, has_header, merge_headers
from ._status import too_large


class AsyncHTTPClient:
    """The sibling of `HTTPClient` for an event loop: the same settings, the
    same redirect and credential rules and the same errors, with `request()`
    and `redirect()` as coroutines and the closer `aclose()`.

    `transport` is an object with `async send()` as `ThreadedTransport.send()`
    (a coroutine function, else `TypeError`); without one the client builds the
    transport that `PATHAPI_HTTP_ASYNC_TRANSPORT` names on first use (by
    default `ThreadedTransport`, which runs the synchronous transport that
    `PATHAPI_HTTP_TRANSPORT` names on worker threads) and closes it with
    `aclose()` or at the end of an `async with` block. A transport it was
    given stays the caller's. `headers`, `timeout`, `deadline` and `user_agent`
    mean what they mean for `HTTPClient`. The client never creates, closes or
    configures an event loop."""

    __slots__ = (
        "headers",
        "timeout",
        "deadline",
        "user_agent",
        "_transport",
        "_built",
        "_transport_lock",
        "__weakref__",
    )

    def __init__(
        self,
        transport: "_ty.Any" = None,
        *,
        headers: "_ty.Mapping[str, str] | None" = None,
        timeout: "float | tuple | None" = DEFAULT_TIMEOUT,
        deadline: "float | None" = None,
        user_agent: "str | None" = None,
    ):
        if transport is not None and not _inspect.iscoroutinefunction(
            getattr(transport, "send", None)
        ):
            raise TypeError("an async transport is an object with an async send()")
        self._transport = transport
        self._built = None
        self._transport_lock = _threading.Lock()
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.deadline = check_deadline(deadline)
        self.user_agent = user_agent or f"pathapi/{__version__}"

    def __repr__(self) -> str:
        # Headers are left out: they carry credentials.
        return f"{type(self).__name__}()"

    @property
    def transport(self):
        """The transport: the one the client was given, or one built on first
        use. Read once per request."""
        transport = self._transport
        if transport is None:
            with self._transport_lock:
                if self._transport is None:
                    self._built = self._transport = default_async_transport()
                transport = self._transport
        return transport

    async def aclose(self) -> None:
        """Closes the transport this client built for itself; one it was given
        stays the caller's. Harmless when called again."""
        with self._transport_lock:
            built, self._built = self._built, None
            if built is not None:
                self._transport = None
        if built is not None:
            await built.aclose()

    async def __aenter__(self) -> "AsyncHTTPClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    def _sent_headers(self, headers, auth) -> "dict[str, str]":
        base = {"User-Agent": self.user_agent, "Accept": "*/*"}
        sent = merge_headers(base, self.headers, headers)
        if auth is not None and not has_header(sent, "Authorization"):
            sent["Authorization"] = (
                auth if isinstance(auth, str) else _follow.basic_authorization(auth)
            )
        return sent

    async def _dispatch(
        self,
        method,
        url,
        sent,
        data,
        allow_redirects,
        stream,
        timeout,
        deadline,
        first=None,
    ) -> AsyncHTTPResponse:
        check_url(url)
        check_headers(sent)
        if timeout is _UNSET:
            timeout = self.timeout
        deadline = self.deadline if deadline is _UNSET else check_deadline(deadline)
        if deadline is not None:
            deadline = Deadline(deadline, f"{method} {_follow.shown(first or url)}")
        if isinstance(data, str):
            data = data.encode("utf-8")
        transport = self.transport
        # Always streamed from the transport, so that no reply is read whole
        # before it is known to be small enough.
        if method.upper() in _follow.FOLLOWED_METHODS and data is None:
            if allow_redirects is False and first is None:
                resp = await transport.send(
                    method,
                    url,
                    headers=sent,
                    timeout=timeout if deadline is None else deadline.cap(timeout),
                    stream=True,
                )
            else:
                resp = await _async_follow.send_following(
                    transport,
                    method,
                    url,
                    headers=sent,
                    timeout=timeout,
                    stream=True,
                    first=first,
                    deadline=deadline,
                )
        else:
            resp = await _async_follow.send_once(
                transport,
                method,
                url,
                headers=sent,
                body=data,
                timeout=timeout,
                stream=True,
                deadline=deadline,
            )
        if deadline is not None:
            deadline.bound_async(resp)
        if not stream:
            limit = _settings.MAX_REPLY_BYTES
            await resp._buffered(limit, too_large(_follow.shown(url), limit))
        return resp

    async def request(
        self,
        method: str,
        url: str,
        *,
        data=None,
        headers: "_ty.Mapping[str, str] | None" = None,
        stream: bool = False,
        allow_redirects: "bool | None" = None,
        timeout=_UNSET,
        deadline=_UNSET,
        auth: "tuple[str | bytes, str | bytes] | str | None" = None,
    ) -> AsyncHTTPResponse:
        """`HTTPClient.request()` as a coroutine, returning the final
        `AsyncHTTPResponse`. With `stream=False` the body is read before this
        returns; with `stream=True` the caller reads it, and closes the
        response."""
        sent = self._sent_headers(headers, auth)
        return await self._dispatch(
            method, url, sent, data, allow_redirects, stream, timeout, deadline
        )

    async def redirect(
        self,
        method: str,
        url: str,
        resp: AsyncHTTPResponse,
        *,
        headers: "_ty.Mapping[str, str] | None" = None,
        stream: bool = False,
        timeout=_UNSET,
        deadline=_UNSET,
        auth: "tuple[str | bytes, str | bytes] | str | None" = None,
    ) -> AsyncHTTPResponse:
        """`HTTPClient.redirect()` as a coroutine."""
        sent = self._sent_headers(headers, auth)
        location = _follow.redirect_location(url, resp)
        if location is None:
            raise _follow.refused_redirect(
                method, url, resp, None, "no usable Location"
            )
        return await self._dispatch(
            method, location, sent, None, None, stream, timeout, deadline, first=url
        )

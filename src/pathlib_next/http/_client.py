"""The client: a transport, the headers and timeout every request is sent with,
and the redirect rules above the transport."""

from __future__ import annotations

import threading as _threading
import typing as _ty

from .. import __version__
from . import _follow, _settings
from ._headers import check_headers, check_url, has_header, merge_headers
from ._response import HTTPResponse
from ._status import too_large
from ._transports import default_transport

DEFAULT_TIMEOUT = (10, 60)
"""`(connect, read)` timeout, in seconds, a client sends with every request
unless it is given `timeout` (`HTTPClient(timeout=...)` or per request);
`timeout=None` waits without a limit."""

_UNSET = object()
"""The `timeout` of a call that did not give one: the client's own."""


class HTTPClient:
    """A transport with the settings every request is sent with, and the rules
    for what happens after a redirect.

    `transport` is any object with a `send()` method as `HTTPTransport.send()`;
    without one the client builds the transport that `PATHLIB_NEXT_HTTP_TRANSPORT`
    names (the standard-library `HTTPTransport` by default) on first use and
    closes it with `close()` or at the end of a `with` block. A transport it
    was given stays the caller's. `headers` are sent with every request,
    request-specific headers merged over them by name, over a `User-Agent` of
    `user_agent` (`pathlib-next/<version>` by default) and `Accept: */*`.
    Every request gets `timeout=DEFAULT_TIMEOUT` (`(10, 60)` seconds) unless
    `timeout` here or in the call says otherwise (`None` waits without limit).

    The client, not the transport, follows redirects. `GET`, `HEAD` and
    `OPTIONS` follow up to 30; a redirect that changes the scheme, host or
    port drops `Authorization` (and nothing else). Any other method is sent
    without following: a 307 or 308 (for `PROPFIND` also a 301, 302 or 303) to
    the same scheme, host and port is sent once more with the same method and
    body, and any other 3xx, a redirect to another origin or a second redirect
    raises `OSError(EIO)`."""

    __slots__ = (
        "headers",
        "timeout",
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
        user_agent: "str | None" = None,
    ):
        if transport is not None and not callable(getattr(transport, "send", None)):
            raise TypeError("a transport is an object with a send() method")
        self._transport = transport
        self._built = None
        self._transport_lock = _threading.Lock()
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.user_agent = user_agent or f"pathlib-next/{__version__}"

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
                    self._built = self._transport = default_transport()
                transport = self._transport
        return transport

    def close(self) -> None:
        """Closes the transport this client built for itself; one it was given
        stays the caller's. Harmless when called again."""
        with self._transport_lock:
            built, self._built = self._built, None
            if built is not None:
                self._transport = None
        if built is not None:
            built.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _sent_headers(self, headers, auth) -> "dict[str, str]":
        """The headers every request starts from: the base ones, the client's,
        the call's, and the `Authorization` that `auth` stands for."""
        base = {"User-Agent": self.user_agent, "Accept": "*/*"}
        sent = merge_headers(base, self.headers, headers)
        if auth is not None and not has_header(sent, "Authorization"):
            sent["Authorization"] = (
                auth if isinstance(auth, str) else _follow.basic_authorization(auth)
            )
        return sent

    def _dispatch(
        self, method, url, sent, data, allow_redirects, stream, timeout, first=None
    ) -> HTTPResponse:
        check_url(url)
        check_headers(sent)
        if timeout is _UNSET:
            timeout = self.timeout
        if isinstance(data, str):
            data = data.encode("utf-8")
        transport = self.transport
        # Always streamed from the transport, so that no reply is read whole
        # before it is known to be small enough (see `stream` below).
        if method.upper() in _follow.FOLLOWED_METHODS and data is None:
            if allow_redirects is False and first is None:
                resp = transport.send(
                    method, url, headers=sent, timeout=timeout, stream=True
                )
            else:
                resp = _follow.send_following(
                    transport,
                    method,
                    url,
                    headers=sent,
                    timeout=timeout,
                    stream=True,
                    first=first,
                )
        else:
            resp = _follow.send_once(
                transport,
                method,
                url,
                headers=sent,
                body=data,
                timeout=timeout,
                stream=True,
            )
        if not stream:
            limit = _settings.MAX_REPLY_BYTES
            resp._buffered(limit, too_large(_follow.shown(url), limit))
        return resp

    def request(
        self,
        method: str,
        url: str,
        *,
        data=None,
        headers: "_ty.Mapping[str, str] | None" = None,
        stream: bool = False,
        allow_redirects: "bool | None" = None,
        timeout=_UNSET,
        auth: "tuple[str | bytes, str | bytes] | str | None" = None,
    ) -> HTTPResponse:
        """Send `method` to `url` and return the final response. A `data` that
        is not `bytes` (a file, an iterable) cannot be sent twice, so it is not
        re-sent after a redirect or a dropped connection.
        `allow_redirects=False` returns the first answer of a `GET`, `HEAD` or
        `OPTIONS` as it is. `auth` is a `(user, password)` pair sent as
        `Authorization: Basic`, or a `str` sent as the `Authorization` value
        (`"Bearer <token>"`), to the origin of `url` unless a header names an
        `Authorization`. `ValueError` for a URL (credentials in it included)
        or a header with a control character, before anything is sent.

        With `stream=False` the body is read before this returns, and a decoded
        body over `MAX_REPLY_BYTES` is `OSError(EFBIG)`; with `stream=True` the
        caller reads it, and closes the response."""
        sent = self._sent_headers(headers, auth)
        return self._dispatch(method, url, sent, data, allow_redirects, stream, timeout)

    def redirect(
        self,
        method: str,
        url: str,
        resp: HTTPResponse,
        *,
        headers: "_ty.Mapping[str, str] | None" = None,
        stream: bool = False,
        timeout=_UNSET,
        auth: "tuple[str | bytes, str | bytes] | str | None" = None,
    ) -> HTTPResponse:
        """Send `method` to where `resp`, the answer to a request for `url`,
        redirects to, and follow what it redirects to in turn. The request
        carries what `url` is sent with, except that `Authorization` is left
        off whenever it goes to another scheme, host or port than `url`'s. A
        redirect with no usable `Location` is `OSError(EIO)`."""
        sent = self._sent_headers(headers, auth)
        location = _follow.redirect_location(url, resp)
        if location is None:
            raise _follow.refused_redirect(
                method, url, resp, None, "no usable Location"
            )
        return self._dispatch(
            method, location, sent, None, None, stream, timeout, first=url
        )

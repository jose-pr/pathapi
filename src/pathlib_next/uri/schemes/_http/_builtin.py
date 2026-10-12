"""The built-in transport: HTTP/1.1 on `http.client`, with no third-party code."""

from __future__ import annotations

import errno as _errno
import http.client as _client
import select as _select
import socket as _socket
import ssl as _ssl
import threading as _threading
import time as _time
import typing as _ty

from . import _pool
from ._headers import (
    Target,
    bracket,
    check_body,
    check_headers,
    check_method,
    check_url,
    wants_decoding,
)
from ._proxies import ProxyAddress, proxies_for
from ._response import HTTPResponse

REUSE_WINDOW = 1.0
"""A request that cannot be sent again uses an idle connection only if the
connection carried a response within this many seconds; an older one may have
been dropped by the server, and the request would be lost with it."""

_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "PROPFIND"})

_now = _time.monotonic

_DEFAULT_CONTEXT: "_ssl.SSLContext | None" = None
_UNVERIFIED_CONTEXT: "_ssl.SSLContext | None" = None
_DEFAULT_CONTEXT_LOCK = _threading.Lock()


def _default_context() -> "_ssl.SSLContext":
    """One verifying context for every transport that is not given its own."""
    global _DEFAULT_CONTEXT
    with _DEFAULT_CONTEXT_LOCK:
        if _DEFAULT_CONTEXT is None:
            _DEFAULT_CONTEXT = _ssl.create_default_context()
        return _DEFAULT_CONTEXT


def _unverified_context() -> "_ssl.SSLContext":
    """One context that accepts any certificate, for `verify=False`."""
    global _UNVERIFIED_CONTEXT
    with _DEFAULT_CONTEXT_LOCK:
        if _UNVERIFIED_CONTEXT is None:
            context = _ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = _ssl.CERT_NONE
            _UNVERIFIED_CONTEXT = context
        return _UNVERIFIED_CONTEXT


def _failure(error: BaseException) -> BaseException:
    """`error` of the socket, TLS or `http.client` layer as the exception a
    transport raises: `TimeoutError`, `ConnectionError`, `ssl.SSLError`, or
    else `OSError(EIO)`. The text never holds a URL or a header."""
    if isinstance(error, (_socket.timeout, TimeoutError)):
        return TimeoutError(_errno.ETIMEDOUT, "the request timed out")
    if isinstance(error, _ssl.SSLError):
        if "timed out" in str(error):
            return TimeoutError(_errno.ETIMEDOUT, "the request timed out")
        return error
    if isinstance(error, _client.RemoteDisconnected):
        return ConnectionResetError(
            _errno.ECONNRESET, "the server closed the connection without a response"
        )
    if isinstance(error, ConnectionError):
        return error
    if isinstance(error, _socket.gaierror):
        return ConnectionError(
            _errno.EHOSTUNREACH, "the host name could not be resolved"
        )
    if isinstance(error, _client.NotConnected):
        return ConnectionAbortedError(_errno.ECONNABORTED, "the connection was closed")
    if isinstance(error, _client.IncompleteRead):
        return OSError(_errno.EIO, "the response ended before its stated length")
    if isinstance(error, _client.HTTPException):
        return OSError(
            _errno.EIO, f"the response is not valid HTTP ({type(error).__name__})"
        )
    return error


def _dropped(error: BaseException) -> bool:
    """Whether `error` says the server closed an idle connection (as opposed to
    a request that went wrong): nothing of a response had arrived."""
    return isinstance(
        error,
        (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            _ssl.SSLEOFError,
            _ssl.SSLZeroReturnError,
        ),
    )


def _unusable(sock) -> bool:
    """Whether an idle connection must not carry a request: the server closed
    it, or sent bytes nobody asked for, which the next reply would start with."""
    if sock is None:
        return True
    try:
        if getattr(sock, "pending", None) is not None and sock.pending():
            return True
        return bool(_select.select([sock], [], [], 0)[0])
    except (OSError, ValueError):
        return True


MAX_INTERIM_REPLIES = 8
"""Most interim (1xx) replies the transport skips before the reply it returns;
a server that sends more is not answering."""


def _skip_headers(fp) -> None:
    """Reads the header lines of an interim reply, up to its blank line."""
    for _ in range(100):
        line = fp.readline(65537)
        if len(line) > 65536:
            raise _client.LineTooLong("header line")
        if line in (b"\r\n", b"\n", b""):
            return
    raise _client.HTTPException("got more than 100 headers")


class _Reply(_client.HTTPResponse):
    """A response that goes past every interim (1xx) reply, not only `100`
    (`http.client` takes any other as the final one, and the real reply is then
    read as the answer to the next request), and at most `MAX_INTERIM_REPLIES`
    of them."""

    _interim = 0

    def _read_status(self):
        while True:
            version, status, reason = super()._read_status()
            if not 100 <= status < 200:
                return version, status, reason
            self._interim += 1
            if self._interim > MAX_INTERIM_REPLIES:
                raise _client.HTTPException("too many interim replies")
            if status == _client.CONTINUE:
                return version, status, reason  # `begin()` skips its headers
            _skip_headers(self.fp)


class _CannotConnect(Exception):
    """No connection could be made to the server or the proxy: the next proxy
    of the route is tried."""

    def __init__(self, error: BaseException):
        super().__init__()
        self.error = error


class _Dropped(Exception):
    """An idle connection the server had closed."""

    def __init__(self, error: BaseException):
        super().__init__()
        self.error = error


def _timeouts(timeout) -> "tuple[float | None, float | None]":
    if timeout is None:
        return None, None
    if isinstance(timeout, tuple):
        if len(timeout) != 2:
            raise ValueError("a timeout is a number, None or a (connect, read) pair")
        connect, read = timeout
    else:
        connect = read = timeout
    for value in (connect, read):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
        ):
            raise ValueError("a timeout is a positive number of seconds, or None")
    return connect, read


class _Payload:
    """The request body, and whether it can be sent twice."""

    def __init__(self, body):
        self.repeatable = check_body(body)
        self.value = body


class _Body:
    """The body of one response, read from its connection. The connection goes
    back to the pool once the body has been read to its end; one that is
    closed earlier, fails, or ends short is closed."""

    def __init__(self, owner, pool, connection, response, expected: "int | None"):
        # The transport stays alive while its reply is open: collecting it
        # would close the connection the reply is reading.
        self._owner = owner
        self._pool = pool
        self._connection = connection
        self._response = response
        self._expected = expected
        self._received = 0
        self._done = False
        if expected == 0:
            self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        # Closing the response closes its file object, not the socket; a
        # response left open would make `http.client` refuse the next request.
        self._response.close()
        if self._response.will_close:
            self._pool.discard(self._connection)
        else:
            self._pool.give_back(self._connection, _now())

    def _abort(self) -> None:
        if not self._done:
            self._done = True
            self._response.close()
            self._pool.discard(self._connection)

    def read(self, amount: int) -> bytes:
        if self._done:
            return b""
        try:
            chunk = self._response.read1(amount)
        except (OSError, _client.HTTPException) as error:
            self._abort()
            raise _failure(error) from None
        except BaseException:
            self._abort()
            raise
        if chunk:
            self._received += len(chunk)
            if self._response.isclosed():
                self._finish()
            return chunk
        if self._expected is not None and self._received < self._expected:
            self._abort()
            raise OSError(
                _errno.EIO,
                f"the response ended after {self._received} of {self._expected} bytes",
            )
        self._finish()
        return b""

    def close(self) -> None:
        self._abort()


class HTTPTransport:
    """Sends HTTP requests on the standard library: `http.client` over a socket,
    with TLS from `ssl`, and no third-party code.

    `send()` makes exactly one request: it follows no redirect, adds no
    credentials and retries only as described below. A transport is a context
    manager, and `close()` closes every connection it holds.

    `https:` servers are verified against `ssl_context`; without one, against
    `ssl.create_default_context()` (the system trust store). `verify=False`
    is the one way to turn verification off, and `ssl_context` wins over it.

    `proxies` decides which proxy a request uses. `None` reads the
    environment (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, through
    `urllib.request.getproxies()` and `proxy_bypass()`). Otherwise it is an
    object indexed with the request URL: it returns `None` (no proxy), one
    proxy, or an iterable of them to try in order when a connection cannot be
    made; `KeyError` means no proxy and the environment is not asked. A proxy
    is a URL or any object with a `url` attribute; a
    plain `dict` may use the keys `requests` uses (`"http"`, `"all"`, ...).
    Only `http://` proxies are supported (`CONNECT` for `https:`), with Basic
    credentials from the proxy URL; any other scheme is
    `OSError(EPROTONOSUPPORT)`. `{}` sends everything direct.

    Each thread keeps one idle connection per server (at most 8), which a later
    request reuses unless the server has closed it or sent bytes on it since;
    the connections of a thread are closed when it ends. A
    request that fails before any response byte on a reused connection because
    the server had closed it is sent once more on a new connection, but only
    if it is repeatable (`GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`,
    `PROPFIND`) and its body is `None` or in memory. Any other request reuses a
    connection only if it carried a response within the last second."""

    def __init__(
        self,
        *,
        ssl_context: "_ssl.SSLContext | None" = None,
        verify: bool = True,
        proxies: "_ty.Any" = None,
    ):
        self._ssl_context = ssl_context
        self._verify = bool(verify)
        self._proxies = proxies
        self._pool = _pool.Pool()
        self._finalizer = _pool.finalize_with(self, self._pool)

    def __repr__(self) -> str:
        return f"HTTPTransport(verify={self._verify})"

    def __enter__(self) -> "HTTPTransport":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """Closes every connection, idle or in use. Harmless when called again;
        a later request connects afresh."""
        self._pool.close()

    def _context(self) -> "_ssl.SSLContext":
        if self._ssl_context is not None:
            return self._ssl_context
        return _default_context() if self._verify else _unverified_context()

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: "_ty.Mapping[str, str]",
        body: "bytes | _ty.BinaryIO | _ty.Iterable[bytes] | None" = None,
        timeout: "float | tuple | None",
        stream: bool = False,
    ) -> HTTPResponse:
        """One request, one response.

        `headers` are sent as given; `Accept-Encoding: gzip, deflate` is added
        (and a coded body decoded) unless they name an `Accept-Encoding`.
        `timeout` is seconds for the connection and for each wait on the
        server: a number, a `(connect, read)` pair, or `None` to wait for ever.
        With `stream=False` the body has been read when this returns; with
        `stream=True` it is read from the response, which must be closed.

        Raises `ValueError` before anything is sent for a header or URL with
        a control character, a URL with credentials or another scheme;
        `TimeoutError`, `ConnectionError` and `ssl.SSLError` for the network;
        `OSError(EIO)` for a reply that is not valid HTTP or ends early."""
        method = check_method(method)
        target = check_url(url)
        pairs = check_headers(headers)
        connect_timeout, read_timeout = _timeouts(timeout)
        payload = _Payload(body)
        names = {name.lower() for name, _ in pairs}
        decode = wants_decoding(pairs)
        # As the server will see it: no fragment, "/" for an empty path.
        plain_url = f"{target.scheme}://{target.authority}{target.path}"
        route = proxies_for(self._proxies, plain_url, target.scheme, target.host)
        failure: "BaseException | None" = None
        for proxy in route:
            try:
                response = self._attempt(
                    proxy,
                    method,
                    target,
                    pairs,
                    names,
                    payload,
                    connect_timeout,
                    read_timeout,
                    plain_url,
                    decode,
                )
            except _CannotConnect as refused:
                failure = refused.error
                continue
            return response if stream else response._buffered()
        raise failure

    def _attempt(
        self,
        proxy: "ProxyAddress | None",
        method: str,
        target: Target,
        pairs,
        names,
        payload: _Payload,
        connect_timeout,
        read_timeout,
        url: str,
        decode: bool,
    ) -> HTTPResponse:
        key = (target.scheme, target.host, target.port, proxy.key if proxy else None)
        repeatable = method in _IDEMPOTENT and payload.repeatable
        scope = _pool.scope_id()
        connection = self._pool.take(key, scope)
        if (
            connection is not None
            and not repeatable
            and _now() - connection.released_at > REUSE_WINDOW
        ):
            self._pool.discard(connection)
            connection = None
        if connection is not None and _unusable(connection.http.sock):
            self._pool.discard(connection)
            connection = None
        reused = connection is not None
        while True:
            if connection is None:
                connection = self._open(
                    proxy, target, key, scope, connect_timeout, read_timeout
                )
                reused = False
            sock = connection.http.sock
            try:
                if sock is None:
                    raise ConnectionAbortedError(
                        _errno.ECONNABORTED, "the connection was closed"
                    )
                sock.settimeout(read_timeout)
            except OSError:
                # An idle socket that is already closed: nothing was sent.
                self._pool.discard(connection)
                if not reused:
                    raise
                connection, reused = None, False
                continue
            try:
                return self._exchange(
                    connection,
                    proxy,
                    method,
                    target,
                    pairs,
                    names,
                    payload,
                    url,
                    decode,
                    reused,
                )
            except _Dropped as dropped:
                if reused and repeatable:
                    connection, reused = None, False
                    continue
                raise dropped.error from None

    def _headers(
        self, proxy: "ProxyAddress | None", target: Target, pairs, names
    ) -> "dict[str, str]":
        sent = dict(pairs)
        if "host" not in names:
            sent["Host"] = target.authority
        if "accept-encoding" not in names:
            sent["Accept-Encoding"] = "gzip, deflate"
        if (
            proxy is not None
            and target.scheme == "http"
            and proxy.authorization
            and "proxy-authorization" not in names
        ):
            sent["Proxy-Authorization"] = proxy.authorization
        return sent

    def _exchange(
        self,
        connection,
        proxy,
        method,
        target,
        pairs,
        names,
        payload,
        url,
        decode,
        reused,
    ) -> HTTPResponse:
        http = connection.http
        if proxy is not None and target.scheme == "http":
            line = f"http://{target.authority}{target.path}"
        else:
            line = target.path
        try:
            http.request(
                method,
                line,
                body=payload.value,
                headers=self._headers(proxy, target, pairs, names),
            )
            response = http.getresponse()
        except (OSError, _client.HTTPException) as error:
            self._pool.discard(connection)
            if reused and _dropped(error):
                raise _Dropped(_failure(error)) from None
            raise _failure(error) from None
        except BaseException:
            self._pool.discard(connection)
            raise
        headers = response.getheaders()
        body = _Body(self, self._pool, connection, response, response.length)
        return HTTPResponse(
            response.status, response.reason, headers, url, body, decode=decode
        )

    def _open(self, proxy, target, key, scope, connect_timeout, read_timeout):
        host, port = (proxy.host, proxy.port) if proxy else (target.host, target.port)
        try:
            raw = _socket.create_connection((host, port), timeout=connect_timeout)
        except (OSError, _client.HTTPException) as error:
            raise _CannotConnect(_failure(error)) from None
        sock = raw
        try:
            raw.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
            if target.scheme == "https":
                if proxy is not None:
                    self._tunnel(raw, target, proxy)
                sock = self._context().wrap_socket(raw, server_hostname=target.host)
            sock.settimeout(read_timeout)
        except (OSError, _client.HTTPException) as error:
            raw.close()
            raise _failure(error) from None
        except BaseException:
            raw.close()
            raise
        http = _client.HTTPConnection(target.host, target.port)
        # A connection that was closed stays closed: `http.client` would
        # otherwise reconnect by itself, in plain TCP, outside the TLS session,
        # the proxy and the timeout this request was given.
        http.auto_open = 0
        http.response_class = _Reply
        http.sock = sock
        connection = _pool.Connection(http, key, scope, self._pool.generation)
        self._pool.register(connection)
        return connection

    @staticmethod
    def _tunnel(sock, target: Target, proxy: ProxyAddress) -> None:
        """Asks the proxy to connect `sock` to the server (RFC 9110 9.3.6)."""
        authority = f"{bracket(target.host)}:{target.port}"
        lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
        if proxy.authorization:
            lines.append(f"Proxy-Authorization: {proxy.authorization}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        reply = _client.HTTPResponse(sock, method="CONNECT")
        try:
            reply.begin()
            status = reply.status
        finally:
            reply.close()
        if status != 200:
            raise ConnectionRefusedError(
                _errno.ECONNREFUSED, f"the proxy refused CONNECT (HTTP {status})"
            )

"""`requests` as a transport, for what the built-in one does not do: SOCKS
proxies, `~/.netrc`, digest or NTLM authentication, cookies, mounted adapters."""

from __future__ import annotations

import errno as _errno
import http.client as _client
import ssl as _ssl
import threading as _threading
import typing as _ty

from .._extras import import_client
from ._follow import origin
from ._headers import (
    Headers,
    check_body,
    check_headers,
    check_method,
    check_timeout,
    check_url,
    drop_header,
    has_header,
    merge_headers,
    wants_decoding,
)
from ._response import HTTPResponse

_OWN_KEYS = frozenset(
    {"method", "url", "headers", "data", "timeout", "stream", "allow_redirects"}
)


class _OnlyThis:
    """Authentication that adds nothing of its own. `requests` would otherwise
    apply the session's `auth`, an `Authorization` among its default headers or
    `~/.netrc` to a request; those belong to one server, so a request to any
    other carries the `Authorization` it was given or none."""

    def __init__(self, authorization: "str | None"):
        self._authorization = authorization

    def __call__(self, request):
        request.headers.pop("Authorization", None)
        if self._authorization is not None:
            request.headers["Authorization"] = self._authorization
        return request


def _find(error: BaseException, kind: type) -> "BaseException | None":
    """An exception of `kind` among the causes of `error`, however deep the
    client libraries buried it."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, kind):
            return current
        pending.extend(
            nxt
            for nxt in (
                getattr(current, "reason", None),
                current.__cause__,
                current.__context__,
                *current.args,
            )
            if isinstance(nxt, BaseException)
        )
    return None


class _Body:
    """The raw (still encoded) body of a `requests` response."""

    def __init__(self, response, expected: "int | None"):
        self._response = response
        self._expected = expected
        self._received = 0

    def read(self, amount: int) -> bytes:
        raw = self._response.raw
        try:
            # urllib3 2 can return what has arrived; 1.x fills the amount.
            chunk = getattr(raw, "read1", raw.read)(amount)
        except Exception as error:
            raise _read_failure(error) from None
        if chunk:
            self._received += len(chunk)
            return chunk
        if self._expected is not None and self._received < self._expected:
            raise OSError(
                _errno.EIO,
                f"the response ended after {self._received} of {self._expected} bytes",
            )
        return b""

    def close(self) -> None:
        self._response.close()


def _read_failure(error: Exception) -> BaseException:
    """A failure while reading a body as the exception a transport raises."""
    name = type(error).__name__
    if name in ("ReadTimeoutError", "TimeoutError") or isinstance(error, TimeoutError):
        return TimeoutError(_errno.ETIMEDOUT, "the request timed out")
    if _find(error, ConnectionError) is not None:
        return ConnectionError("the connection failed while reading the response")
    if isinstance(error, OSError) and type(error).__module__ == "builtins":
        return error
    return OSError(_errno.EIO, f"the response could not be read ({name})")


def _header_pairs(response) -> "list[tuple[str, str]]":
    """The headers of `response`, a name sent twice as two pairs (`requests`
    joins them in its own mapping)."""
    raw = getattr(getattr(response, "raw", None), "headers", None)
    if hasattr(raw, "getlist"):
        return [(name, value) for name in raw for value in raw.getlist(name)]
    return list(response.headers.items())


class RequestsTransport:
    """Sends requests with a `requests.Session`.

    `session` is the session to use (the caller's: `close()` leaves it open),
    or `None` for a new one that `close()` closes. `requests_args` (`verify`,
    `cert`, `proxies`, `auth`, ...) are passed to every `session.request()`.
    `requests` is imported here and nowhere else; without it the constructor
    raises `ImportError` naming the `http` extra.

    The transport sends one request and follows no redirect. What the session
    holds for authentication (`auth`, an `Authorization` among its headers,
    `~/.netrc`) is used for the server of the first request only; a request to
    any other server carries just the `Authorization` it was given.

    Failures are `TimeoutError`, `ConnectionError`, `ssl.SSLError`, or
    `OSError(EIO)`; the text of a `requests` exception, which can quote a proxy
    URL with credentials, is never passed on."""

    def __init__(self, session: "_ty.Any" = None, **requests_args):
        self._requests = import_client("requests", "http")
        self._owns_session = session is None
        self.session = session if session is not None else self._requests.Session()
        self._args = requests_args
        self._pinned: "tuple | None" = None
        self._lock = _threading.Lock()

    def __repr__(self) -> str:
        return "RequestsTransport()"

    def __enter__(self) -> "RequestsTransport":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        """Closes the session if this transport made it. Harmless when called
        again."""
        if self._owns_session:
            self.session.close()

    def _failure(self, error) -> BaseException:
        exceptions = self._requests.exceptions
        if isinstance(error, exceptions.Timeout):
            return TimeoutError(_errno.ETIMEDOUT, "the request timed out")
        if isinstance(error, exceptions.SSLError):
            found = _find(error, _ssl.SSLError)
            return found if found is not None else _ssl.SSLError("TLS error")
        if isinstance(error, exceptions.ConnectionError):
            malformed = _find(error, _client.HTTPException)
            if malformed is not None and not isinstance(
                malformed, _client.RemoteDisconnected
            ):
                return OSError(
                    _errno.EIO,
                    f"the response is not valid HTTP ({type(malformed).__name__})",
                )
            return ConnectionError(f"connection failed ({type(error).__name__})")
        if isinstance(
            error,
            (
                exceptions.InvalidURL,
                exceptions.InvalidSchema,
                exceptions.MissingSchema,
                exceptions.InvalidHeader,
                exceptions.InvalidProxyURL,
            ),
        ):
            return ValueError(f"the request is not valid ({type(error).__name__})")
        return OSError(_errno.EIO, f"request failed ({type(error).__name__})")

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
        """One request, one response, as `HTTPTransport.send()`; `requests`'
        own proxies, trust store and authentication apply."""
        method = check_method(method)
        check_url(url)
        check_body(body)
        check_timeout(timeout)
        sent = dict(check_headers(headers))
        decode = wants_decoding(sent.items())
        if not has_header(sent, "Accept-Encoding"):
            sent["Accept-Encoding"] = "gzip, deflate"
        args = {k: v for k, v in self._args.items() if k not in _OWN_KEYS}
        ambient = self._args.get("headers")
        with self._lock:
            if self._pinned is None:
                self._pinned = origin(url)
            home = self._pinned == origin(url)
        if ambient:
            sent = merge_headers(
                ambient if home else drop_header(ambient, "Authorization"), sent
            )
        explicit = next(
            (v for k, v in sent.items() if k.lower() == "authorization"), None
        )
        if not home:
            args["auth"] = _OnlyThis(explicit)
        elif (
            explicit is not None
            and args.get("auth") is None
            and not getattr(self.session, "auth", None)
        ):
            args["auth"] = _OnlyThis(explicit)
        plain = url.split("#", 1)[0]
        try:
            response = self.session.request(
                method=method,
                url=plain,
                headers=sent,
                data=body,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                **args,
            )
        except self._requests.exceptions.RequestException as error:
            raise self._failure(error) from None
        except ValueError as error:
            # The URL and the headers were checked before this call, so this
            # is `requests` reading what the server said: a `Location` it
            # cannot parse, when it looks ahead to the redirect.
            raise OSError(
                _errno.EIO, f"the reply could not be read ({type(error).__name__})"
            ) from None
        status = response.status_code
        if status < 200:
            # An interim reply taken as the final one (urllib3 skips only
            # `100`); the real reply is still to come on that connection.
            response.close()
            raise OSError(
                _errno.EIO,
                f"the server's reply was an interim one (HTTP {status}); "
                "the built-in transport skips these",
            )
        length = response.headers.get("Content-Length", "")
        if method == "HEAD" or status in (204, 304):
            expected = 0
        elif "chunked" in response.headers.get("Transfer-Encoding", "").lower():
            expected = None
        else:
            expected = int(length) if length.isascii() and length.isdigit() else None
        result = HTTPResponse(
            response.status_code,
            response.reason,
            Headers(_header_pairs(response)),
            getattr(response, "url", None) or plain,
            _Body(response, expected),
            decode=decode,
        )
        return result if stream else result._buffered()

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import io as _io
import ipaddress as _ipaddress
import typing as _ty
import urllib.parse as _urlparse
import warnings as _warnings

from ... import providers as _providers
from .. import Source, UriPath
from ..source import compose_host
from ._http import HTTPResponse, RequestsTransport, TransportOwner
from ._http import _follow
from ._http._headers import check_headers as _check_headers
from ._http._headers import check_url as _check_url
from ._http._headers import merge_headers as _merge_headers
from ._http._headers import peer_text
from ._http._status import HTTPStatusError as _HTTPStatusError
from ._http._status import drop_body as _drop_body
from ._http._status import json_body as _json_body
from ._http._status import too_large as _too_large

DEFAULT_TIMEOUT = (10, 60)
"""`(connect, read)` timeout, in seconds, `RepoBackend` sends with every
request unless the caller supplies `timeout` (`RepoBackend(timeout=...)` or
per request); `timeout=None` waits without a limit."""

_UNSET = object()
"""The `timeout` of a call that did not give one: the backend's own."""


class InsecureTransportWarning(UserWarning):
    """A `RepoBackend` is about to send an `Authorization` header (its token,
    or one given in `headers=`) over plain `http://` to a host that is not
    this machine, where anyone on the route can read it."""


def _is_loopback(host: "str | None") -> bool:
    if not host:
        return False
    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _warn_if_plaintext(url: str, headers: dict) -> None:
    if not url.lower().startswith("http://"):
        return
    if not any(name.lower() == "authorization" for name in headers):
        return
    host = _urlparse.urlsplit(url).hostname
    if _is_loopback(host):
        return
    _warnings.warn(
        f"Sending an Authorization header over plain http to {host}; "
        "use an https API root",
        InsecureTransportWarning,
        stacklevel=3,
    )


def _rate_limit_error(response, path_obj) -> "OSError | None":
    """The EAGAIN `OSError` (a `BlockingIOError`) for a rate-limited reply,
    else None. Both hosts signal a primary limit with 403 or 429 and a
    `(X-)RateLimit-Remaining: 0` header, and a secondary limit with 403 or
    429 and a `Retry-After` header; a bare 429 is a limit too. Checked
    before the generic mapping, which read a secondary-limit 403 as
    PermissionError and a 429 as EIO."""
    if response is None or response.status not in (403, 429):
        return None
    headers = response.headers
    remaining = headers.get("X-RateLimit-Remaining", headers.get("RateLimit-Remaining"))
    retry_after = headers.get("Retry-After")
    if response.status == 403 and remaining != "0" and retry_after is None:
        return None
    if retry_after is not None:
        when = f"retry after {peer_text(retry_after, 40)}s"
    else:
        reset = headers.get("X-RateLimit-Reset", headers.get("RateLimit-Reset", "?"))
        when = f"resets at {peer_text(reset, 40)}"
    return OSError(
        _errno.EAGAIN,
        f"API rate limit exceeded (HTTP {response.status}) for {path_obj} ({when})",
    )


def _raise_for_status(resp: HTTPResponse) -> None:
    if resp.status >= 400:
        raise _HTTPStatusError(resp)


@_contextlib.contextmanager
def _translate_repo_errors(path_obj):
    # `from None`: a chained cause is printed by every traceback. The status
    # code or exception type stays in the message.
    try:
        yield
    except _HTTPStatusError as e:
        response = e.response
        status = response.status
        reason = peer_text(response.reason or "")
        limited = _rate_limit_error(response, path_obj)
        if limited is not None:
            raise limited from None
        if status == 404:
            raise FileNotFoundError(path_obj) from None
        elif status in (401, 403):
            raise PermissionError(path_obj) from None
        else:
            message = f"HTTP Error {status} {reason}".rstrip()
            raise OSError(_errno.EIO, f"{message} for {path_obj}") from None
    except TimeoutError as e:
        raise TimeoutError(f"Timeout for {path_obj} ({type(e).__name__})") from None
    except ConnectionError as e:
        raise ConnectionError(
            f"Connection error for {path_obj} ({type(e).__name__})"
        ) from None


def _reply_limit() -> int:
    """The most bytes of a reply the library reads whole: the limit a listing
    has (`http.MAX_LISTING_BYTES`), read now so that assigning it applies."""
    from . import http

    return http.MAX_LISTING_BYTES


class BaseRepoBackend(object):
    """Protocol for issuing an authenticated HTTP request against a
    GitHub/GitLab REST API. Subclass this to plug in custom auth/session
    handling; `RepoBackend` is the real transport-based implementation."""

    # Weakly referenceable so `UriPath` can record which backends a path
    # derived for itself (see `UriPath._supplied_backend()`).
    __slots__ = ("__weakref__",)

    @_providers.notimplemented
    def request(self, method, url, headers=None, params=None, **kwargs):
        """Send one request to the API and return its `HTTPResponse`."""


class RepoBackend(TransportOwner, BaseRepoBackend):
    """The transport and the optional bearer `token` sent on every request
    (mirrors `HttpBackend` in `http.py`). Unauthenticated works fine for
    public repos, subject to the host's anonymous rate limit (60 req/h for
    github.com).

    `transport` is any object with a `send()` method as
    `HTTPTransport.send()`; without one the built-in `HTTPTransport` is built
    on first use (see `HttpBackend`). `session=` (a `requests.Session`) or
    `requests_args` (`verify=`, `proxies=`, ...) select a `RequestsTransport`
    over it. `headers` go with every request, under the request's own.
    `api_base`, when set, overrides the scheme's convention-derived API root
    entirely (e.g. `https://api.github.com`/`https://{host}/api/v3`) -- the
    seam a test fake (or a reverse-proxied self-hosted setup) plugs into,
    since the real convention always forces `https`; a request that carries
    an `Authorization` header to a plain `http://` URL on a host other than
    loopback warns (`InsecureTransportWarning`). Every request gets
    `timeout=DEFAULT_TIMEOUT` (`(10, 60)` seconds) unless `timeout` here or
    in the call says otherwise; `None` waits without limit. A `GET` follows
    redirects as `HttpBackend` does, dropping the token when the host
    changes."""

    __slots__ = ("token", "api_base", "headers", "timeout", "cache")

    def __init__(
        self,
        token: str = None,
        session=None,
        api_base: str = None,
        *,
        transport=None,
        headers=None,
        timeout=DEFAULT_TIMEOUT,
        **requests_args,
    ):
        if session is not None or requests_args:
            if transport is not None:
                raise TypeError("give a transport, or a session and its arguments")
            transport = RequestsTransport(session, **requests_args)
        super().__init__(transport)
        self.token = token or None
        self.api_base = api_base.rstrip("/") if api_base else None
        self.headers = dict(headers or {})
        self.timeout = timeout
        # Generic per-backend memoization slot for schemes that need it
        # (GitLabPath's resolved default-branch lookup, see `_resolved_ref`)
        # -- shared across every path instance that shares this backend.
        self.cache = {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(api_base={self.api_base!r})"

    def request(
        self,
        method,
        url,
        headers=None,
        params=None,
        *,
        data=None,
        timeout=_UNSET,
        stream=False,
    ) -> HTTPResponse:
        sent = _merge_headers(
            {"User-Agent": "pathlib-next", "Accept": "*/*"}, self.headers, headers
        )
        if self.token:
            sent.setdefault("Authorization", f"Bearer {self.token}")
        _warn_if_plaintext(url, sent)
        if params:
            query = _urlparse.urlencode(
                [(k, v) for k, v in dict(params).items() if v is not None], doseq=True
            )
            if query:
                url = url + ("&" if "?" in url else "?") + query
        _check_url(url)
        _check_headers(sent)
        if timeout is _UNSET:
            timeout = self.timeout
        transport = self.transport
        # Streamed from the transport and, unless the caller asked to read it,
        # held in memory only up to the limit a listing has.
        if method.upper() in _follow.FOLLOWED_METHODS and data is None:
            resp = _follow.send_following(
                transport, method, url, headers=sent, timeout=timeout, stream=True
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
            limit = _reply_limit()
            resp._buffered(limit, _too_large(_follow.shown(url), limit))
        return resp


class _RepoApiPath(UriPath):
    """Shared base for `github:`/`gitlab:` (`<scheme>://host/owner/repo/path
    /in/repo?ref=<ref>`): read-only access to a git-hosting REST API. `ref`
    (branch/tag/SHA) is always optional, carried in the `?ref=` query string
    -- omitted, GitHub falls back server-side to the repo's default branch,
    while GitLab's file endpoints need an explicit ref, so `GitLabPath`
    resolves (and caches) the default branch once. `host` defaults to the
    public SaaS host; any other host is treated as a self-hosted instance.
    Auth: a bearer token via `with_backend(RepoBackend(token=...))`, or
    embedded in the URI as userinfo -- `<scheme>://user:TOKEN@host/...`
    (git's `x-access-token:TOKEN` / GitLab's `oauth2:TOKEN` convention: the
    password slot is the token) or a bare `<scheme>://TOKEN@host/...` --
    the backend kwarg wins if both are given. Because the user slot can be
    the secret here, `str()`, `repr()`, `as_uri(sanitize=True)` and so every
    exception message drop the WHOLE userinfo for these schemes (other
    schemes drop only the password); `as_uri()` still round-trips it.
    Read-only: write goes through a separate commits API entirely (out of
    scope, see `docs/divergences.md`)."""

    __slots__ = ()

    if _ty.TYPE_CHECKING:
        backend: BaseRepoBackend

    @classmethod
    def _format_parsed_parts(
        cls,
        source: Source,
        path: str,
        query: str,
        fragment: str,
        /,
        sanitize=True,
    ) -> str:
        # A bare `TOKEN@host` puts the secret in the user slot, which the
        # base sanitizing keeps -- drop the whole userinfo instead.
        if sanitize and source.userinfo:
            source = source._replace(userinfo=None)
        return super()._format_parsed_parts(
            source, path, query, fragment, sanitize=sanitize
        )

    def _initbackend(self):
        user, password = self.source.parsed_userinfo()
        return RepoBackend(token=password or user or None, timeout=DEFAULT_TIMEOUT)

    @property
    def _api_base(self) -> str:
        return getattr(self.backend, "api_base", None) or self._default_api_base()

    @_providers.notimplemented
    def _default_api_base(self) -> str:
        """The API root of this host when the backend names none."""

    def _bad_reply(self, what: str) -> OSError:
        return OSError(_errno.EIO, f"Unexpected API reply for {self}: {what}")

    def _decode(self, resp, *expected: type):
        """The JSON body of `resp`, which must be one of `expected` (`dict`,
        `list`): anything else -- a captive portal's page, a gateway's JSON,
        `null`, a truncated body -- is `OSError(EIO)` naming this path, so
        `exists()` answers `False` and `stat()` raises."""
        try:
            data = _json_body(resp, _reply_limit(), self)
        except ValueError:
            data = None
        if not isinstance(data, expected):
            kinds = " or ".join({dict: "object", list: "array"}[k] for k in expected)
            raise self._bad_reply(f"not a JSON {kinds}")
        return data

    def _size_of(self, entry: dict) -> int:
        """The `size` of an API object: a non-negative integer, `0` when it
        has none."""
        size = entry.get("size")
        if size is None:
            return 0
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            return size
        raise self._bad_reply(f"size {peer_text(repr(size), 60)}")

    def as_posix(self) -> str:
        # `user@host:path` would show the token when it is the user slot.
        host = self.source.host
        return f"{host}:{self.path}" if host else self.path

    def _api_quote(self, text: str, safe: str = "") -> str:
        """`text` as part of an API URL path: percent-encoded (`safe` stays
        bare), and never a `.` or `..` segment, which the client would
        resolve out of the repository's API root."""
        if any(part in (".", "..") for part in text.split("/")):
            raise ValueError(f"A dot segment cannot be part of an API URL: {self}")
        return _urlparse.quote(text, safe=safe, errors="surrogateescape")

    @property
    def owner(self) -> str:
        segments = self.segments
        return segments[1] if len(segments) > 1 else ""

    @property
    def repo(self) -> str:
        segments = self.segments
        return segments[2] if len(segments) > 2 else ""

    @property
    def repo_path(self) -> str:
        # A trailing "/" (`.../docs/`) names the same directory.
        return "/".join(self.segments[3:]).rstrip("/")

    def _api_authority(self) -> str:
        """`host[:port]` of this URI for a self-hosted API root, with an
        IPv6 literal bracketed."""
        authority = compose_host(self.source.host)
        if self.source.port:
            authority += f":{self.source.port}"
        return authority

    @property
    def ref(self) -> "str | None":
        from ..query import Query

        return Query(self.query or "").to_dict(single=True).get("ref") or None

    def _backend_cache(self) -> "dict | None":
        """The backend's memoization dict, or None for a custom
        `BaseRepoBackend` that has none (memoization is then skipped)."""
        cache = getattr(self.backend, "cache", None)
        return cache if isinstance(cache, dict) else None

    @staticmethod
    def _check_read_mode(mode: str) -> None:
        # `open()` hands `_open()` a canonical mode ("r", "r+", "w", ...);
        # anything but plain "r" would write, and writes are unsupported.
        if mode.replace("b", "").replace("t", "") != "r":
            raise NotImplementedError(f"open(mode={mode!r}): read-only scheme")

    @staticmethod
    def _reader(data: bytes) -> _ty.BinaryIO:
        # Read-only like a local file opened "rb": a write raises
        # io.UnsupportedOperation instead of landing in a detached buffer.
        return _io.BufferedReader(_io.BytesIO(data))

    def with_source(self, source: Source):
        if source and source.scheme == "git" and "git" not in type(self)._schemes():
            # `git:` re-dispatches by host; the base implementation would
            # construct `git:` with no host, which always raised ValueError.
            from .git._base import GitPath

            provider_cls = GitPath._provider_cls(source)
            inst = UriPath.__new__(provider_cls)
            backend, derived = self._backend_for(source)
            if backend is not None and isinstance(self, provider_cls):
                inst._backend, inst._backend_derived = backend, derived
            inst._init(source, self.path, self.query, self.fragment)
            return inst
        return super().with_source(source)

    def _make_child_relpath(self, name: str, **kwargs) -> _ty.Self:
        # The base `_make_child_relpath` resets query and fragment for a child, which would
        # drop `?ref=` on every iterdir()/glob()/walk() descendant and fall back to the
        # default branch instead of the ref asked for.
        inst = super()._make_child_relpath(name, **kwargs)
        if self.query:
            inst._query = self.query
        return inst

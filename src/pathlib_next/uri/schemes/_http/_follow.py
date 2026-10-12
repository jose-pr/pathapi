"""What a backend does above a transport: credentials, redirects and the
errors it raises for a redirect it will not follow."""

from __future__ import annotations

import base64 as _base64
import errno as _errno
import re as _re
import urllib.parse as _urlparse

from ._headers import drop_header
from ._response import HTTPResponse

MAX_REDIRECTS = 30
"""Most redirects a `GET`, `HEAD` or `OPTIONS` follows."""

FOLLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods whose redirects are followed. Every other method goes through
`send_once()`: following would turn a state-changing one into a different
request, and a PROPFIND into a GET or one without its body."""

RESENT_STATUSES = {"PROPFIND": (301, 302, 303, 307, 308)}
"""The redirect statuses after which a method is sent again, same method and
body, to a Location on the same scheme, host and port. PROPFIND only reads,
so it is repeated for every redirect; any other method only for 307 and 308,
which are defined to keep the method and the body."""

_REDIRECTS = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_RE_URL_SCHEME = _re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_DRAIN = 64 * 1024


def credential(text: str) -> "str | bytes":
    """The octets a percent-encoded userinfo part stands for (RFC 3986 2.1),
    as `str` when they are ASCII and as `bytes` otherwise."""
    octets = _urlparse.unquote_to_bytes(text)
    return octets.decode("ascii") if octets.isascii() else octets


def split_userinfo(url: str) -> "tuple[str, tuple[str|bytes, str|bytes] | None]":
    """Split the userinfo out of an absolute URL: `(url_without_userinfo,
    (user, password) | None)`, both parts percent-decoded to the octets they
    stand for. Credentials inside a request URL would end up in
    `HTTPResponse.url`, in error text and in redirect handling; they are sent
    in an `Authorization` header instead."""
    match = _RE_URL_SCHEME.match(url)
    if not match:
        return url, None
    start = match.end()
    end = len(url)
    for sep in "/?#":
        index = url.find(sep, start)
        if index != -1 and index < end:
            end = index
    userinfo, at, hostport = url[start:end].rpartition("@")
    if not at:
        return url, None
    user, _, password = userinfo.partition(":")
    auth = (credential(user), credential(password))
    return url[:start] + hostport + url[end:], (auth if any(auth) else None)


def basic_authorization(auth: "tuple[str | bytes, str | bytes]") -> str:
    """The `Authorization` value for Basic credentials (RFC 7617)."""
    user, password = (p.encode("utf-8") if isinstance(p, str) else p for p in auth)
    return "Basic " + _base64.b64encode(user + b":" + password).decode("ascii")


def origin(url: str) -> "tuple[str, str, int | None]":
    """The scheme, host and port a URL names."""
    split = _urlparse.urlsplit(url)
    scheme = split.scheme.lower()
    try:
        port = split.port
    except ValueError:
        port = -1
    return scheme, (split.hostname or ""), port or _DEFAULT_PORTS.get(scheme)


def shown(url: str) -> str:
    """`url` as an error message may print it: no userinfo, query or fragment,
    which can carry a credential."""
    try:
        split = _urlparse.urlsplit(url)
        netloc = split.netloc.rpartition("@")[2]
        return _urlparse.urlunsplit((split.scheme, netloc, split.path, "", ""))
    except ValueError:
        return "(unreadable URL)"


def is_redirect(resp: HTTPResponse) -> bool:
    return resp.status in _REDIRECTS and "Location" in resp.headers


def redirect_location(url: str, resp: HTTPResponse) -> "str | None":
    """`resp`'s `Location` as an absolute `http:`/`https:` URL without
    userinfo, or `None` when there is none or it cannot be used."""
    location = resp.headers.get("Location")
    if not location:
        return None
    try:
        # http.client decodes header bytes as Latin-1; servers send UTF-8.
        location = location.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in location):
        return None
    try:
        absolute = split_userinfo(_urlparse.urljoin(url, location))[0]
        if _urlparse.urlsplit(absolute).scheme.lower() not in _DEFAULT_PORTS:
            return None
        return absolute
    except ValueError:
        return None


def refused_redirect(method: str, url: str, resp: HTTPResponse, location, reason):
    status = f"HTTP {resp.status} {resp.reason or ''}".rstrip()
    where = shown(location) if location else "(none)"
    return OSError(
        _errno.EIO,
        f"{status} for {method} {shown(url)}: redirect to {where} "
        f"not followed ({reason})",
    )


def _discard(resp: HTTPResponse) -> None:
    """Gives up the rest of a body nobody wants, reading a small one so the
    connection can serve the next request."""
    try:
        resp.read(_DRAIN)
    except OSError:
        pass
    finally:
        resp.close()


def can_resend_body(body) -> bool:
    return body is None or isinstance(body, (bytes, bytearray, memoryview))


def send_following(
    transport,
    method: str,
    url: str,
    *,
    headers: "dict[str, str]",
    timeout,
    stream: bool,
) -> HTTPResponse:
    """Send a request that carries no body and follow its redirects, up to
    `MAX_REDIRECTS`. `Authorization` goes to the origin of the first URL only:
    a redirect that changes the scheme, host or port drops it for every later
    hop. A `303` becomes a `GET` (unless the request was a `HEAD`)."""
    first = origin(url)
    current = url
    hops = 0
    while True:
        resp = transport.send(
            method, current, headers=headers, body=None, timeout=timeout, stream=stream
        )
        if not is_redirect(resp):
            return resp
        location = redirect_location(current, resp)
        if location is None:
            return resp
        if hops >= MAX_REDIRECTS:
            _discard(resp)
            raise OSError(
                _errno.EIO,
                f"more than {MAX_REDIRECTS} redirects from {shown(url)}",
            )
        _discard(resp)
        if origin(location) != first:
            headers = drop_header(headers, "Authorization")
        if resp.status == 303 and method != "HEAD":
            method = "GET"
        current = location
        hops += 1


def send_once(
    transport,
    method: str,
    url: str,
    *,
    headers: "dict[str, str]",
    body,
    timeout,
    stream: bool,
) -> HTTPResponse:
    """Send a request that must not be redirected the way a browser does: a
    redirect with a status in `RESENT_STATUSES` to the same scheme, host and
    port is sent once more with the same method and body, and any other 3xx
    raises `OSError(EIO)` naming the status and the `Location`."""
    resp = transport.send(
        method, url, headers=headers, body=body, timeout=timeout, stream=stream
    )
    if not 300 <= resp.status < 400:
        return resp
    location = redirect_location(url, resp)
    resent = RESENT_STATUSES.get(method.upper(), (307, 308))
    if resp.status not in resent:
        reason = (
            "only "
            + ", ".join(map(str, resent[:-1]))
            + f" and {resent[-1]} send the request again"
        )
    elif location is None:
        reason = "no usable Location"
    elif origin(location) != origin(url):
        reason = "it leaves the scheme, host and port of the request"
    elif not can_resend_body(body):
        reason = "the request body cannot be sent twice"
    else:
        _discard(resp)
        resp = transport.send(
            method, location, headers=headers, body=body, timeout=timeout, stream=stream
        )
        if not 300 <= resp.status < 400:
            return resp
        url, location = location, redirect_location(location, resp)
        reason = "a second redirect"
    _discard(resp)
    raise refused_redirect(method, url, resp, location, reason)

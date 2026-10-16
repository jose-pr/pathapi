"""The HTTP transports that exist, which one is the default, and what a new one
starts from."""

from __future__ import annotations

import os as _os
import re as _re
import typing as _ty

from ._body import prepare_body as _prepare_body
from ._builtin import HTTPTransport
from ._headers import (
    check_body as _check_body,
    check_headers as _check_headers,
    check_method as _check_method,
    check_timeout as _check_timeout,
    check_url as _check_url,
    has_header as _has_header,
    wants_decoding as _wants_decoding,
)

TRANSPORT_GROUP = "pathapi.http_transports"
"""The entry-point group of the HTTP transports. An entry's name is the
transport's name and its value names a class that is built with
no arguments."""

TRANSPORT_ENV = "PATHAPI_HTTP_TRANSPORT"
"""Names the transport an `HTTPClient` builds for itself: `builtin`, or the
name of one that an installed distribution declares in `TRANSPORT_GROUP`.
Read when that transport is first
needed; unset means `builtin`."""

_BUILTIN = "builtin"

# What a distribution name may hold after `pathapi-`.
_SUGGESTABLE = _re.compile(r"[A-Za-z0-9][A-Za-z0-9._]*")

# The methods whose request says `Content-Length: 0` when it has no body.
_EXPECTING_BODY = frozenset({"PATCH", "POST", "PUT"})


def _entries(group: str) -> "dict[str, _ty.Any]":
    """The entry points of `group` by name, the first declaration of a name
    winning."""
    import importlib.metadata as _metadata

    try:
        found = _metadata.entry_points(group=group)
    except TypeError:  # Python 3.9
        found = _metadata.entry_points().get(group, ())
    declared: "dict[str, _ty.Any]" = {}
    for entry in found:
        declared.setdefault(entry.name, entry)
    return declared


def _declared() -> "dict[str, _ty.Any]":
    """The entry points of `TRANSPORT_GROUP` by name."""
    return _entries(TRANSPORT_GROUP)


def transport_names() -> "tuple[str, ...]":
    """The names of the HTTP transports, sorted: `builtin` and every entry of
    `TRANSPORT_GROUP` of an installed distribution. Nothing is imported."""
    return tuple(sorted({_BUILTIN, *_declared()}))


class _NotATransport(ValueError, ImportError):
    """No transport answers to a name. Also an `ImportError`, which is what a
    caller of earlier releases caught for a transport whose distribution was
    not installed."""


def _not_a_transport(subject: str, name: object) -> str:
    """The message for a name no transport answers to: the choices, the group
    a transport registers in and, for a name that can be part of one, the
    distribution name `pathapi-NAME` as a name to look for."""
    text = (
        f"{subject} is not an HTTP transport "
        f"(expected one of {', '.join(map(repr, transport_names()))}); "
        f"a transport registers in the entry-point group {TRANSPORT_GROUP!r}"
    )
    if isinstance(name, str) and _SUGGESTABLE.fullmatch(name):
        text += (
            f": look for a distribution named {'pathapi-' + name.lower()!r} "
            "(a name to look for, not a known package)"
        )
    return text


def get_transport(name: str) -> type:
    """The class of the transport called `name` (case-sensitive), loading its
    entry point. `builtin` is always `HTTPTransport`: no entry point replaces
    it.

    Raises `ValueError` (also an `ImportError`) for any other name, naming the
    choices, the entry-point group and a distribution name to look for."""
    if name == _BUILTIN:
        return HTTPTransport
    entry = _declared().get(name) if isinstance(name, str) else None
    if entry is not None:
        found = entry.load()
        if not callable(found):
            raise TypeError(f"the HTTP transport {name!r} is not a class")
        return found
    raise _NotATransport(_not_a_transport(repr(name), name))


def default_transport():
    """A new transport of the kind `PATHAPI_HTTP_TRANSPORT` names. A value
    that is empty, differently cased or unknown is `ValueError` (also an
    `ImportError`) naming the choices, the entry-point group and a distribution
    name to look for."""
    value = _os.environ.get(TRANSPORT_ENV)
    kind = _BUILTIN if value is None else value
    if kind == _BUILTIN:
        return HTTPTransport()
    try:
        transport_class = get_transport(kind)
    except _NotATransport:
        raise _NotATransport(
            _not_a_transport(f"{TRANSPORT_ENV}={kind!r}", kind)
        ) from None
    return transport_class()


class PreparedRequest(_ty.NamedTuple):
    """A request that passed the checks of the transport contract.

    `headers` hold text values, with the `Content-Length` of a body whose
    length is known (`0` for a `PATCH`, `POST` or `PUT` without a body) and an
    `Accept-Encoding` of `gzip, deflate` unless the caller named one. `body` is
    `None`, `bytes`, an object that sends exactly the `Content-Length` bytes
    when it is read or iterated, or an iterable of `bytes` to send chunked.
    `timeout` is `(connect, read)`. `decode` says whether the response decodes
    a `gzip` or `deflate` body: build it with `HTTPResponse(...,
    decode=decode)`."""

    method: str
    url: str
    headers: "dict[str, str]"
    body: _ty.Any
    timeout: "tuple[float | None, float | None]"
    decode: bool


def prepare_request(
    method: str,
    url: str,
    *,
    headers: "_ty.Mapping[str, str] | None",
    body: "bytes | _ty.BinaryIO | _ty.Iterable[bytes] | None" = None,
    timeout: "float | tuple | None",
) -> PreparedRequest:
    """The arguments of `send()` as the contract reads them.

    `method` is upper-cased. Raises `ValueError`, before anything is sent, for
    a method or header name that is not an HTTP token, a header value or URL
    with a control character, a URL with credentials or another scheme, a
    `Content-Length` that is not a count of bytes or exceeds the body, and a
    `timeout` that is not a positive number, `None` or a pair of them;
    `TypeError` for a `str` body. None of the messages quotes the URL or a
    header value. The fragment is dropped from `url`."""
    method = _check_method(method)
    _check_url(url)
    _check_body(body)
    timeout = _check_timeout(timeout)
    pairs = _check_headers(headers)
    sent = dict(pairs)
    body, length = _prepare_body(body, pairs)
    if length is not None:
        sent["Content-Length"] = str(length)
    elif not _has_header(sent, "Content-Length"):
        if isinstance(body, (bytes, bytearray, memoryview)):
            sent["Content-Length"] = str(memoryview(body).nbytes)
        elif body is None and method in _EXPECTING_BODY:
            sent["Content-Length"] = "0"
    decode = _wants_decoding(sent.items())
    if not _has_header(sent, "Accept-Encoding"):
        sent["Accept-Encoding"] = "gzip, deflate"
    return PreparedRequest(method, url.split("#", 1)[0], sent, body, timeout, decode)

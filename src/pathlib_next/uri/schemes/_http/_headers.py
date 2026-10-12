"""Headers and the checks a request passes before anything is sent."""

from __future__ import annotations

import collections.abc as _abc
import re as _re
import typing as _ty
import urllib.parse as _urlparse

_TOKEN = _re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_CONTROL = _re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_URL_CONTROL = _re.compile(r"[\x00-\x1f\x7f]")
_NOT_PRINTABLE = _re.compile(r"[^\x21-\x7e]")

_DEFAULT_PORTS = {"http": 80, "https": 443}


class Headers(_abc.Mapping):
    """The headers of a response: a read-only mapping whose names compare
    without regard to case. A name sent more than once reads as its values
    joined with `", "` (RFC 9110 5.3); `get_all()` returns them separately."""

    __slots__ = ("_pairs", "_joined", "_spelling")

    def __init__(self, pairs: "_ty.Iterable[tuple[str, str]] | _abc.Mapping" = ()):
        if isinstance(pairs, _abc.Mapping):
            pairs = pairs.items()
        self._pairs = tuple((str(name), str(value)) for name, value in pairs)
        grouped: "dict[str, list[str]]" = {}
        self._spelling: "dict[str, str]" = {}
        for name, value in self._pairs:
            key = name.lower()
            grouped.setdefault(key, []).append(value)
            self._spelling.setdefault(key, name)
        self._joined = {key: ", ".join(values) for key, values in grouped.items()}

    def __getitem__(self, name: str) -> str:
        return self._joined[name.lower()]

    def __iter__(self) -> "_ty.Iterator[str]":
        return iter(self._spelling.values())

    def __len__(self) -> int:
        return len(self._joined)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._joined

    def get_all(self, name: str) -> "list[str]":
        """Every value sent for `name`, in order."""
        key = name.lower()
        return [value for field, value in self._pairs if field.lower() == key]

    def __repr__(self) -> str:
        # Values are left out: a reply can carry cookies and tokens.
        return f"Headers({list(self._spelling.values())!r})"


def has_header(headers: "_abc.Mapping[str, str]", name: str) -> bool:
    key = name.lower()
    return any(field.lower() == key for field in headers)


def wants_decoding(pairs: "_ty.Iterable[tuple[str, str]]") -> bool:
    """Whether a response is decoded for a request with these headers: unless
    the request names a content coding of its own in `Accept-Encoding`
    (`identity` is not one, so a server that encodes anyway is still decoded)."""
    for name, value in pairs:
        if name.lower() == "accept-encoding":
            tokens = {part.split(";")[0].strip().lower() for part in value.split(",")}
            return tokens <= {"", "identity"}
    return True


def drop_header(headers: "_abc.Mapping[str, str]", name: str) -> "dict[str, str]":
    """`headers` as a new dict without `name`, whatever its case."""
    key = name.lower()
    return {field: value for field, value in headers.items() if field.lower() != key}


def merge_headers(*layers: "_abc.Mapping[str, str] | None") -> "dict[str, str]":
    """The layers laid over one another, a later one winning on a name whatever
    its case; a value of `None` removes the name."""
    merged: "dict[str, tuple[str, str]]" = {}
    for layer in layers:
        for name, value in (layer or {}).items():
            if value is None:
                merged.pop(name.lower(), None)
            else:
                merged[name.lower()] = (name, value)
    return dict(merged.values())


def check_method(method: str) -> str:
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise ValueError("the request method is not an HTTP token")
    return method.upper()


def check_body(body) -> bool:
    """Whether `body` (`None`, bytes, a binary file or an iterable of bytes)
    can be sent twice; `TypeError` for text or anything else."""
    if body is None or isinstance(body, (bytes, bytearray, memoryview)):
        return True
    if isinstance(body, str):
        raise TypeError("the request body is bytes, not text")
    if hasattr(body, "read"):
        return False
    if isinstance(body, (list, tuple)):
        return all(isinstance(piece, (bytes, bytearray)) for piece in body)
    if hasattr(body, "__iter__"):
        return False
    raise TypeError("the request body is bytes, a file or an iterable of bytes")


def check_headers(headers: "_abc.Mapping[str, str] | None") -> "list[tuple[str, str]]":
    """`headers` as `(name, value)` text pairs, or `ValueError` for a name that
    is not an HTTP token or a value with a control character, a line break
    included. The refused text is not quoted: it may be a credential."""
    pairs = []
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name):
            raise ValueError("a request header name is not an HTTP token")
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        elif isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError(f"the value of the {name} header is not text")
        if _CONTROL.search(value):
            raise ValueError(f"the value of the {name} header has a control character")
        try:
            value.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError(f"the value of the {name} header is not Latin-1") from None
        pairs.append((name, value))
    return pairs


class Target(_ty.NamedTuple):
    """Where a request goes and how it names itself on the wire."""

    scheme: str
    host: str  # IDNA, without brackets
    port: int
    path: str  # the origin-form request target, percent-encoded
    authority: str  # the Host header value


def _percent(match: "_re.Match") -> str:
    return "".join(
        f"%{byte:02X}" for byte in match.group().encode("utf-8", "surrogateescape")
    )


def bracket(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def check_url(url: str) -> Target:
    """The parts of an `http:`/`https:` URL a request is made of. A control
    character (CR and LF included), credentials in the URL, another scheme or
    a missing host are `ValueError`; the URL is not quoted in the message."""
    if not isinstance(url, str):
        raise ValueError("the request URL is not text")
    if _URL_CONTROL.search(url):
        raise ValueError("the request URL has a control character")
    try:
        parts = _urlparse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("the request URL cannot be parsed") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"the request URL has scheme {scheme!r}, not http or https")
    if "@" in parts.netloc:
        raise ValueError(
            "the request URL carries credentials; send them in an Authorization header"
        )
    host = parts.hostname
    if not host:
        raise ValueError("the request URL has no host")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(
                "the host of the request URL is not a valid name"
            ) from None
    port = port or _DEFAULT_PORTS[scheme]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    path = _NOT_PRINTABLE.sub(_percent, path)
    authority = bracket(host)
    if port != _DEFAULT_PORTS[scheme]:
        authority += f":{port}"
    return Target(scheme, host, port, path, authority)

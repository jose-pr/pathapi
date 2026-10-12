"""HTTP on a replaceable transport, for `http:`, `dav:`, `github:` and
`gitlab:`. Private: the public names are re-exported by
`pathlib_next.uri.schemes.http`."""

from __future__ import annotations

from ._builtin import HTTPTransport
from ._headers import Headers
from ._requests import RequestsTransport
from ._response import HTTPResponse
from ._select import TRANSPORT_ENV, TransportOwner, default_transport

__all__ = [
    "HTTPResponse",
    "HTTPTransport",
    "Headers",
    "RequestsTransport",
    "TRANSPORT_ENV",
    "TransportOwner",
    "default_transport",
]

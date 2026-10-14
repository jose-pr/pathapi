"""HTTP requests on the standard library, with a replaceable transport.

`HTTPClient` sends a request through a transport (an object with `send()`:
`HTTPTransport` on `http.client`, or one another distribution provides) and
follows redirects under one rule for credentials: `Authorization` goes to the
origin it was given for and to no other scheme, host or port. A reply comes
back as an `HTTPResponse`. `translate_errors()` maps a failed request to the
`OSError` types `pathlib` raises.

Importing this package loads `http.client` and `ssl`; `import pathlib_next`
does not. Nothing here needs a third-party library.
"""

from __future__ import annotations

import sys as _sys
import types as _types

from . import _settings
from ._builtin import HTTPTransport
from ._client import DEFAULT_TIMEOUT, HTTPClient
from ._errors import os_error, server_size, translate_errors
from ._follow import is_redirect, split_userinfo
from ._headers import Headers, peer_text
from ._response import HTTPResponse
from ._status import (
    HTTPStatusError,
    drop_body,
    json_body,
    raise_for_status,
    read_limited,
)
from ._transports import (
    TRANSPORT_ENV,
    TRANSPORT_GROUP,
    PreparedRequest,
    default_transport,
    get_transport,
    prepare_request,
    transport_names,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "HTTPClient",
    "HTTPResponse",
    "HTTPStatusError",
    "HTTPTransport",
    "Headers",
    "MAX_REPLY_BYTES",
    "PreparedRequest",
    "TRANSPORT_ENV",
    "TRANSPORT_GROUP",
    "default_transport",
    "drop_body",
    "get_transport",
    "is_redirect",
    "json_body",
    "os_error",
    "peer_text",
    "prepare_request",
    "raise_for_status",
    "read_limited",
    "server_size",
    "split_userinfo",
    "translate_errors",
    "transport_names",
]


class _Module(_types.ModuleType):
    """The module, with `MAX_REPLY_BYTES` read from and written to where it
    takes effect."""

    @property
    def MAX_REPLY_BYTES(self) -> int:
        return _settings.MAX_REPLY_BYTES

    @MAX_REPLY_BYTES.setter
    def MAX_REPLY_BYTES(self, value: int) -> None:
        _settings.MAX_REPLY_BYTES = value


_sys.modules[__name__].__class__ = _Module

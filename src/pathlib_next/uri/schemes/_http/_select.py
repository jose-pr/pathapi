"""Which transport a backend uses when it is not given one."""

from __future__ import annotations

import os as _os
import threading as _threading
import typing as _ty

from ._builtin import HTTPTransport
from ._requests import RequestsTransport

TRANSPORT_ENV = "PATHLIB_NEXT_HTTP_TRANSPORT"
"""Names the transport a backend builds for itself: `builtin` or `requests`.
Read when that transport is first needed; unset means `DEFAULT_TRANSPORT`."""

DEFAULT_TRANSPORT = "builtin"

CHOICES = ("builtin", "requests")


def default_transport():
    """A new transport of the kind `PATHLIB_NEXT_HTTP_TRANSPORT` names. A value
    that is empty, differently cased or unknown is `ValueError`."""
    value = _os.environ.get(TRANSPORT_ENV)
    kind = DEFAULT_TRANSPORT if value is None else value
    if kind == "builtin":
        return HTTPTransport()
    if kind == "requests":
        return RequestsTransport()
    raise ValueError(
        f"{TRANSPORT_ENV}={kind!r} is not an HTTP transport "
        f"(expected one of {', '.join(map(repr, CHOICES))})"
    )


class TransportOwner:
    """A backend's transport: the one it was given, or one built on first use
    that the backend closes. `transport` is read once per request."""

    __slots__ = ("_transport", "_built", "_transport_lock")

    def __init__(self, transport: "_ty.Any" = None):
        if transport is not None and not callable(getattr(transport, "send", None)):
            raise TypeError("a transport is an object with a send() method")
        self._transport = transport
        self._built = None
        self._transport_lock = _threading.Lock()

    @property
    def transport(self):
        transport = self._transport
        if transport is None:
            with self._transport_lock:
                if self._transport is None:
                    self._built = self._transport = default_transport()
                transport = self._transport
        return transport

    def close(self) -> None:
        """Closes the transport this backend built for itself; one it was given
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

"""FTP and FTPS paths on the standard library alone (`ftplib`).

`FTPPath` is a `Path` for a directory tree on an FTP server and needs no URI;
`FTPBackend` holds the connections to one server and account.
"""

from __future__ import annotations

from ._backend import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    IDLE_PROBE_SECONDS,
    BaseFTPBackend,
    FTPBackend,
    FTPEndpoint,
)
from ._path import FTPPath

__all__ = [
    "BaseFTPBackend",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "FTPBackend",
    "FTPEndpoint",
    "FTPPath",
    "IDLE_PROBE_SECONDS",
]

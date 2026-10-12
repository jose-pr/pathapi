from __future__ import annotations

import threading as _thread
import typing as _ty
import weakref as _weakref

import netimps as _netimps

from ...ftp import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT
from ...ftp import IDLE_PROBE_SECONDS as IDLE_PROBE_SECONDS
from ...ftp import BaseFTPBackend, FTPBackend, FTPPath
from ...utils import notimplemented as _notimplemented
from .. import Source, Uri
from ._delegate import DelegatingPath

if _ty.TYPE_CHECKING:
    import ssl as _ssl


class BaseFtpBackend(object):
    """Protocol for obtaining a connected+logged-in FTP client (or
    its TLS variant) for a `Source`. Subclass this to plug in custom connection
    handling (e.g. tests mock it directly, no real server);
    `FtpBackend` is the real implementation. A path to each endpoint
    (`Source` and `tls`) runs on a core `pathlib_next.ftp.BaseFTPBackend`
    that asks `client()` for its connections."""

    # Weakly referenceable so `UriPath` can record which backends a path
    # derived for itself (see `UriPath._supplied_backend()`).
    __slots__ = ("__weakref__",)

    @_notimplemented
    def client(self, source: Source, tls: bool) -> "_ty.Any":
        """The connected, logged-in client for `source`; `tls` asks for `ftps:`."""


class FtpBackend(BaseFtpBackend):
    """Connects via the stdlib FTP client (`ftp:`) or its TLS variant
    (`ftps:`, with `PROT P` for an encrypted data channel too).

    `timeout` (seconds, default `DEFAULT_TIMEOUT` = 30) bounds connect,
    every reply and every transfer read; `None` blocks forever.

    `ftps:` verifies the server certificate and host name by default
    (`ssl.create_default_context()`), before `USER`/`PASS` are sent. To
    trust a private CA or a self-signed certificate, pass your own
    `ssl_context` (e.g. `ssl.create_default_context(cafile=...)`). To turn
    verification off entirely -- accepting any certificate, so anyone on
    the network path can read the password -- pass `verify=False`.
    `ssl_context` wins over `verify` when both are given. Data connections
    reuse the control connection's TLS session."""

    __slots__ = ("timeout", "ssl_context", "verify")

    def __init__(
        self,
        timeout: "float | None" = DEFAULT_TIMEOUT,
        ssl_context: "_ssl.SSLContext | None" = None,
        verify: bool = True,
    ) -> None:
        self.timeout = timeout
        self.ssl_context = ssl_context
        self.verify = verify

    def client(self, source: Source, tls: bool):
        user, password = source.parsed_userinfo()
        return FTPBackend(
            str(source.host),
            source.port or _netimps.get_default_port("ftp"),
            user=user,
            password=password,
            tls=tls,
            timeout=self.timeout,
            ssl_context=self.ssl_context,
            verify=self.verify,
        ).client()


_DEFAULT_BACKEND = FtpBackend()
"""The backend every `FtpPath` built without `backend=` shares, so separately
constructed paths to one server reuse one connection per thread instead of
opening (and keeping) one each."""


class _EndpointBackend(BaseFTPBackend):
    """The core backend of one endpoint of a `BaseFtpBackend`: its connections
    are the ones the owner's `client(source, tls)` opens, and its endpoint is
    the `Source`, so paths to one server through one owner share them."""

    __slots__ = ("owner", "source", "tls", "endpoint")

    def __init__(self, owner: BaseFtpBackend, source: Source, tls: bool):
        self.owner = owner
        self.source = source
        self.tls = tls
        self.endpoint = source

    def client(self):
        return self.owner.client(self.source, self.tls)


_ENDPOINTS: "_weakref.WeakValueDictionary[tuple, _EndpointBackend]" = (
    _weakref.WeakValueDictionary()
)
_ENDPOINTS_LOCK = _thread.Lock()


def _endpoint_backend(owner: BaseFtpBackend, source: Source, tls: bool):
    """The one core backend for `owner`, `source` and `tls`, while any path or
    cached connection holds it. The owner is held by the entry, so its `id`
    names it for as long as the entry lives."""
    key = (id(owner), source, tls)
    with _ENDPOINTS_LOCK:
        found = _ENDPOINTS.get(key)
        if found is None:
            found = _ENDPOINTS[key] = _EndpointBackend(owner, source, tls)
        return found


class FtpPath(DelegatingPath):
    """`ftp:`/`ftps:` scheme: full read/write access via the stdlib FTP client,
    done by a `pathlib_next.ftp.FTPPath` on the connections of `backend`
    (a thread-keyed LRU cache; a thread's connections close when it ends). A
    listing prefers MLSD (RFC 3659 -- type/size/modify facts in one round
    trip) and a stat MLST of the entry itself where FEAT lists it, else its
    parent's MLSD; servers that don't support them fall back to NLST for
    listing (names only) and SIZE/CWD for stat (no portable "not found vs. is
    a directory" distinction in that path)."""

    __SCHEMES = ("ftp", "ftps")
    __slots__ = ()
    _DELEGATED = (
        "_scandir",
        "_listdir",
        "_open",
        "_mkdir",
        "unlink",
        "rmdir",
        "chmod",
        "_utime",
    )

    if _ty.TYPE_CHECKING:
        backend: BaseFtpBackend

    def _initbackend(self):
        return _DEFAULT_BACKEND

    def _make_core(self) -> FTPPath:
        source = self.source
        backend = _endpoint_backend(self.backend, source, source.scheme == "ftps")
        return FTPPath(self.path or "/", backend=backend)

    def rename(self, target: "FtpPath | Uri | str"):
        # A plain str target is a sibling rename (relative to self's
        # parent), matching sftp.py's rename() semantics.
        target = self._rename_target(target)
        destination = self.with_path(target.path)
        core = self._core_path
        moved = core.with_segments(target.path or "/")
        with self._delegated((str(moved), str(destination))):
            core.rename(moved)
        # pathlib returns the new path.
        return destination

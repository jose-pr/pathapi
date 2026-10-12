from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import ftplib as _ftplib
import itertools as _itertools
import ssl as _ssl
import threading as _thread
import time as _time
import typing as _ty
import weakref as _weakref

from .._lru import LRU as _LRU
from .._stubs import notimplemented as _notimplemented

DEFAULT_PORT = 21
"""The control port `FTPBackend` connects to when it is not given one. `tls`
asks for explicit FTPS on that same port; port 990 is never assumed."""

DEFAULT_TIMEOUT = 30.0
"""Socket timeout, in seconds, `FTPBackend` applies to connect, replies and
transfers when the caller does not pass one."""

IDLE_PROBE_SECONDS = 1.0
"""A cached control connection that carried a command within this many seconds
is used as it is. One that has been idle longer, or has not carried a command
yet, is probed with `NOOP` first and replaced if the server dropped it.
Servers close idle sessions after tens of seconds at the least, so a run of
operations pays no probe and a pause long enough to lose the session pays one."""


class FTPEndpoint(_ty.NamedTuple):
    """The server and account a backend connects to: what two backends must
    share for their paths to name the same files. The password is not part of
    it."""

    host: str
    port: int
    user: "str | None"
    tls: bool


class BaseFTPBackend(object):
    """Contract for obtaining a connected, logged-in `ftplib.FTP` (or
    `FTP_TLS`). Subclass this to plug in custom connection handling (tests
    mock it directly, no real server); `FTPBackend` is the real
    implementation. `endpoint`, when a subclass sets it, is a hashable value
    naming the server and account: two backends with equal endpoints serve
    the same files."""

    # Weakly referenceable so a path can be told which backends it built.
    __slots__ = ("__weakref__",)

    endpoint: "_ty.Hashable | None" = None

    @_notimplemented
    def client(self) -> "_ftplib.FTP":
        """A new connected, logged-in client."""


class _SessionReuseFTP_TLS(_ftplib.FTP_TLS):
    """`FTP_TLS` whose data connections resume the control connection's TLS
    session. Stdlib `ntransfercmd` wraps the data socket without
    `session=`, and servers such as vsftpd (`require_ssl_reuse=YES`, the
    default) and FileZilla Server reject such a data connection with
    `522`. With TLS 1.3 the session ticket arrives after the handshake; by
    the first transfer the login replies have been read, so it is there."""

    def ntransfercmd(self, cmd, rest=None):
        # Skip FTP_TLS.ntransfercmd (it wraps without `session=`).
        conn, size = super(_ftplib.FTP_TLS, self).ntransfercmd(cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=getattr(self.sock, "session", None),
            )
        return conn, size


class FTPBackend(BaseFTPBackend):
    """The connections to one FTP server, as one account: stdlib
    `ftplib.FTP`, or `ftplib.FTP_TLS` with `tls=True` (explicit FTPS on the
    control port, `PROT P` for an encrypted data channel too).

    Nothing connects when the backend is built. Each thread opens a control
    connection on its first use and keeps it, so paths that share a backend
    share a connection per thread (`close()` ends them, and so does the end
    of the thread). `timeout` (seconds, default `DEFAULT_TIMEOUT` = 30)
    bounds connect, every reply and every transfer read; `None` blocks
    forever. Without `user` the login is `anonymous`.

    `tls=True` verifies the server certificate and host name by default
    (`ssl.create_default_context()`), before `USER`/`PASS` are sent. To
    trust a private CA or a self-signed certificate, pass your own
    `ssl_context` (e.g. `ssl.create_default_context(cafile=...)`). To turn
    verification off entirely -- accepting any certificate, so anyone on
    the network path can read the password -- pass `verify=False`.
    `ssl_context` wins over `verify` when both are given. Data connections
    reuse the control connection's TLS session.

    A context manager: leaving the block calls `close()`."""

    __slots__ = (
        "host",
        "port",
        "user",
        "password",
        "tls",
        "timeout",
        "ssl_context",
        "verify",
    )

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        user: "str | None" = None,
        password: "str | None" = None,
        tls: bool = False,
        timeout: "float | None" = DEFAULT_TIMEOUT,
        ssl_context: "_ssl.SSLContext | None" = None,
        verify: bool = True,
    ) -> None:
        if not host:
            raise ValueError("an FTP backend needs a host")
        self.host = str(host)
        self.port = DEFAULT_PORT if port is None else int(port)
        self.user = user
        self.password = password
        self.tls = bool(tls)
        self.timeout = timeout
        self.ssl_context = ssl_context
        self.verify = verify

    @property
    def endpoint(self) -> FTPEndpoint:
        return FTPEndpoint(self.host, self.port, self.user, self.tls)

    def __repr__(self) -> str:
        shown = [repr(self.host), str(self.port)]
        if self.user is not None:
            shown.append(f"user={self.user!r}")
        if self.tls:
            shown.append("tls=True")
        return f"{type(self).__name__}({', '.join(shown)})"

    def __reduce__(self):
        return (
            _rebuild_backend,
            (
                type(self),
                self.host,
                self.port,
                self.user,
                self.password,
                self.tls,
                self.timeout,
                self.ssl_context,
                self.verify,
            ),
        )

    def __enter__(self) -> "FTPBackend":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _tls_context(self) -> "_ssl.SSLContext":
        if self.ssl_context is not None:
            return self.ssl_context
        context = _ssl.create_default_context()
        if not self.verify:
            context.check_hostname = False
            context.verify_mode = _ssl.CERT_NONE
        return context

    def client(self) -> "_ftplib.FTP":
        if self.tls:
            client = _SessionReuseFTP_TLS(
                context=self._tls_context(), timeout=self.timeout
            )
        else:
            client = _ftplib.FTP(timeout=self.timeout)
        try:
            client.connect(self.host, self.port)
            client.login(self.user or "anonymous", self.password or "")
            if self.tls:
                client.prot_p()
            client.set_pasv(True)
        except BaseException:
            # A rejected certificate or a timed-out greeting must not leave
            # the half-open control socket behind.
            close = getattr(client, "close", None)
            if close is not None:
                close()
            raise
        return client

    def close(self) -> None:
        """Closes every control connection this backend holds, whichever
        thread opened it; one a command is running on closes when that
        command ends. Harmless when called again; a later use connects
        afresh."""
        _drop_backend(self)


def _rebuild_backend(cls, host, port, user, password, tls, timeout, context, verify):
    return cls(
        host,
        port,
        user=user,
        password=password,
        tls=tls,
        timeout=timeout,
        ssl_context=context,
        verify=verify,
    )


_now = _time.monotonic


class _Session:
    """What is known of one cached connection: when it last answered, whether
    the server offers `MLST` and `MFMT`, and whether a command is running on
    it. A connection dropped from the cache while a command runs on it is
    closed when that command ends."""

    __slots__ = ("last_used", "mlst", "mfmt", "busy", "retired")

    def __init__(self):
        self.last_used: "float | None" = None
        self.mlst: "bool | None" = None
        self.mfmt: "bool | None" = None
        self.busy = 0
        self.retired = False


_SESSION_ATTR = "_pathlib_next_session"
_SESSION_LOCK = _thread.Lock()


def session_of(client) -> "_Session | None":
    session = getattr(client, _SESSION_ATTR, None)
    if session is None:
        session = _Session()
        try:
            setattr(client, _SESSION_ATTR, session)
        except AttributeError:
            return None  # a client that takes no attributes is always probed
    return session


def _create_ftpclient(backend: BaseFTPBackend, endpoint: object, scope: int):
    return backend.client()


def _close_ftpclient(key: tuple, client: "_ftplib.FTP") -> None:
    session = getattr(client, _SESSION_ATTR, None)
    if session is not None:
        with _SESSION_LOCK:
            if session.busy:
                # Dropped from the cache while a command is running on it
                # (another thread's LRU overflow): its user closes it.
                session.retired = True
                return
    client.close()


_THREAD_SCOPE = _thread.local()
_SCOPE_IDS = _itertools.count(1)


def _drop_scope(scope: int) -> None:
    with _CACHED_CLIENTS.lock:
        keys = [key for key in _CACHED_CLIENTS.cache if key[2] == scope]
    for key in keys:
        _CACHED_CLIENTS.discard(*key)


def _drop_backend(backend: BaseFTPBackend) -> None:
    with _CACHED_CLIENTS.lock:
        keys = [key for key in _CACHED_CLIENTS.cache if key[0] is backend]
    for key in keys:
        _CACHED_CLIENTS.discard(*key)


class _ThreadScope:
    """Held in the thread's own storage, so it is released when the thread
    ends and takes the thread's cached connections with it."""

    def __init__(self):
        self.id = next(_SCOPE_IDS)
        _weakref.finalize(self, _drop_scope, self.id).atexit = False


def _scope_id() -> int:
    """The calling thread's key in `_CACHED_CLIENTS`. Not the thread
    identifier, which the operating system reuses for a later thread."""
    scope = getattr(_THREAD_SCOPE, "scope", None)
    if scope is None:
        scope = _THREAD_SCOPE.scope = _ThreadScope()
    return scope.id


# Keyed by (backend, endpoint, thread scope): ftplib clients are not
# thread-safe. Evicted and discarded clients are closed (`_close_ftpclient`),
# not left logged in, and a thread's clients are dropped when it ends.
_CACHED_CLIENTS = _LRU(_create_ftpclient, maxsize=128, on_evict=_close_ftpclient)


def cache_key(backend: BaseFTPBackend) -> tuple:
    return (backend, backend.endpoint, _scope_id())


def cached_session(backend: BaseFTPBackend) -> "_Session | None":
    """The session record of the calling thread's cached connection to
    `backend`, or None when there is none."""
    client = _CACHED_CLIENTS.cache.get(cache_key(backend))
    return None if client is None else session_of(client)


def live_client(backend: BaseFTPBackend) -> "_ftplib.FTP":
    """The calling thread's connection to `backend`: the cached one when it
    carried a command within `IDLE_PROBE_SECONDS`, else after a `NOOP` that
    finds it alive, else a new one."""
    key = cache_key(backend)
    client = _CACHED_CLIENTS(*key)
    session = session_of(client)
    if (
        session is not None
        and session.last_used is not None
        and _now() - session.last_used <= IDLE_PROBE_SECONDS
    ):
        return client
    try:
        client.voidcmd("NOOP")
    except (
        OSError,
        EOFError,
        _ftplib.error_temp,
        _ftplib.error_proto,
        _ftplib.error_reply,
    ):
        # Replaced and closed (`_close_ftpclient`), not abandoned.
        client = _CACHED_CLIENTS.invalidate(*key)
    return client


@_contextlib.contextmanager
def wired(backend: BaseFTPBackend, label: str):
    """The calling thread's live client of `backend`, for one command or
    transfer; `label` names the path in the errors raised.

    A complete error reply (`error_perm`/`error_temp`) leaves the control
    channel in step and passes through for the caller to translate.
    Anything else raised while the client is in use -- a dropped
    connection, an unexpected reply, a KeyboardInterrupt or decode error
    in the middle of a transfer whose final reply is still queued --
    leaves it out of step, so the client is dropped from the cache and
    closed; the next operation reconnects. Protocol errors and EOF are
    re-raised as ConnectionError."""
    key = cache_key(backend)
    try:
        client = live_client(backend)
    except _ftplib.error_perm as error:
        # A refused login is not a refusal for this path: it must not
        # reach the caller's probes and read as "no such file".
        raise PermissionError(
            _errno.EACCES, f"FTP login refused: {error}", label
        ) from error
    except (_ftplib.Error, EOFError) as error:
        raise ConnectionError(
            _errno.ECONNREFUSED, f"FTP connection failed: {error!r}", label
        ) from error
    session = session_of(client)
    if session is not None:
        with _SESSION_LOCK:
            session.busy += 1
    try:
        yield client
        if session is not None:
            session.last_used = _now()
    except (_ftplib.error_perm, _ftplib.error_temp):
        # A complete reply: the connection is in step.
        if session is not None:
            session.last_used = _now()
        raise
    except BaseException as error:
        with _CACHED_CLIENTS.lock:
            cached = _CACHED_CLIENTS.cache.get(key) is client
            if cached:
                _CACHED_CLIENTS.discard(*key)
        if not cached:
            client.close()
        if isinstance(error, (_ftplib.Error, EOFError)):
            raise ConnectionError(
                _errno.ECONNABORTED, f"FTP session failed: {error!r}", label
            ) from error
        if isinstance(error, UnicodeDecodeError):
            raise OSError(
                _errno.EILSEQ,
                "FTP listing is not valid "
                f"{getattr(client, 'encoding', 'utf-8')}: {error}",
                label,
            ) from error
        raise
    finally:
        if session is not None:
            with _SESSION_LOCK:
                session.busy -= 1
                closing = session.retired and not session.busy
            if closing:
                client.close()

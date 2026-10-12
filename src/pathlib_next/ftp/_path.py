from __future__ import annotations

import errno as _errno
import ftplib as _ftplib
import io as _io
import posixpath as _posix
import threading as _thread
import time as _time
import types as _types
import typing as _ty
import urllib.parse as _urlparse
import weakref as _weakref

from .._tree import TreePath
from ..utils import as_mode as _as_mode
from ..utils import is_safe_child_name as _is_safe_child_name
from ..utils._commit import CommitOnClose
from .._filestat import FileStat
from ._backend import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    BaseFTPBackend,
    FTPBackend,
    cached_session,
    live_client,
    session_of,
    wired,
)
from ._facts import (
    PERMISSION_WORDS,
    UNSUPPORTED_REPLIES,
    facts_to_filestat,
    is_child_entry,
    lists_feature,
    mlst_facts,
    reply_code,
)


class _Namespace:
    """What two paths share when they resolve their names on one server: the
    object `_node_key()` hands out for an endpoint."""

    __slots__ = ("endpoint", "__weakref__")

    def __init__(self, endpoint):
        self.endpoint = endpoint


_NAMESPACES: "_weakref.WeakValueDictionary[object, _Namespace]" = (
    _weakref.WeakValueDictionary()
)
_NAMESPACES_LOCK = _thread.Lock()


def _namespace_of(backend: BaseFTPBackend) -> object:
    endpoint = backend.endpoint
    if endpoint is None:
        return backend
    with _NAMESPACES_LOCK:
        found = _NAMESPACES.get(endpoint)
        if found is None:
            found = _NAMESPACES[endpoint] = _Namespace(endpoint)
        return found


def _rebuild_path(cls, text, backend):
    return cls(text, backend=backend)


class FTPPath(TreePath):
    """A path on an FTP server, read and written through stdlib `ftplib`,
    with a thread-keyed connection cache (`FTPBackend`; a thread's connections
    close when it ends). A listing prefers MLSD (RFC 3659 -- type/size/modify
    facts in one round trip) and a stat MLST of the entry itself where FEAT
    lists it, else its parent's MLSD; servers that don't support them fall
    back to NLST for listing (names only) and SIZE/CWD for stat (no portable
    "not found vs. is a directory" distinction in that path).

    `FTPPath("/pub/x", host="h", user="u", tls=True)` builds the path's own
    `FTPBackend`; `backend=` shares one connection cache between paths built
    separately, or plugs a `BaseFTPBackend` of your own. A path joined from
    another keeps its backend. Names are read from the server root: `pub/x` and
    `/pub/x` are one file."""

    __slots__ = ("_backend",)

    def __init__(
        self,
        *segments: "str | FTPPath",
        backend: "BaseFTPBackend | None" = None,
        host: "str | None" = None,
        port: int = DEFAULT_PORT,
        user: "str | None" = None,
        password: "str | None" = None,
        tls: bool = False,
        timeout: "float | None" = DEFAULT_TIMEOUT,
        ssl_context: "object | None" = None,
        verify: bool = True,
    ):
        text, donor = self._join(segments)
        endpoint = (
            host is not None
            or user is not None
            or password is not None
            or ssl_context is not None
            or tls
            or port != DEFAULT_PORT
            or timeout != DEFAULT_TIMEOUT
            or verify is not True
        )
        if backend is not None and endpoint:
            raise TypeError(
                "pass either backend= or the endpoint keywords "
                "(host=, port=, user=, ...), not both"
            )
        if backend is None:
            if endpoint:
                if host is None:
                    raise TypeError(
                        "host= is required with the other endpoint keywords"
                    )
                backend = FTPBackend(
                    host,
                    port,
                    user=user,
                    password=password,
                    tls=tls,
                    timeout=timeout,
                    ssl_context=ssl_context,
                    verify=verify,
                )
            elif donor is not None:
                backend = donor._backend
            else:
                raise TypeError("an FTPPath needs host= or backend=")
        self._segments = self._parse(text)
        self._backend = backend

    def _rebuild(self, text: str) -> "FTPPath":
        return type(self)(text, backend=self._backend)

    def __reduce__(self):
        # The text and the backend; a backend pickles without its
        # connections, and with the password only if it was given one.
        return _rebuild_path, (type(self), self.as_posix(), self._backend)

    def __repr__(self) -> str:
        backend = self._backend
        shown = [repr(self.as_posix())]
        if isinstance(backend, FTPBackend):
            shown += [f"host={backend.host!r}", f"port={backend.port}"]
            if backend.user is not None:
                shown.append(f"user={backend.user!r}")
            if backend.tls:
                shown.append("tls=True")
        else:
            shown.append(f"backend={backend!r}")
        return f"{type(self).__name__}({', '.join(shown)})"

    @property
    def backend(self) -> BaseFTPBackend:
        """The `FTPBackend` (or custom `BaseFTPBackend`) holding the
        connections of this path."""
        return self._backend

    def as_uri(self) -> str:
        backend = self._backend
        host = getattr(backend, "host", None)
        if host is None:
            raise NotImplementedError("as_uri() needs an FTPBackend")
        netloc = f"[{host}]" if ":" in host else host
        if backend.user is not None:
            netloc = f"{_urlparse.quote(backend.user, safe='')}@{netloc}"
        if backend.port != DEFAULT_PORT:
            netloc = f"{netloc}:{backend.port}"
        scheme = "ftps" if backend.tls else "ftp"
        return f"{scheme}://{netloc}{_urlparse.quote(self._wirepath, safe='/')}"

    def _same_filesystem(self, other: "FTPPath") -> bool:
        # Equality ignores the server: two paths are one tree when they share a
        # backend or name the same host, port, account and security.
        mine, theirs = self._backend, other._backend
        if mine is theirs:
            return True
        endpoint = mine.endpoint
        return endpoint is not None and endpoint == theirs.endpoint

    def _node_key(self):
        # "x", "/x" and "/d/../x" are one node. This reads no server, so `..`
        # is applied to the text.
        posix = "/".join(self.segments).lstrip("/")
        names = _posix.normpath("/" + posix).removeprefix("/").split("/")
        return _namespace_of(self._backend), tuple(name for name in names if name)

    @property
    def _wirepath(self) -> str:
        """This path as a command argument: rooted, and `/` for the root. An
        empty argument would name the session's working directory instead."""
        return "/" + "/".join(name for name in self.segments if name)

    @property
    def _ftpclient(self) -> "_ftplib.FTP":
        return live_client(self._backend)

    def _wire(self):
        """The calling thread's live client, for one command or transfer (see
        `_backend.wired`)."""
        return wired(self._backend, str(self))

    def _call(self, method: str, *args):
        """`client.<method>(*args)` through `_wire()`. Only `error_perm` is
        left for the caller; a transient `error_temp` becomes OSError.

        `FTP.mlsd()` is a generator that runs its transfer while it is
        iterated, so a listing is read whole here, inside the guard: a
        failure half way through it evicts the client like any other."""
        try:
            with self._wire() as client:
                result = getattr(client, method)(*args)
                if isinstance(result, _types.GeneratorType):
                    result = list(result)
                return result
        except _ftplib.error_temp as error:
            raise OSError(_errno.EAGAIN, str(error), str(self)) from error

    def _translate(self, error: "_ftplib.error_perm", wrong_type=None) -> OSError:
        """OSError for a refused command on this path. A reply alone cannot
        say why: a read-only login gets "550 Not enough privileges" for a
        missing file too. So the path is stat'ed: missing is
        FileNotFoundError; `wrong_type(stat)` may name a type mismatch
        (IsADirectoryError, ...); anything else is PermissionError."""
        try:
            st = self._fresh_stat()
        except FileNotFoundError:
            return FileNotFoundError(
                _errno.ENOENT, f"No such file or directory ({error})", str(self)
            )
        except OSError:
            st = None
        if st is not None and wrong_type is not None:
            mismatch = wrong_type(st)
            if mismatch is not None:
                return mismatch
        return PermissionError(_errno.EACCES, str(error), str(self))

    def _mlsd_entry(self):
        """This entry's MLSD facts from its parent's listing, or None if
        not found, the parent can't be listed (a name in it the client cannot
        decode included), or the server doesn't support MLSD."""
        try:
            for name, facts in self._call("mlsd", self.parent._wirepath):
                if name == self.name and is_child_entry(name, facts):
                    return facts
        except _ftplib.error_perm:
            return None
        except OSError as error:
            if error.errno == _errno.EILSEQ:
                return None
            raise
        return None

    def _offers(self, feature: str) -> bool:
        """Whether the server of this path's connection lists `feature` in its
        `FEAT` reply, asked once per connection and remembered on its
        session."""
        slot = feature.lower()
        try:
            with self._wire() as client:
                session = session_of(client)
                if session is not None and getattr(session, slot) is not None:
                    return getattr(session, slot)
                send = getattr(client, "sendcmd", None)
                offered = False
                if send is not None:
                    try:
                        offered = lists_feature(send("FEAT"), feature)
                    except _ftplib.error_perm:
                        pass  # 500/502: the server has no FEAT
                if session is not None:
                    setattr(session, slot, offered)
                return offered
        except _ftplib.error_temp:
            return False  # not remembered: the next call asks again

    def _stat_entry(self):
        """This entry's facts, asked of the server in the fewest replies it
        allows: `MLST` of the entry itself where `FEAT` offers it (RFC 3659
        7.2), else its parent's `MLSD` listing. None when the server has no
        such entry, or will not say."""
        if self._offers("MLST"):
            try:
                reply = self._control(f"MLST {self._wirepath}")
            except _ftplib.error_perm:
                return None
            facts = None if reply is None else mlst_facts(reply)
            if facts is not None:
                return facts
            # An MLST that answers without an entry is no use on this
            # connection: use the listing, and do not ask again.
            session = cached_session(self._backend)
            if session is not None:
                session.mlst = False
        return self._mlsd_entry()

    def _control(self, command: str) -> "str | None":
        """The reply to a raw `command` on the control connection, or None for
        a client that cannot send one. `error_perm` is left for the caller; a
        transient `error_temp` becomes OSError."""
        try:
            with self._wire() as client:
                send = getattr(client, "sendcmd", None)
                return None if send is None else send(command)
        except _ftplib.error_temp as error:
            raise OSError(_errno.EAGAIN, str(error), str(self)) from error

    def _not_a_directory(self, st) -> "OSError | None":
        if st.is_dir():
            return None
        return NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))

    def _is_a_directory(self, st) -> "OSError | None":
        if not st.is_dir():
            return None
        return IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))

    def _scandir(self):
        # MLSD's facts carry type, size and modify for every child in one round trip; NLST
        # (names only) is the fallback. The listing is read whole before anything is
        # yielded, so no transfer is left half-read.
        try:
            listing = [
                (name, facts_to_filestat(facts))
                for name, facts in self._call("mlsd", self._wirepath)
                if is_child_entry(name, facts)
            ]
            if (
                len(listing) == 1
                and listing[0][0] == self.name
                and not listing[0][1].is_dir()
            ):
                # RFC 3659 has MLSD of a file refused (501), but a server
                # that lists the file itself instead looks the same as a
                # directory holding one same-named file: only a stat tells.
                mismatch = self._not_a_directory(self._fresh_stat())
                if mismatch is not None:
                    raise mismatch
        except _ftplib.error_perm as error:
            if reply_code(error) not in UNSUPPORTED_REPLIES:
                # A missing path, a file, or a refused listing.
                raise self._translate(error, self._not_a_directory) from error
            try:
                names = self._call("nlst", self._wirepath)
            except _ftplib.error_perm as error:
                raise self._translate(error, self._not_a_directory) from error
            listing = [
                (base, None)
                for base in (name.rsplit("/", 1)[-1] for name in names)
                if _is_safe_child_name(base)
            ]
            if len(listing) <= 1:
                # Servers answer NLST of a missing path with an empty list,
                # and of a file with the file's own name.
                mismatch = self._not_a_directory(self._fresh_stat())
                if mismatch is not None:
                    raise mismatch
        yield from listing

    def iterdir(self):
        for name, _stat in self._scandir():
            yield self.with_segments(*self.segments, name)

    def stat(self, *, follow_symlinks=True):
        return self._fresh_stat()

    def _fresh_stat(self) -> FileStat:
        # Special case: the FTP root '/' has no parent to MLSD and SIZE won't
        # work on a directory.  Confirm it exists via CWD.
        if self._wirepath == "/":
            try:
                self._call("cwd", "/")
                return FileStat(is_dir=True)
            except _ftplib.error_perm as error:
                raise FileNotFoundError(str(self)) from error
        facts = self._stat_entry()
        if facts is not None:
            return facts_to_filestat(facts)
        # MLST/MLSD unsupported, the parent unlistable, or the entry not there. SIZE answers
        # for a file (in binary mode: servers refuse it in ASCII), CWD for a directory (every
        # path here is absolute, so the changed working directory affects nothing).
        try:
            self._call("voidcmd", "TYPE I")
            size = self._call("size", self._wirepath)
        except _ftplib.error_perm:
            size = None
        if size is not None:
            return FileStat(st_size=size, is_dir=False)
        try:
            self._call("cwd", self._wirepath)
        except _ftplib.error_perm as error:
            raise FileNotFoundError(str(self)) from error
        return FileStat(is_dir=True)

    def _open(self, mode="r", buffering=-1):
        if mode in ("r", "r+"):
            buf = _io.BytesIO()
            try:
                self._call("retrbinary", f"RETR {self._wirepath}", buf.write)
            except _ftplib.error_perm as error:
                raise self._translate(error, self._is_a_directory) from error
            if mode == "r+":
                # Read-modify-write: what is written is uploaded on close.
                return self._upload_on_close("STOR", initial=buf.getvalue())
            buf.seek(0)
            # Read-only, like a local file opened "rb": a write raises
            # instead of landing in a buffer nobody uploads.
            return _io.BufferedReader(buf)
        if mode not in ("w", "x", "a"):
            raise NotImplementedError(f"open(mode={mode!r})")
        if mode == "x" and self.exists():
            raise FileExistsError(str(self))
        return self._upload_on_close("APPE" if mode == "a" else "STOR")

    def _upload_on_close(self, cmd: str, initial: "bytes | None" = None):
        """A write buffer that uploads with `cmd` (STOR/APPE) when it is
        closed. The connection is looked up then, not when the file is opened:
        a write that outlasts the server's idle timeout still lands, on a
        reconnected session."""

        def upload(buffer):
            buffer.seek(0)
            self._store(cmd, buffer)

        return CommitOnClose(upload, initial)

    def _store(self, cmd: str, fileobj) -> None:
        """Upload `fileobj` with STOR/APPE on a live connection."""
        try:
            self._call("storbinary", f"{cmd} {self._wirepath}", fileobj)
        except _ftplib.error_perm as error:
            raise self._create_error(error) from error

    def _create_error(self, error: "_ftplib.error_perm") -> OSError:
        """OSError for a refused STOR/MKD of this path: an existing
        directory, a missing or non-directory parent, or a refusal."""
        try:
            if self._fresh_stat().is_dir():
                return IsADirectoryError(_errno.EISDIR, "Is a directory", str(self))
        except FileNotFoundError:
            pass
        except OSError:
            return PermissionError(_errno.EACCES, str(error), str(self))
        parent = self.parent
        if parent != self:
            try:
                parent_stat = parent._fresh_stat()
            except FileNotFoundError:
                return FileNotFoundError(
                    _errno.ENOENT, f"No such file or directory ({error})", str(self)
                )
            except OSError:
                parent_stat = None
            if parent_stat is not None and not parent_stat.is_dir():
                return NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
        return PermissionError(_errno.EACCES, str(error), str(self))

    def _mkdir(self, mode):
        try:
            self._call("mkd", self._wirepath)
        except _ftplib.error_perm as error:
            if self.exists():
                raise FileExistsError(
                    _errno.EEXIST, "File exists", str(self)
                ) from error
            raise self._create_error(error) from error

    def unlink(self, missing_ok=False):
        try:
            self._call("delete", self._wirepath)
        except _ftplib.error_perm as error:
            translated = self._translate(error, self._is_a_directory)
            if missing_ok and isinstance(translated, FileNotFoundError):
                return
            raise translated from error

    def rmdir(self):
        try:
            self._call("rmd", self._wirepath)
        except _ftplib.error_perm as error:
            raise self._translate(error, self._rmdir_mismatch(error)) from error

    def _rmdir_mismatch(self, error: "_ftplib.error_perm"):
        def mismatch(st) -> "OSError | None":
            if not st.is_dir():
                return NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
            if any(word in str(error).lower() for word in PERMISSION_WORDS):
                return None
            try:
                empty = next(iter(self._scandir()), None) is None
            except OSError:
                return None
            if not empty:
                return OSError(_errno.ENOTEMPTY, "Directory not empty", str(self))
            return None

        return mismatch

    def rename(self, target: "FTPPath | str"):
        # A plain str target is a sibling rename (relative to self's
        # parent), matching sftp.py's rename() semantics.
        if isinstance(target, str):
            target = self.parent / target
        elif not isinstance(target, FTPPath) or not self._same_filesystem(target):
            raise NotImplementedError("rename() onto another server")
        destination = self._rebuild(target.as_posix())
        try:
            self._call("rename", self._wirepath, destination._wirepath)
        except _ftplib.error_perm as error:
            raise self._rename_error(error, destination) from error
        # pathlib returns the new path.
        return destination

    def _rename_error(
        self, error: "_ftplib.error_perm", destination: "FTPPath"
    ) -> OSError:
        """OSError for a refused rename. Whether a server replaces an existing
        target is its own business (POSIX ones do, Windows ones answer `550
        File exists`), so a refusal that does not read as a permission one,
        with the source there and the target too, is `FileExistsError`."""
        refused = self._translate(error)
        if isinstance(refused, FileNotFoundError) or any(
            word in str(error).lower() for word in PERMISSION_WORDS
        ):
            return refused
        try:
            destination._fresh_stat()
        except OSError:
            return refused
        return FileExistsError(
            _errno.EEXIST, f"File exists ({error})", str(destination)
        )

    def _utime(
        self, *, atime: "float | None" = None, mtime: "float | None" = None
    ) -> None:
        # `MFMT` (draft-somers-ftp-mfxx) sets the modification time, in UTC and
        # whole seconds; FTP has no access time to set. Only a server whose
        # `FEAT` lists it is asked.
        if mtime is None:
            return
        if not self._offers("MFMT"):
            raise NotImplementedError("MFMT is not offered by this server")
        stamp = _time.strftime("%Y%m%d%H%M%S", _time.gmtime(mtime))
        try:
            self._call("voidcmd", f"MFMT {stamp} {self._wirepath}")
        except _ftplib.error_perm as error:
            if reply_code(error) in UNSUPPORTED_REPLIES:
                raise NotImplementedError(
                    f"MFMT not supported by this server ({error})"
                ) from error
            raise self._translate(error) from error

    def chmod(self, mode: "int | str", *, follow_symlinks: bool = True):
        # SITE CHMOD is a non-standard extension that many servers lack. A rejection of an
        # existing path becomes NotImplementedError, which copy() skips; a missing path is
        # FileNotFoundError.
        if not follow_symlinks:
            raise NotImplementedError("chmod(follow_symlinks=False)")
        mode = _as_mode(mode)
        try:
            self._call("voidcmd", f"SITE CHMOD {mode:o} {self._wirepath}")
        except _ftplib.error_perm as error:
            if reply_code(error) in UNSUPPORTED_REPLIES:
                raise NotImplementedError(
                    f"SITE CHMOD not supported by this server ({error})"
                ) from error
            translated = self._translate(error)
            if isinstance(translated, FileNotFoundError):
                raise translated from error
            raise NotImplementedError(
                f"SITE CHMOD refused by the server ({error})"
            ) from error

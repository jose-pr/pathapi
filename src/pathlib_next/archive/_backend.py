from __future__ import annotations

import contextlib as _contextlib
import io as _io
import os as _os
import shutil as _shutil
import tempfile as _tempfile
import threading as _threading
import weakref as _weakref

from ..path import Path, _local_file
from .._filestat import FileStat
from ._snapshot import _Snapshot

_registry_lock = _threading.Lock()
_registry: "_weakref.WeakValueDictionary[tuple[type, object], _ArchiveBackend]" = (
    _weakref.WeakValueDictionary()
)


def _local_outer_path(outer: "Path") -> "str | None":
    """The local filesystem path of an outer archive that is a file of this
    machine (a `LocalPath`, or a path holding one such as a `file:` URI), else
    None."""
    local = _local_file(outer)
    if local is None:
        return None
    try:
        return str(local)
    except (NotImplementedError, ValueError):
        return None


def _outer_identity(outer: "Path") -> object:
    """What names a non-local outer archive in the registry: its node, for a
    path that answers `_node_key()`, else its URI."""
    try:
        node = outer._node_key()
    except Exception:
        node = None
    if node is not None:
        space, names = node
        return id(space), names
    return outer.as_uri()


def _registry_key(
    backend_cls: type, outer: "Path", identity: object = None
) -> "tuple[type, object]":
    local = _local_outer_path(outer)
    if local is not None:
        # One key per file, not per spelling: `C:` vs `c:` or a symlink used
        # to get independent handles onto the same file.
        return backend_cls, _os.path.normcase(_os.path.realpath(local))
    return backend_cls, _outer_identity(outer) if identity is None else identity


def _get_backend(
    backend_cls: "type[_ArchiveBackend]", outer: "Path", identity: object = None
) -> "_ArchiveBackend":
    """Return the shared `_ArchiveBackend` for `outer`, creating one if this
    is the first live reference. Keyed by (backend class, canonical local
    file path -- or `identity`, else the node or URI of a non-local outer) so
    independently-constructed paths pointing at the same archive share one
    handle instead of each opening their own -- avoids the stale-read/
    corrupt-write hazard of two handles writing the same underlying file.
    Backed by a `WeakValueDictionary`: once every path referencing a given
    backend is garbage-collected, the backend itself is collected (its
    `__del__` closes the underlying handle) and the registry entry is
    dropped automatically -- no explicit refcounting needed."""
    key = _registry_key(backend_cls, outer, identity)
    with _registry_lock:
        backend = _registry.get(key)
        if backend is None:
            backend = backend_cls(outer)
            _registry[key] = backend
        return backend


MEMBER_SPOOL_BYTES = 16 * 1024 * 1024
"""A member read through an archive path is held in memory up to this many
bytes and spooled to an unnamed temporary file beyond that, so a member
larger than memory can be read."""

_CHUNK = 64 * 1024


def _spool(source) -> "_io.IOBase":
    """A seekable copy of the stream `source`, in memory up to
    `MEMBER_SPOOL_BYTES` and in a temporary file past it. The copy is what a
    caller keeps, so the archive's shared handle is not tied up by a reader
    that keeps its stream open across later changes to the archive."""
    memory = _io.BytesIO()
    while True:
        chunk = source.read(_CHUNK)
        if not chunk:
            memory.seek(0)
            return memory
        memory.write(chunk)
        if memory.tell() > MEMBER_SPOOL_BYTES:
            break
    disk = _tempfile.TemporaryFile()
    try:
        with memory.getbuffer() as written:
            disk.write(written)
        memory.close()
        _shutil.copyfileobj(source, disk, _CHUNK)
        disk.seek(0)
    except BaseException:
        disk.close()
        raise
    return disk


class _LazyReadFile:
    """A read-only, seekable view of a local file that holds an OS handle
    only while an operation runs: `release()` closes it, and the next
    read/seek reopens it at the same position. A shared `ZipFile` or
    `TarFile` over one therefore never keeps the archive open between
    operations (on Windows an open handle blocks deleting or replacing the
    file)."""

    def __init__(self, path: str):
        self.name = path
        self._pos = 0
        # Opened eagerly: a missing file raises FileNotFoundError here,
        # which `zipfile` would otherwise turn into BadZipFile.
        self._fp = open(path, "rb")

    def _file(self):
        if self._fp is None:
            self._fp = open(self.name, "rb")
            self._fp.seek(self._pos)
        return self._fp

    def read(self, size=-1):
        data = self._file().read(size)
        self._pos = self._fp.tell()
        return data

    def seek(self, offset, whence=0):
        self._pos = self._file().seek(offset, whence)
        return self._pos

    def tell(self):
        return self._pos

    def seekable(self):
        return True

    def readable(self):
        return True

    def release(self):
        fp, self._fp = self._fp, None
        if fp is not None:
            fp.close()

    close = release


class _ArchiveBackend:
    """Lazily opens+caches the archive handle for one outer archive.
    Shared by every archive path derived from the same one, both through
    normal backend propagation (`with_segments`/`joinpath`/...) AND across
    independently-constructed paths via the module-level `_get_backend`
    registry above.

    For a local outer the cached handle is revalidated against the file's
    (inode, size, mtime) on every access and reopened when it changed, so a
    write by another process or tool is seen instead of being overwritten
    from a stale central directory. `_lock` serializes every use of the
    shared handle."""

    __slots__ = (
        "outer",
        "_handle",
        "_signature",
        "_lock",
        "_snapshot",
        "_prefetched",
        "__weakref__",
    )

    #: What a damaged archive raises, whatever decoder noticed it.
    _FORMAT_ERROR: "type[Exception]" = OSError

    def __init__(self, outer: "Path"):
        self.outer = outer
        self._handle = None
        self._signature = None
        self._snapshot = None
        self._prefetched = None
        self._lock = _threading.RLock()

    @property
    def lock(self):
        """Held across a read of the shared handle and the use of its
        result: another thread's rewrite reopens the handle."""
        return self._lock

    def _offer(self, data: bytes) -> None:
        """Hands over the bytes of a non-local outer that were read to decide
        its format, so opening does not fetch them a second time."""
        with self._lock:
            if self._handle is None and self._prefetched is None:
                self._prefetched = data

    def _outer_bytes(self) -> bytes:
        """The whole outer archive of a non-local outer."""
        with self._lock:
            data, self._prefetched = self._prefetched, None
        return self.outer.read_bytes() if data is None else data

    def _is_format_error(self, error: Exception) -> bool:
        """Whether `error`, raised by the decoder, says the archive is damaged."""
        return False

    @_contextlib.contextmanager
    def _damage_as_format_error(self):
        """Re-raises what a decoder raises for a damaged archive as the
        format's own error, so a caller handles one family per format.
        Anything else (a missing member, an I/O error) passes unchanged."""
        try:
            yield
        except Exception as error:
            if isinstance(error, self._FORMAT_ERROR) or not self._is_format_error(
                error
            ):
                raise
            raise self._FORMAT_ERROR(str(error) or type(error).__name__) from error

    def __del__(self):
        try:
            self._close_handle()
        except Exception:
            pass

    def _open(self):
        raise NotImplementedError

    def _close_handle(self):
        # Drop the cached handle so the next operation (through any instance sharing this
        # backend) reopens and sees the change. The snapshot derives from that handle's
        # entries, so it goes too; every mutation replaces the archive (`_replace_outer`).
        handle = self._handle
        self._handle = None
        self._snapshot = None
        if handle is not None:
            handle.close()

    def refresh(self) -> None:
        """Forget the handle and any bytes kept from the outer archive; the
        next use reads the outer again."""
        with self._lock:
            self._prefetched = None
            self._close_handle()

    def _outer_signature(self):
        local = _local_outer_path(self.outer)
        if local is None:
            return None
        try:
            st = _os.stat(local)
        except OSError:
            return None
        return st.st_ino, st.st_size, st.st_mtime_ns

    def _outer_file(self) -> str:
        """The local file of the outer archive; `ValueError` for any other."""
        local = _local_outer_path(self.outer)
        if local is None:
            raise ValueError(f"the archive {self.outer} is not a local file")
        return local

    @property
    def handle(self):
        with self._lock:
            signature = self._outer_signature()
            if self._handle is not None and signature != self._signature:
                self._close_handle()
            if self._handle is None:
                self._handle = self._open()
                self._signature = signature
            return self._handle

    @property
    def writable(self) -> bool:
        return False

    def snapshot(self) -> _Snapshot:
        """The members of the open handle, derived once and cached with it
        (see `_Snapshot`). Revalidates the handle once; a reopen drops the
        cached one. Every listing, stat, read and write starts here, and
        rebuilding it from the entries each time made an operation on a
        20k-member archive scan all of them."""
        with self._lock:
            self.handle  # revalidates, and clears the snapshot if it reopened
            if self._snapshot is None:
                entries = self._entries()
                self._snapshot = _Snapshot(
                    type(self), entries, self._link_texts(entries)
                )
            return self._snapshot

    def member_index(self) -> "dict[str, str]":
        """Normalized member name -> the key this backend knows it by."""
        return self.snapshot().index

    def raw_names(self, normalized: str) -> "tuple[str, ...]":
        return self.snapshot().raw_names(normalized)

    def _entries(self) -> "list[tuple[str, object]]":
        """`(key, info)` for every entry of the open handle, in archive
        order; reads nothing from the archive."""
        raise NotImplementedError

    def _link_texts(self, entries) -> "dict[str, str | None]":
        """`{key: stored text}` for the entries that are symbolic links (the
        text is None when it cannot be read); empty for a format without."""
        return {}

    @staticmethod
    def _is_directory(info) -> bool:
        raise NotImplementedError

    @staticmethod
    def _stat_of(info, snapshot: _Snapshot) -> FileStat:
        raise NotImplementedError

    def read_member(self, path: str):
        raise NotImplementedError


def _detect_backend_cls(outer: "Path") -> "tuple[type, bytes | None]":
    """Detect zip vs tar for `outer` (extension, then magic-byte sniff --
    see `detect_format`): the matching backend class and, when
    the sniff had to read a non-local outer whole, those bytes. An error
    reading the outer propagates. Lazily imports `._zip`/`._tar` (rather than
    at module level) to avoid a circular import: both submodules import
    `_ArchiveBackend` from this module."""
    from ._pack import detect_format
    from ._tar import _TarBackend
    from ._zip import _ZipBackend

    local = _local_outer_path(outer) is not None
    whole: "list[bytes]" = []

    def _peek() -> bytes:
        if local:
            with outer.open("rb") as f:
                return f.read(4)
        whole.append(outer.read_bytes())
        return whole[0][:4]

    fmt = detect_format(outer.name, _peek)
    return (_ZipBackend if fmt == "zip" else _TarBackend), (whole[0] if whole else None)


class _UndecidedBackend:
    """Stands in for the backend of an `ArchivePath` until something needs
    it: the format is settled on first use, from the extension or, failing
    that, from the bytes of the outer archive. Building a path, printing it
    and joining names onto it never reach the outer. A failure to read the
    outer propagates and decides nothing, so the next use tries again.
    `shared` and `identity` are what `_get_backend` is asked with."""

    __slots__ = ("outer", "_lock", "_resolved", "_shared", "_identity", "__weakref__")

    def __init__(self, outer: "Path", shared: bool = True, identity: object = None):
        self.outer = outer
        self._lock = _threading.RLock()
        self._resolved = None
        self._shared = shared
        self._identity = identity

    def resolve(self) -> "_ArchiveBackend":
        resolved = self._resolved
        if resolved is None:
            with self._lock:
                resolved = self._resolved
                if resolved is None:
                    backend_cls, data = _detect_backend_cls(self.outer)
                    if not self._shared:
                        # It must not borrow a handle another path opened.
                        resolved = backend_cls(self.outer)
                    else:
                        resolved = _get_backend(backend_cls, self.outer, self._identity)
                    if data is not None:
                        resolved._offer(data)
                    self._resolved = resolved
        return resolved

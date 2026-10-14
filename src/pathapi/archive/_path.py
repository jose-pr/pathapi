from __future__ import annotations

import errno as _errno
import os as _os
import urllib.parse as _urlparse

from .._tree import TreePath
from ..fspath import LocalPath
from ..path import Path
from .._names import is_safe_child_name
from ._backend import (
    _ArchiveBackend,
    _UndecidedBackend,
    _get_backend,
)
from ._names import _normalize_member_name
from ._snapshot import LinkTarget, _Snapshot, _Unresolved
from ._tar import _TarBackend
from ._write import _WritableMembers
from ._zip import _ZipBackend

_SEP = "!/"


def _outer_file(archive: object) -> Path:
    """The archive file as a `Path`: a path of this library as it is, a `str`
    or any other `os.PathLike` as a `LocalPath`."""
    if isinstance(archive, Path):
        return archive
    if isinstance(archive, (str, _os.PathLike)):
        return LocalPath(archive)
    raise TypeError(
        "archive should be a Path, a str or an os.PathLike, "
        f"not {type(archive).__name__!r}"
    )


def _rebuild_member(cls, text, archive):
    return cls(text, archive=archive)


class ArchivePath(_WritableMembers, TreePath):
    """A member path of an archive file, which is any `Path` (a `LocalPath`, a
    `MemPath`, a path on a remote store). The segments are the member path; the
    archive's format, zip or tar, is read from its extension or its first bytes
    on first use, and `ZipPath` and `TarPath` fix it. Building a path, printing
    it and joining names onto it read nothing.

    A zip archive that is a local file can be written (new members,
    `unlink`/`rmdir`/`rename`, `batch()`); every other archive is read-only.
    One backend serves each archive file and format, so paths built separately
    for one file see each other's writes."""

    __slots__ = ("_backend",)
    _SCHEME = "archive"
    _backend_cls: "type | None" = None

    def __init__(self, *segments: "str | ArchivePath", archive: object = None):
        text, donor = self._join(segments)
        if archive is not None:
            backend = self.backend_for(archive)
        elif donor is not None:
            backend = donor._backend
        else:
            raise TypeError("an archive path needs archive=")
        self._segments = self._parse(text)
        self._backend = backend

    @classmethod
    def backend_for(cls, archive: object, *, shared: bool = True, identity=None):
        """The backend that serves `archive`, to pass as `archive=` of any path
        of the class: the backend of another path of the same archive is
        returned as it is. With `shared` the one backend of that archive file
        and format (found by its real path for a local file, else by `identity`
        or the node or URI of the path); with `shared=False` a backend of its
        own. Reads nothing: the format of `ArchivePath` is decided on first
        use."""
        if isinstance(archive, (_ArchiveBackend, _UndecidedBackend)):
            return archive
        outer = _outer_file(archive)
        if cls._backend_cls is None:
            return _UndecidedBackend(outer, shared, identity)
        if not shared:
            return cls._backend_cls(outer)
        return _get_backend(cls._backend_cls, outer, identity)

    def _rebuild(self, text: str) -> "ArchivePath":
        return type(self)(text, archive=self._backend)

    def __reduce__(self):
        # The text and the archive file; the handle is found again by the
        # registry, and a pickle holds no open file.
        return _rebuild_member, (type(self), self.as_posix(), self.archive)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.as_posix()!r}, archive={self.archive!r})"

    @property
    def archive(self) -> Path:
        """The archive file. Never decides the format."""
        return self._backend.outer

    @property
    def backend(self) -> _ArchiveBackend:
        """The handle on the archive, shared by every path of it: settles the
        format of an `ArchivePath` on the first call, which reads the archive
        when its extension does not say."""
        backend = self._backend
        if isinstance(backend, _UndecidedBackend):
            backend = backend.resolve()
        return backend

    def refresh(self) -> None:
        """Forgets what is held of the archive file, so the next use reads it
        again. A non-local archive is read once and kept while any path to it
        lives, so this is how a later change at its source is seen; a local one
        is checked against the file on every use. Every path to the same
        archive shares the refresh."""
        backend = self._backend
        if isinstance(backend, _UndecidedBackend):
            backend = backend._resolved
            if backend is None:
                return  # nothing has been read yet
        backend.refresh()

    def as_uri(self) -> str:
        # Encoded so the string parses back to the same member: the member
        # path's `%`, `?` and `#`, and a literal "!/" inside the archive URI.
        outer = self.archive
        outer_uri = outer.as_uri()
        if not isinstance(outer, ArchivePath):
            # A nested archive's outer keeps its own separator (it already
            # encodes any other "!/").
            outer_uri = outer_uri.replace(_SEP, "%21/")
        member = _urlparse.quote(self.as_posix().lstrip("/"), safe="/")
        # A "!/" inside a member name must never read as a separator once
        # this URI is itself the outer of a nested archive.
        member = member.replace(_SEP, "%21/")
        return f"{self._SCHEME}:{outer_uri}{_SEP}{member}"

    def _snapshot(self) -> _Snapshot:
        """The archive's members as of one validation of its handle; see
        `_Snapshot`."""
        return self.backend.snapshot()

    def _raw_name(self, name: str) -> str:
        """The backend's own key for a normalized name (the name itself when
        the archive spells it canonically, or holds no such member yet).

        A write may be the call that CREATES the archive, so a missing outer
        is not an error here -- there is simply nothing to map yet.
        """
        try:
            return self._snapshot().index.get(name, name)
        except OSError:
            return name

    @property
    def _member(self) -> "str | None":
        """This path as a normalized member name, or None if it escapes."""
        return _normalize_member_name(self.as_posix().removeprefix("/"))

    def _resolve(self, snapshot: _Snapshot, member: str, follow: bool = True) -> str:
        """`member` with the link members on its way followed (the last
        component only when `follow`), or the error a lookup of it raises:
        `FileNotFoundError` naming the link that leaves the archive, is
        absolute or has no text, `OSError(ELOOP)` after `MAX_LINKS` links."""
        try:
            return snapshot.resolve(member, follow=follow)
        except _Unresolved as error:
            link = self._rebuild(error.link)
            if error.loop:
                raise OSError(
                    _errno.ELOOP, "Too many levels of symbolic links", str(link)
                ) from None
            raise FileNotFoundError(
                _errno.ENOENT,
                "A link does not resolve inside the archive",
                str(link),
            ) from None

    def readlink(self) -> LinkTarget:
        """The text a link member stores, as a `LinkTarget` (`OSError(EINVAL)`
        when this member is not a link). Links on the way to it are followed."""
        member = self._member
        if member is None:
            raise FileNotFoundError(str(self))
        snapshot = self._snapshot()
        member = self._resolve(snapshot, member, follow=False)
        if snapshot.stat(member) is None:
            raise FileNotFoundError(str(self))
        if member not in snapshot.links:
            raise OSError(_errno.EINVAL, "Invalid argument", str(self))
        text = snapshot.links[member]
        if text is None:
            raise OSError(_errno.EIO, "The link's text cannot be read", str(self))
        target = LinkTarget(text)
        target._text = text
        return target

    def _directory_children(self) -> "tuple[_Snapshot, str, list[str]]":
        member = self._member
        if member is None:
            raise FileNotFoundError(str(self))
        snapshot = self._snapshot()
        member = self._resolve(snapshot, member)
        found = snapshot.stat(member)
        if found is None:
            raise FileNotFoundError(str(self))
        if not found.is_dir():
            raise NotADirectoryError(_errno.ENOTDIR, "Not a directory", str(self))
        return snapshot, member, list(snapshot.children.get(member, ()))

    def _scandir(self):
        # The stat data is in the snapshot already: a listing costs one
        # validation of the handle, however many entries it has.
        snapshot, member, names = self._directory_children()
        prefix = f"{member}/" if member else ""
        for name in names:
            if not is_safe_child_name(name):
                continue
            found = snapshot.stat(prefix + name)
            if found is not None:
                yield name, found

    def iterdir(self):
        for name, _stat in self._scandir():
            yield self.with_segments(*self.segments, name)

    def stat(self, *, follow_symlinks=True):
        member = self._member
        if member is None:
            raise FileNotFoundError(str(self))
        # The root too: a missing or unreadable archive is not a directory
        # that exists, and a listing of it fails the same way.
        snapshot = self._snapshot()
        resolved = self._resolve(snapshot, member, follow_symlinks)
        if follow_symlinks and snapshot.links:
            self._check_enterable(snapshot, member, resolved)
        found = snapshot.stat(resolved)
        if found is None:
            raise FileNotFoundError(str(self))
        return found

    def _check_enterable(self, snapshot: _Snapshot, member: str, resolved: str) -> None:
        """A walk that follows links asks each listed child for its following
        stat, so that answer decides whether it descends. A link to a
        directory that lies beyond another link, or that points at the
        directory holding it or one of that directory's ancestors, answers
        `OSError(ELOOP)`: it is a link and not a directory to every walker,
        and the entries a recursive operation visits stay bounded by the
        members, the links and the directory links, however the links point.
        A path spelled through further links is still read, listed and
        opened."""
        entry = snapshot.resolve(member, follow=False)
        if entry not in snapshot.links:
            return
        found = snapshot.stat(resolved)
        if found is None or not found.is_dir():
            return
        names = member.split("/")
        beyond = any(
            "/".join(names[:count]) in snapshot.links for count in range(1, len(names))
        )
        parent = entry.rpartition("/")[0]
        around = (
            resolved == "" or resolved == parent or parent.startswith(resolved + "/")
        )
        if beyond or around:
            raise OSError(
                _errno.ELOOP,
                "A link to a directory is not entered "
                + (
                    "beyond another link"
                    if beyond
                    else "when it points at the directory that holds it or above"
                ),
                str(self),
            )

    def _is_dir_member(self) -> bool:
        try:
            return self.stat().is_dir()
        except FileNotFoundError:
            return False

    def _read_member(self):
        member = self._member
        if member is None:
            raise FileNotFoundError(str(self))
        try:
            snapshot = self._snapshot()
            spelled, member = member, self._resolve(snapshot, member)
            if snapshot.links:
                self._check_enterable(snapshot, spelled, member)
            if snapshot.hidden(member):
                raise KeyError(member)
            return self.backend.read_member(self._raw_name(member))
        except KeyError as error:
            if self._is_dir_member():
                raise IsADirectoryError(
                    _errno.EISDIR, "Is a directory", str(self)
                ) from error
            raise FileNotFoundError(str(self)) from error

    def _open(self, mode="r", buffering=-1):
        if "r" in mode and "+" not in mode:
            return self._read_member()
        return self._open_for_write(mode)

    def _node_key(self):
        # One member per archive handle, whichever spelling names it.
        member = self._member
        if member is None:
            return None
        return self.backend, tuple(member.split("/")) if member else ()

    def _same_filesystem(self, other: "ArchivePath") -> bool:
        # The archive handle is the namespace. A path of an undecided archive
        # holds a stand-in until its format is settled, and two of them naming
        # one archive settle on one handle.
        try:
            return other.backend is self.backend
        except Exception:
            # The archive cannot be read: nothing settles its handle.
            return False


class ZipPath(ArchivePath):
    """A member path of a zip archive, whatever its extension. Read/write when
    the archive file is a local `Path`; every other archive is read-only (it
    is fetched whole into memory first). Every change replaces the archive
    atomically (a temporary file next to it, then `os.replace`), so the result
    is a new file: other hard links keep the old content, and an archive the
    caller may not write is refused first. A new member is appended, deflated,
    to a byte copy of the archive (nothing recompressed); overwriting,
    deleting or renaming a member rewrites the archive, keeping every other
    member's metadata and compression method."""

    __slots__ = ()
    _SCHEME = "zip"
    _backend_cls = _ZipBackend


class TarPath(ArchivePath):
    """A member path of a tar archive, plain or compressed (`tarfile`'s
    detected `r:*` mode), whatever its extension. Read-only. Members stored
    with a `./` prefix are addressed without it."""

    __slots__ = ()
    _SCHEME = "tar"
    _backend_cls = _TarBackend

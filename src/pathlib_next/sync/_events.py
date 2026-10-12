"""The events a sync reports and the path-and-stat pair it works on."""

from __future__ import annotations

import enum as _enum
import stat as _stat
import typing as _ty

from ..path import Path
from .._filestat import FileStat


class SyncEvent(_enum.Enum):
    """Events `PathSyncer.hook()` fires during a sync, for progress/logging
    callbacks."""

    Copy = _enum.auto()
    RemovedMissing = _enum.auto()
    Synced = _enum.auto()
    CreatedDirectory = _enum.auto()
    SyncStart = _enum.auto()
    TypeMismatch = _enum.auto()
    CheckTargetChild = _enum.auto()
    CheckTargetChildren = _enum.auto()
    SyncChild = _enum.auto()
    SyncChildren = _enum.auto()
    Symlink = _enum.auto()
    #: Comparing a source/target file pair (quick check or checksum)
    #: failed. Only ever passed to `ignore_error`; no hook fires for it.
    Compare = _enum.auto()
    #: The source entry is neither a regular file, a directory nor a
    #: symlink (a FIFO, socket or device, or a stat with no file type): it is
    #: skipped and the target is left untouched.
    Skipped = _enum.auto()
    #: `ignore_error` tolerated an error for this source/target pair. Fired
    #: after the policy returned True; the failing event and the exception
    #: are in the WARNING record logged on the `pathlib_next.sync` logger.
    Error = _enum.auto()


class PathAndStat(object):
    """A `Path` plus its cached `stat()` result (`None` if it doesn't
    exist). `is_*` attribute access (e.g. `.is_file()`) delegates to the
    cached stat, returning a false-returning callable if the path doesn't
    exist; any other unknown attribute raises `AttributeError` as normal.

    `follow_symlink` (in `__init__` and `refresh()`) defaults to `True`,
    like `pathlib.Path.stat()` and `FileStat.from_path()`: a symlink is
    described by what it points at. Pass `False` for the link itself
    (`lstat()`); `PathSyncer` always passes its `follow_symlinks` setting."""

    __slots__ = ("_path", "_stat")

    def __init__(self, path: Path, *, follow_symlink: bool = True) -> None:
        self._path = path
        self.refresh(follow_symlink)

    @classmethod
    def from_stat(cls, path: Path, stat: FileStat | None) -> "PathAndStat":
        entry = cls.__new__(cls)
        entry._path = path
        entry._stat = stat
        return entry

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return str((self.path, self._stat))

    @property
    def path(self):
        return self._path

    @property
    def stat(self):
        return self._stat

    def exists(self):
        return self.stat is not None

    def refresh(self, follow_symlink: bool = True):
        self._stat = FileStat.from_path(self.path, follow_symlink=follow_symlink)

    def __getattr__(self, name: str):
        if name.startswith("is_"):
            if self.stat:
                return getattr(self.stat, name)
            else:
                return lambda *args, **kwargs: False
        raise AttributeError(name)


if _ty.TYPE_CHECKING:

    class PathAndStat(PathAndStat, FileStat): ...

"""Symbolic links in a sync: whether they can be made and whether two agree."""

from __future__ import annotations

import errno as _errno
import os as _os
import pathlib as _pathlib
import stat as _stat

from ..path import Path
from ..protocols import Symlink, supports
from .._filestat import FileStat
from ._events import PathAndStat


def _supports_symlinks(path: Path) -> bool:
    """Whether `path`'s class makes links. Decided on the class, before
    anything is touched; a backend that implements the primitive can still
    fail at runtime."""
    return supports(path, Symlink)


def _link_text(path: Path) -> str:
    """The raw, unresolved target string of the symlink at `path`. A path
    that has a `.path` attribute (a URI path) holds the un-prefixed link text there,
    since `as_posix()` prepends "host:" when the result carries a host; plain
    stdlib Path has no `.path`, so `.as_posix()` is the only accessor there.
    Both sides of a comparison must use this same extraction."""
    link = path.readlink()
    return link.path if hasattr(link, "path") else link.as_posix()


def _is_windows_local(path: Path) -> bool:
    return _os.name == "nt" and isinstance(path, _pathlib.Path)


def _link_is_directory(path: Path) -> bool:
    """Whether a symlink at `path` is (or should be recreated as) a
    directory link. On Windows a local link records its own kind, dangling
    or not, in its attributes; elsewhere the kind is what the link resolves
    to (False when dangling), which only Windows targets act on."""
    if _is_windows_local(path):
        attributes = getattr(_os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & _stat.FILE_ATTRIBUTE_DIRECTORY)
    try:
        return bool(path.is_dir())
    except (OSError, NotImplementedError):
        return False


def _same_link(source: Path, target: Path) -> bool:
    """Whether the symlink `target` already matches the symlink `source`:
    the same raw target string, and on a Windows target the same link kind
    (a file link to a directory cannot be traversed). Any failure to read
    either side means "not known to match", so the link is replaced."""
    try:
        if _link_text(source) != _link_text(target):
            return False
        if _is_windows_local(target):
            return _link_is_directory(source) == _link_is_directory(target)
        return True
    except (OSError, NotImplementedError):
        return False


def _as_entry(entry) -> "PathAndStat":
    # Hooks and error policies are declared to receive PathAndStat; a raw
    # path that never got stat'd (its stat() is what failed) is reported with
    # an unknown stat.
    if isinstance(entry, PathAndStat):
        return entry
    return PathAndStat.from_stat(entry, None)


def _unresolved_link(entry: "PathAndStat") -> "tuple[Exception, PathAndStat] | None":
    """For a source entry whose followed stat found nothing: the error to
    offer and the entry as the link itself, when the directory still lists
    something there that cannot be resolved (a dangling link, a loop, an
    unmounted volume). None when nothing is left at that name, so it
    vanished after the listing. Never an instruction to delete anything."""
    link = FileStat.from_path(entry.path, follow_symlink=False)
    if link is None:
        return None
    error = None
    try:
        entry.path.stat()
    except OSError as caught:
        error = caught
    if error is None or isinstance(error, FileNotFoundError):
        error = FileNotFoundError(
            _errno.ENOENT, "the link's target does not exist", str(entry.path)
        )
    return error, PathAndStat.from_stat(entry.path, link)

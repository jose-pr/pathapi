"""What `copy()` and `move()` hand to one another: stats, identity and modes."""

from __future__ import annotations

import contextlib as _contextlib
import contextvars as _contextvars
import stat as _stat
import typing as _ty

if _ty.TYPE_CHECKING:
    from ._path import Path


def _stat_or_none(path: Path, follow_symlinks: bool):
    """`path.stat()`, or None where there is none to have: a missing path, a
    backend without `stat()`. The callers then do what they did without it."""
    try:
        return path.stat(follow_symlinks=follow_symlinks)
    except (OSError, ValueError, NotImplementedError):
        return None


class _Handed:
    """What `move()` found out about the two roots, for the one `copy()` it
    calls on exactly this source and target. `source_stat` is the source's
    own stat (None: not known to be a plain entry), `target_absent` that
    nothing was at the target, with nothing written or removed there since."""

    __slots__ = ("source", "target", "source_stat", "target_absent", "taken")

    def __init__(self, source, target, source_stat, target_absent):
        self.source, self.target = source, target
        self.source_stat, self.target_absent = source_stat, target_absent
        self.taken = False


_HANDED: "_contextvars.ContextVar[_Handed | None]" = _contextvars.ContextVar(
    "pathlib_next_handed_stats", default=None
)


@_contextlib.contextmanager
def _handing(source, target, source_stat, target_absent):
    """Make `source_stat` and `target_absent` available to the `copy()` of
    `source` onto `target` made inside the block, once, and to nothing else
    (`ObjectStorePath.copy()` and the like pass the very same objects on to
    `Path.copy()`)."""
    token = _HANDED.set(_Handed(source, target, source_stat, target_absent))
    try:
        yield
    finally:
        _HANDED.reset(token)


def _take_handed(source, target) -> "_Handed | None":
    """What `move()` handed to the copy of exactly `source` onto `target`, the
    first time it is asked; None for any other copy."""
    handed = _HANDED.get()
    if (
        handed is None
        or handed.taken
        or handed.source is not source
        or handed.target is not target
    ):
        return None
    handed.taken = True
    return handed


def _same_identity(first, second) -> bool:
    """Whether two stats carry the same `(st_dev, st_ino)`. A stat without
    them (a `FileStat`, an SFTP attribute set) says nothing."""
    one = (getattr(first, "st_dev", None), getattr(first, "st_ino", None))
    other = (getattr(second, "st_dev", None), getattr(second, "st_ino", None))
    return None not in one and None not in other and one == other


def _copy_mode(
    src: Path, target: Path, follow_symlinks: bool, stat: "_ty.Any" = None
) -> None:
    """Give `target` the permission bits `src` reports, when it reports any.

    Only a mode the source backend actually reported is metadata:
    `FileStat`'s placeholder (0o444 for a file) is not, and applying it made
    every copy from MemPath/HTTP/S3/... a read-only file that a re-copy or
    re-sync could not replace. Permission bits only: the file-type bits of
    `st_mode` are not a mode, and a backend such as FTP's SITE CHMOD sends
    them verbatim. Between two classes the setuid, setgid and sticky bits are
    dropped: a mode read from another backend (a tar member, a remote
    server) must not make a privileged file here."""
    try:
        if stat is None:
            stat = src.stat(follow_symlinks=follow_symlinks)
        if getattr(stat, "mode_known", True) and stat.st_mode:
            mode = _stat.S_IMODE(stat.st_mode)
            if type(src) is not type(target):
                mode &= ~(_stat.S_ISUID | _stat.S_ISGID | _stat.S_ISVTX)
            target.chmod(mode)
    except NotImplementedError:
        pass

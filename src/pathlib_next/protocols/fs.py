from __future__ import annotations

import abc as _abc
import errno as _errno
import stat as _stat
import time as _time
import typing as _ty

from .. import _permissions, _stubs
from ._errors import _os_error
from ._info import _PathInfo

__all__ = ["Chmod", "Chown", "Create", "FileStatLike", "Owner", "Stat", "Utime"]


class FileStatLike(_ty.Protocol):
    """Minimum properties stat like object should provide"""

    __slots__ = ()

    @property
    @_abc.abstractmethod
    def st_mode(self) -> int: ...
    @property
    @_abc.abstractmethod
    def st_size(self) -> int: ...
    @property
    @_abc.abstractmethod
    def st_mtime(self) -> float: ...


class Stat(_ty.Protocol):
    """Any object that can implement Stat and utilities functions based on it"""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("stat",)

    @_stubs.primitive
    def stat(self, *, follow_symlinks=True) -> FileStatLike:
        """Return the stat of the path (a stub: `NotImplementedError`)."""
        return _stubs._unimplemented("stat")

    def lstat(self) -> FileStatLike:
        """
        Like stat(), except if the path points to a symlink, the symlink's
        status information is returned, rather than its target's.
        """
        return self.stat(follow_symlinks=False)

    def _st_mode(self, *, follow_symlinks=True):
        """
        Utility function only for internal use if this object,
        not required nor to be expected in any implementations of the protocol
        """
        try:
            # follow_symlinks must be forwarded -- it wasn't, so is_symlink()
            # (which calls this with follow_symlinks=False) always resolved
            # through the symlink instead of stat'ing it directly.
            return self.stat(follow_symlinks=follow_symlinks).st_mode
        except (OSError, ValueError):
            # Every OSError (PermissionError included) and ValueError reads as "no such entry"
            # on every version: pathlib's rule from 3.13; 3.9-3.12 re-raise errors outside
            # ENOENT/ENOTDIR/EBADF/ELOOP (EACCES say), so there the generic exists() differs.
            return None

    # Convenience functions for querying the stat results
    def exists(self, *, follow_symlinks=True):
        """
        Whether this path exists.
        """
        return self._st_mode(follow_symlinks=follow_symlinks) is not None

    def is_dir(self, *, follow_symlinks=True):
        """
        Whether this path is a directory. `follow_symlinks=False` reports a
        symlink to a directory as not one (3.13 parity).
        """
        return _stat.S_ISDIR(self._st_mode(follow_symlinks=follow_symlinks) or 0)

    def is_file(self, *, follow_symlinks=True):
        """
        Whether this path is a regular file (also True for symlinks pointing
        to regular files unless `follow_symlinks=False`, 3.13 parity).
        """
        return _stat.S_ISREG(self._st_mode(follow_symlinks=follow_symlinks) or 0)

    def is_symlink(self):
        """
        Whether this path is a symbolic link.
        """
        return _stat.S_ISLNK(self._st_mode(follow_symlinks=False) or 0)

    def is_block_device(self):
        """
        Whether this path is a block device.
        """
        return _stat.S_ISBLK(self._st_mode() or 0)

    def is_char_device(self):
        """
        Whether this path is a character device.
        """
        return _stat.S_ISCHR(self._st_mode() or 0)

    def is_fifo(self):
        """
        Whether this path is a FIFO.
        """
        return _stat.S_ISFIFO(self._st_mode() or 0)

    def is_socket(self):
        """
        Whether this path is a socket.
        """
        return _stat.S_ISSOCK(self._st_mode() or 0)

    @property
    def info(self) -> "_PathInfo":
        """What this path is, as an object shaped like `pathlib.types.PathInfo`
        (Python 3.14): `exists()`, `is_dir()`, `is_file()` (each with
        `follow_symlinks=True`) and `is_symlink()`. It asks `stat()` at most
        once with and once without following links and remembers the answers
        on that object only: a new `info` is a new question. `LocalPath` has
        pathlib's own on 3.14."""
        return _PathInfo(self)

    def is_junction(self) -> bool:
        """Whether this path is a Windows junction (pathlib 3.12 parity).

        A junction is NOT a symlink: it is a second NAME for a directory,
        the Windows equivalent of a Linux bind mount. `is_symlink()` is
        False for one, `readlink()` does not describe it, and a
        non-following stat reports a plain directory -- which is exactly why
        a symlink check cannot protect a recursive walk from it.

        Default False; `LocalPath` answers for real. A remote
        backend has no such concept unless it says otherwise.
        """
        return False

    def is_mount(self) -> bool:
        """Whether this path is a mount point (pathlib parity).

        The POSIX half of the same idea: a bind mount makes one tree visible
        under a second name, with no link anywhere to say so. Default False;
        `LocalPath` answers for real.
        """
        return False

    def is_dir_binding(self) -> bool:
        """Whether this directory is another tree's second NAME rather than
        part of this one: a Windows junction or a mount point (bind or
        otherwise).

        The distinction that matters to anything walking a tree. A symlink
        announces itself -- `is_symlink()` is True and every walker here
        already declines to follow one. A binding does not: it looks like an
        ordinary directory, and its contents belong to whoever mounted or
        junctioned it. `rm(recursive=True)` removes the binding itself
        rather than the contents behind it.

        An implementation that cannot answer (stdlib's `is_mount()` raises
        on Windows before 3.12) counts as "not a binding" rather than
        breaking the operation that asked.
        """
        for test in (self.is_junction, self.is_mount):
            try:
                if test():
                    return True
            except (NotImplementedError, OSError, ValueError):
                continue
        return False


class Owner(Stat, _ty.Protocol):
    """Reading who owns a path. `owner()` and `group()` map the `st_uid` and
    `st_gid` of `stat()` through the primitives `_user_name()` and
    `_group_name()`; a class that has `owner()` and `group()` from somewhere
    else (`pathlib`) needs neither."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (
        ("_user_name", "owner"),
        ("_group_name", "group"),
    )

    def owner(self, *, follow_symlinks: bool = True) -> str:
        """The name of the user that owns the path. `NotImplementedError`
        when `stat()` carries no `st_uid` or the class does not map user ids
        to names (`_user_name()`)."""
        uid = getattr(self.stat(follow_symlinks=follow_symlinks), "st_uid", None)
        if uid is None:
            raise NotImplementedError("owner() requires stat() to provide st_uid")
        return self._user_name(uid)

    def group(self, *, follow_symlinks: bool = True) -> str:
        """The name of the group that owns the path. `NotImplementedError`
        when `stat()` carries no `st_gid` or the class does not map group ids
        to names (`_group_name()`)."""
        gid = getattr(self.stat(follow_symlinks=follow_symlinks), "st_gid", None)
        if gid is None:
            raise NotImplementedError("group() requires stat() to provide st_gid")
        return self._group_name(gid)

    @_stubs.primitive
    def _user_name(self, uid: int) -> str:
        """Backend primitive behind `owner()`: the name of user `uid` (a
        stub: `NotImplementedError`)."""
        return _stubs._unimplemented("_user_name")

    @_stubs.primitive
    def _group_name(self, gid: int) -> str:
        """Backend primitive behind `group()`: the name of group `gid` (a
        stub: `NotImplementedError`)."""
        return _stubs._unimplemented("_group_name")


class Chown(_ty.Protocol):
    """Changing who owns a path: `_chown()` is the primitive and `chown()`
    normalizes its arguments."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (("_chown", "chown"),)

    @_stubs.primitive
    def _chown(
        self,
        uid: int | str | None,
        gid: int | str | None,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        """Backend primitive behind `chown()`.

        Receives an already-canonical `(uid, gid)` pair: `int` to set, or
        `None` for "leave unchanged" (a `str` is a user/group *name*).
        Convert only to this backend's own wire spelling -- `-1` for
        `os.chown`, an omitted attr field for SFTP `setstat`. Normalizing
        the caller's input is `chown()`'s job, not the primitive's.
        """
        return _stubs._unimplemented("_chown")

    def chown(
        self,
        uid: int | str | None = None,
        gid: int | str | None = None,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        """Change the owner and/or group of the path.

        Extension: `pathlib.Path` has `owner()`/`group()` **readers** but no
        writer (the stdlib equivalent is `shutil.chown`/`os.chown`), so
        ownership was the one attribute `stat()` could report -- via
        `st_uid`/`st_gid` -- and nothing could write back.

        `None` (the default) leaves a field unchanged, which is what lets a
        caller set the group without knowing the owner. `-1` is accepted as
        an alias for `None`, matching `os.chown`'s own sentinel. An `int` is
        a uid/gid; a `str` is a user/group name, passed through for backends
        that can resolve one.

        Backends implement `_chown()` and receive an already-canonical pair
        (see `providers.parse_owner()`), so the "unchanged" semantics cannot drift
        between schemes.
        """
        uid, gid = _permissions.parse_owner(uid, gid)
        if uid is None and gid is None:
            # Nothing to change -- don't spend a round trip (or make a
            # backend decide what an all-sentinel call means).
            return
        return self._chown(uid, gid, follow_symlinks=follow_symlinks)


class Utime(_ty.Protocol):
    """Setting the access and modification times of a path."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("_utime",)

    @_stubs.primitive
    def _utime(self, *, atime: float | None = None, mtime: float | None = None) -> None:
        """Backend primitive: set the access and/or modification time of the
        entry, in seconds since the epoch. `None` leaves that time alone. A
        backend that keeps no such time (or no atime, or whole seconds only)
        sets what it can and ignores the rest; one that cannot set a time at
        all raises `NotImplementedError`, which is the default. `touch()` of an
        existing file and `PathSyncer(preserve_mtime=True)` call it."""
        return _stubs._unimplemented("_utime")


class Chmod(Chown, Utime, _ty.Protocol):
    """Changing the permissions of a path. It also inherits `Chown` and
    `Utime`, which it documented before they were separate protocols, so
    `chown()`, `_chown()` and `_utime()` stay reachable from it;
    `supports(x, Chmod)` asks for `chmod()` only."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("chmod",)

    @_stubs.primitive
    def chmod(self, mode: int | str, *, follow_symlinks: bool = True):
        """
        Change the permissions of the path, like os.chmod().

        Extension: `mode` may be a `str`, which is parsed as **octal**
        (`"0755"`, `"755"` and `0o755` all mean the same thing) -- see
        `providers.parse_mode()` for why the base is never left implicit.

        Every implementation normalizes its argument through
        `providers.parse_mode()` on entry. Unlike `symlink_to`/`chown`, `chmod` is
        overridden directly by each backend (each has real per-scheme logic
        -- version shims, capability gates, a `SITE CHMOD` command), so
        there is no single wrapper to hang the conversion on; the shared
        helper is what keeps the base from drifting between them.
        """
        return _stubs._unimplemented("chmod")

    def lchmod(self, mode: int | str):
        """
        Like chmod(), except if the path points to a symlink, the symlink's
        permissions are changed, rather than its target's.
        """
        self.chmod(mode, follow_symlinks=False)


class Create(Stat, Chmod, Utime, _ty.Protocol):
    """Creating a directory or a file: `mkdir()` and `touch()` on top of the
    `_mkdir()` primitive. `touch()` also uses `open()` (`BinaryOpen`) and
    `unlink()` (`Remove`)."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = (("_mkdir", "mkdir"),)

    def touch(self, mode=None, exist_ok=True):
        """
        Create this file, if it doesn't exist.

        Raises FileExistsError if exist_ok is False and the file already
        exists (pathlib parity). An existing file is never truncated.

        Differences from `pathlib.Path.touch`, which creates through
        `os.open()` (so the process umask applies) and bumps an existing
        file's mtime:

        - `mode` is applied with `chmod()` only when passed explicitly, and
          then verbatim: a remote server's umask is unknowable. A backend
          that cannot chmod raises `NotImplementedError` and the file it
          created is removed again, so a mode that was asked for is never
          silently dropped. The default (`None`) leaves a new file with the
          permissions the backend gives it, instead of chmod'ing it to a
          world-writable 0o666.
        - An existing file's access and modification times are set to the
          current time through `_utime()`; a backend that cannot set a time
          leaves the file as it is, since rewriting the content to bump it
          is not a touch. `LocalPath` uses pathlib's own touch.
        """
        # stat() directly, not exists(): exists() reads *any* OSError (a
        # timeout, a dropped connection) as "missing", which let a transient
        # failure fall through to a truncating open("w").
        try:
            self.stat()
        except FileNotFoundError:
            pass
        else:
            if not exist_ok:
                raise _os_error(FileExistsError, _errno.EEXIST, self)
            now = _time.time()
            try:
                self._utime(atime=now, mtime=now)
            except NotImplementedError:
                pass
            return
        try:
            with self.open("x"):
                ...
        except FileExistsError:
            # Created since the stat() above: an existing file, not ours to
            # truncate.
            if not exist_ok:
                raise
            return
        except NotImplementedError:
            # _open() doesn't support "x" (optional per the mode contract),
            # so the stat() above is the only guard before the truncating
            # "w" -- a small TOCTOU window.
            with self.open("w"):
                ...
        if mode is not None:
            try:
                self.chmod(mode)
            except NotImplementedError:
                # The caller asked for a mode this backend cannot apply: leave
                # no file that has other permissions than the ones asked for.
                try:
                    self.unlink()
                except (OSError, NotImplementedError):
                    pass
                raise

    @_stubs.primitive
    def _mkdir(self, mode: int):
        """Backend primitive: create this directory (a stub:
        `NotImplementedError`). `mkdir()` calls it."""
        return _stubs._unimplemented("_mkdir")

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        """
        Create a new directory at this given path.
        """
        try:
            self._mkdir(mode)
        except FileNotFoundError:
            if not parents or self.parent == self:
                raise
            # Parents may already exist (e.g. a sibling branch created them)
            # -- mirror CPython: exist_ok=True for parents, original
            # exist_ok only for the final retry of self.
            self.parent.mkdir(parents=True, exist_ok=True)
            self.mkdir(mode, parents=False, exist_ok=exist_ok)
        except FileExistsError:
            if not exist_ok or not self.is_dir():
                raise

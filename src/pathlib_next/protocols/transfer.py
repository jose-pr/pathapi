"""Telling two paths apart, copying and moving."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import shutil as _shutil
import stat as _stat
import typing as _ty

from .. import _names, _stubs
from .._errorpolicy import copy_error_predicate
from .._filestat import FileStat
from ._relation import (
    _INNER,
    _OTHER,
    _OUTER,
    _SAME,
    _drop_second_name,
    _relation,
    _same_file,
    contains,
)
from ._removal import _write_batch_of
from ._support import supports
from ._transfer import (
    _copy_mode,
    _handing,
    _same_identity,
    _stat_or_none,
    _take_handed,
)
from .fs import Create, Stat
from .io import _ASK_STAT, BinaryOpen
from .links import Symlink
from .tree import Remove

if _ty.TYPE_CHECKING:
    from ..path import Path

__all__ = ["Copy", "Identity", "Move"]


class Identity(Stat, _ty.Protocol):
    """Whether two paths name the same node, and in which namespace.
    `_same_filesystem()` and `_node_key()` are overridden by a class whose
    instances can front different stores; `samefile()` compares what
    `stat()` reports."""

    __slots__ = ()

    __primitives__ = ()

    def _same_filesystem(self, other: "Path") -> bool:
        """Whether `other`'s segments are resolved in the same namespace as
        this path's: the same host, store or tree. Only when they are do
        `==` and `is_relative_to()` between the two say anything about files,
        so `copy()`, `move()` and `PathSyncer` ask this before refusing a
        transfer as "the same file" or "overlapping".

        Called only with `type(other) is type(self)`. Must be symmetric and
        must not do I/O. Default: both share one `_backend` object (or neither
        has one), which makes equal paths of a type that says nothing the
        same file. A type whose instances can front different hosts or stores
        MUST override this, or a transfer between two of them that are spelled
        alike is refused."""
        return getattr(self, "_backend", None) is getattr(other, "_backend", None)

    def _node_key(self) -> "tuple[object, tuple[str, ...]] | None":
        """Where this path's node lives, for the same-file and nesting checks
        of `copy()` and `move()`: `(namespace, names)`. `namespace` is an
        object two paths share (compared with `is`) exactly when they resolve
        names in one tree; `names` is the node's position in it, normalized
        so that every spelling of one node gives the same tuple and the
        root gives `()`.

        Default `None`: two paths of one type that `_same_filesystem()`
        places together are compared with `==` and `is_relative_to()`. A type
        whose segments spell one node in several ways (a relative and an
        absolute form, a `..` segment, an alias scheme) overrides this, and
        its paths are then compared across classes. Must not do I/O."""
        return None

    def samefile(self, other_path: str | _ty.Self):
        """Return whether other_path is the same or not as this file, by
        comparing (st_dev, st_ino) when the backend's stat() provides them.
        Raises NotImplementedError otherwise (e.g. our own FileStat doesn't
        carry st_dev/st_ino) -- LocalPath gets a real implementation from
        pathlib.Path via MRO instead of this one.
        """
        from ..path import Path  # `path` imports this module

        # with_segments(), never the bare constructor or `_coerce_target()`:
        # a str is a path on this backend. `type(self)(str)` gave a MemPath
        # a fresh, empty backend, and a URI parse drops this path's host.
        other = (
            other_path
            if isinstance(other_path, Path)
            else self.with_segments(other_path)
        )
        st1 = self.stat()
        st2 = other.stat()
        ident1 = (getattr(st1, "st_dev", None), getattr(st1, "st_ino", None))
        ident2 = (getattr(st2, "st_dev", None), getattr(st2, "st_ino", None))
        if None in ident1 or None in ident2:
            raise NotImplementedError(
                "samefile() requires stat() to provide st_dev/st_ino"
            )
        return ident1 == ident2


class Copy(Identity, Create, Symlink, BinaryOpen, _ty.Protocol):
    """Copying a file or a tree to another path, of this backend or of
    another. It precedes `BinaryOpen` in a class's bases because both define
    `copy()` and this one takes the options of a path copy."""

    __slots__ = ()

    __primitives__ = ()

    def _copy_from(self, source, follow_symlinks=True, preserve_metadata=False):
        """Copy `source` -- any path with `info`, `iterdir()`, `readlink()` and
        `open()`, such as a `pathlib.Path` -- to this path, creating files and
        directories as pathlib 3.14 does: an existing file is overwritten, an
        existing directory raises. Python 3.14's `pathlib.Path.copy(target)`
        hands the work to `target._copy_from()`; `preserve_metadata` copies the
        permission bits and the modification time where this class can set
        them."""
        info = source.info
        link = not follow_symlinks and info.is_symlink()
        if link:
            self.symlink_to(str(source.readlink()), info.is_dir())
        elif info.is_dir():
            children = source.iterdir()
            self.mkdir()
            for child in children:
                self.joinpath(child.name)._copy_from(
                    child, follow_symlinks, preserve_metadata
                )
        else:
            with source.open("rb") as reader, self.open("wb") as writer:
                _shutil.copyfileobj(reader, writer)
        read = getattr(source, "stat", None)
        if preserve_metadata and not link and read is not None:
            stat = read()
            for apply in (
                lambda: self.chmod(_stat.S_IMODE(stat.st_mode)),
                lambda: self._utime(mtime=stat.st_mtime),
            ):
                try:
                    apply()
                except NotImplementedError:
                    pass

    def _coerce_target(self, target: str) -> "Path":
        """Turn a `str` destination (`copy()`, `move()`) into a
        path on the same backend. The default keeps per-instance state (a
        `MemPath`'s in-memory filesystem) via `with_segments()`; the bare
        constructor used before gave every str destination a fresh, empty
        backend, so `move("/c.txt")` wrote there and then deleted the source.
        A class whose strings are URI references overrides this to parse the
        string as one."""
        return self.with_segments(target)

    def _write_batch(self) -> "_ty.ContextManager[object]":
        """A context manager that `copy(recursive=True)` and `PathSyncer` enter
        around the writes they make to this path's tree, so a backend that
        pays for every write (a zip rewrites its file) can commit them
        together when the block ends. It must read nothing and raise nothing;
        the default does nothing."""
        return _contextlib.nullcontext()

    def copy(
        self,
        target: "Path | str",
        *,
        overwrite=False,
        follow_symlinks=True,
        preserve_metadata=True,
        recursive=False,
        ignore_error=None,
        progress: "_ty.Callable[[_ty.Self, int, _ty.Optional[int]], None]" = None,
        on_error: "bool | _ty.Callable[[Exception, _ty.Self], bool] | None" = None,
    ):
        """Copy this file's content to `target`.

        `follow_symlinks`/`preserve_metadata` are named to match CPython
        3.14's Path.copy() (added after this method); `overwrite` is our
        own extension (3.14 has no equivalent -- it always raises if the
        destination exists). `preserve_metadata` defaults to True here
        (unlike 3.14's False): st_mode is propagated, but not
        timestamps or xattrs -- full metadata preservation is not implemented.
        `ignore_error` accepts a bool or a callable. `True` ignores every
        error; `False` and `None` (the default) fail on the first error. A
        callable is invoked as `ignore_error(error)` and, as it always has
        here, is a *notification* hook: the error is suppressed regardless of
        what it returns, so handlers like `errors.append` (returning None)
        keep working. Only errors from *child* copies during a
        `recursive=True` copy are routed here.

        `on_error` is `rm()`'s predicate: a callable `(error, path) -> bool`
        (or a bool) asked once per child error, with `path` the source entry
        that failed. `True` skips the entry and goes on, `False` raises the
        error, and a predicate that raises ends the copy with its own
        exception. An error it declined is not offered again by the
        directories above. `ignore_error` and `on_error` are two spellings of
        one policy: passing both raises `TypeError` before anything is copied.

        A child whose listed name is not one safe component for `target` (a
        drive such as `C:x` or a backslash on a Windows target, which land
        outside it; a trailing dot, which Windows stores under another name)
        is refused with `ValueError` and reported through `ignore_error` or
        `on_error` like any child failure. `PathSyncer` and
        `unpack_archive()` apply the same rule; an archive listing keeps
        such names, as they are ordinary filenames on POSIX.

        `progress`, when given, is called as `progress(path, bytes_copied,
        total_size)` for every chunk written during each *file* copy (`path`
        is the source `Path` being streamed -- `self` for a single-file
        copy, or the relevant child during a `recursive=True` copy).
        `bytes_copied` increases monotonically per file and reaches
        `total_size` (or `None` if the size couldn't be determined) at the
        end of that file. Directories themselves don't get a progress call
        (only the files inside them do). With `progress=None` (the
        default), behavior is unchanged -- no per-chunk overhead. Native
        backend transfers that bypass the generic streaming copy (e.g.
        an SFTP backend's concurrent fan-out) do not invoke `progress`;
        see `docs/divergences.md`'s "Deliberate extensions" section.
        """
        offering = copy_error_predicate(ignore_error, on_error)
        if isinstance(target, str):
            target = self._coerce_target(target)
        src = self
        handed = _take_handed(src, target)
        known = None if handed is None else handed.source_stat

        if not follow_symlinks and known is None and src.is_symlink():
            # pathlib 3.14: copy the link itself, not what it points at.
            # Copying the target's content (and chmod'ing it with the link's
            # own 0o777) silently defeated the flag.
            return src._copy_symlink(target, overwrite=overwrite)

        # One stat of the source answers its type, its mode and, beside the
        # target's, whether the two are one file; None when it cannot be had.
        src_stat = known if known is not None else _stat_or_none(src, follow_symlinks)
        target_absent = handed is not None and handed.target_absent

        if recursive and src_stat is not None and _stat.S_ISDIR(src_stat.st_mode):
            if contains(src, target):
                # Checked before anything is created: the new directory would
                # be listed and copied into itself without end.
                raise OSError(
                    _errno.EINVAL,
                    "Cannot copy a directory into itself",
                    str(target),
                )
            # The writes below may be committed together by a backend that
            # pays for each one (see `_write_batch`).
            with _write_batch_of(target):
                # Listed before the target exists, as shutil.copytree does.
                children = list(src.iterdir())
                if not target_absent and target.exists():
                    if not target.is_dir():
                        raise FileExistsError(target)
                    if not overwrite:
                        raise FileExistsError(target)
                else:
                    target.mkdir()
                windows_target = _names.is_windows_flavoured(target)
                for child in children:
                    try:
                        # A listed name is untrusted: one that is not a single component inside
                        # `target` is refused (see the child-name paragraph of the docstring).
                        if not _names.is_safe_child_name(
                            child.name, windows=windows_target
                        ):
                            raise ValueError(
                                f"refusing unsafe child name {child.name!r} "
                                f"under {target}"
                            )
                        child.copy(
                            target / child.name,
                            overwrite=overwrite,
                            follow_symlinks=follow_symlinks,
                            preserve_metadata=preserve_metadata,
                            recursive=True,
                            ignore_error=ignore_error,
                            progress=progress,
                            # Passed on only when given, so a `copy()` override
                            # without the keyword still works as a child.
                            **({} if offering is None else {"on_error": offering}),
                        )
                    except Exception as e:
                        if offering is not None:
                            if not offering(e, child):
                                raise
                        elif callable(ignore_error):
                            # A notify-and-suppress hook: its return value is not
                            # consulted, so `errors.append` works.
                            ignore_error(e)
                        elif not ignore_error:
                            raise
                if preserve_metadata:
                    # After the children: a read-only directory could not take
                    # them, and its mode is only settled once they are in.
                    _copy_mode(src, target, follow_symlinks, src_stat)
                return

        # What needs no I/O first; the stats below settle the rest.
        if _same_file(src, target, identity=False):
            raise OSError(
                _errno.EINVAL, "Source and target are the same file", str(target)
            )
        # stat(), not exists(): exists() reads a transient error (a 503, a
        # timeout) as "missing", and overwrite=False must never be decided by
        # a failure. Only FileNotFoundError means the target is absent.
        try:
            target_stat = None if target_absent else target.stat()
        except FileNotFoundError:
            target_stat = None
        target_exists = target_stat is not None
        if target_stat is not None:
            if _same_identity(src_stat, target_stat):
                raise OSError(
                    _errno.EINVAL, "Source and target are the same file", str(target)
                )
            if _stat.S_ISDIR(target_stat.st_mode):
                raise IsADirectoryError(target)
            if not overwrite:
                raise FileExistsError(target)

        # Open the source before the target is touched at all: a missing or
        # unreadable source must leave an existing target intact and must not
        # leave a new empty one behind.
        with src.open("rb") as input:
            if target_exists:
                target.unlink(missing_ok=True)
            created = False
            try:
                with target.open("wb") as output:
                    created = True
                    BinaryOpen._copy_stream(
                        src,
                        input,
                        output,
                        progress=(
                            None
                            if progress is None
                            else lambda copied, total: progress(src, copied, total)
                        ),
                        total_size=(
                            _ASK_STAT
                            if src_stat is None
                            else (
                                getattr(src_stat, "st_size", None)
                                if getattr(src_stat, "size_known", True)
                                else None
                            )
                        ),
                    )
            except BaseException:
                # A half-written target is not a copy of anything; do not
                # leave it looking like one.
                if created:
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
                raise

        if preserve_metadata:
            _copy_mode(src, target, follow_symlinks, src_stat)

    def _into(self, target_dir: "Path | str") -> "Path":
        """`target_dir / self.name`, the target of `copy_into()` and
        `move_into()`. A `str` directory is a path on this backend, as for
        `copy()`'s `str` target."""
        name = self.name
        if not name:
            raise ValueError("%r has an empty name" % (self,))
        if isinstance(target_dir, str):
            target_dir = self._coerce_target(target_dir)
        return target_dir / name

    def copy_into(
        self,
        target_dir: "Path | str",
        *,
        overwrite=False,
        follow_symlinks=True,
        preserve_metadata=True,
        recursive=False,
        ignore_error=None,
        progress: "_ty.Callable[[_ty.Self, int, _ty.Optional[int]], None]" = None,
        on_error: "bool | _ty.Callable[[Exception, _ty.Self], bool] | None" = None,
    ) -> "Path":
        """Copy this file or directory into the directory `target_dir`, under
        its own name, and return the new path (pathlib 3.14 parity).

        `copy(target_dir / self.name, ...)` with the same keywords and the
        same defaults as `copy()`: an existing target needs `overwrite=True`
        and a directory source needs `recursive=True`. `ValueError` for a
        path with no name."""
        target = self._into(target_dir)
        self.copy(
            target,
            overwrite=overwrite,
            follow_symlinks=follow_symlinks,
            preserve_metadata=preserve_metadata,
            recursive=recursive,
            ignore_error=ignore_error,
            progress=progress,
            **({} if on_error is None else {"on_error": on_error}),
        )
        return target

    def _copy_symlink(self, target: "Path", *, overwrite=False):
        """`copy(follow_symlinks=False)` of a symlink: create a link at
        `target` with the same (unresolved) target text. Raises
        NotImplementedError when this backend cannot read links or the
        target's cannot create them -- never falls back to copying content."""
        if not supports(self, Symlink):
            raise NotImplementedError(f"copy(follow_symlinks=False) of {self!r}")
        link = self.readlink()
        if type(link) is not type(target):
            # Another backend: hand over the literal target text.
            text = getattr(link, "path", None)
            link = text if isinstance(text, str) else link.as_posix()
        if _same_file(self, target):
            raise OSError(
                _errno.EINVAL, "Source and target are the same file", str(target)
            )
        target_stat = FileStat.from_path(target, follow_symlink=False)
        if target_stat is not None:
            if target_stat.is_dir() and not target_stat.is_symlink():
                raise IsADirectoryError(target)
            if not overwrite:
                raise FileExistsError(target)
            target.unlink(missing_ok=True)
        target.symlink_to(link, target_is_directory=self.is_dir())


class Move(Copy, Remove, _ty.Protocol):
    """Renaming and moving: `rename()` is the primitive, and `move()` falls
    back to a copy followed by a removal where it is not available."""

    __slots__ = ()

    __primitives__ = ("rename",)

    def _rename_compatible(self, target: "Path") -> bool:
        """Whether `rename()` may be attempted onto `target` at all. `move()`
        skips straight to copy + delete when it is not. Default: only onto a
        path of this very class, since `rename()` runs inside this class's
        own store and would otherwise be handed a path of another store (a
        `MemPath` subclass given a `LocalPath` renamed within memory).
        A class for URI references decides per target in `rename()` (raising
        NotImplementedError); `LocalPath` requires a local target, since
        `os.rename()` would otherwise take a remote path's `__fspath__()` as
        a local name."""
        return type(target) is type(self)

    @_stubs.primitive
    def rename(self, target: "_ty.Self | str"):
        """Rename this file or directory to the given target."""
        return _stubs._unimplemented("rename")

    def move(self, target: "Path|str", *, overwrite=False):
        """Move this file or directory to target, falling back to copy+unlink/rm if rename is unsupported.

        Nothing is removed until the move is known to be allowed: a missing
        source, a file onto a directory, a target that holds the source
        (`OSError` `ENOTEMPTY`) or lies inside it (`EINVAL`), and a second
        name of the same file that is a link (`EINVAL`) all raise first. The
        same file under another spelling (a case alias, `x.move(x)`) is
        renamed in place. Between two hard links of one file, an existing
        target needs `overwrite=True`, and the source name is then removed.
        """
        if isinstance(target, str):
            target = self._coerce_target(target)
        src = self
        # rename() only makes sense between paths the same backend can see.
        native = src._rename_compatible(target)

        # Everything that can fail cheaply is checked before the target is
        # touched: a missing source, or a file onto a directory, must not
        # delete the target and only then raise.
        held = _Held(src)
        src_stat = held.source
        if src_stat is None:
            raise FileNotFoundError(
                _errno.ENOENT, "No such file or directory", str(src)
            )
        # The same entry under another spelling (a case-only rename on a
        # case-insensitive filesystem, or `x.move(x)`) is renamed in place:
        # removing the "existing" target would delete the source itself.
        relation = _relation(src, target, follow=False, held=held)
        target_stat = None
        if relation != _SAME:
            target_stat = held.target(target)
            if target_stat is not None and not overwrite:
                raise FileExistsError(target)
            if relation == _INNER:
                raise OSError(
                    _errno.EINVAL, "Cannot move a directory into itself", str(target)
                )
            if target_stat is not None:
                if relation == _OUTER:
                    raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(target))
                if relation == _OTHER:
                    return _drop_second_name(src, src_stat, target, target_stat)
                if target_stat.is_dir() and not target_stat.is_symlink():
                    if not src_stat.is_dir():
                        raise IsADirectoryError(target)
                    target.rm(recursive=True, missing_ok=True)
                elif (
                    native
                    and type(target) is type(src)
                    and getattr(type(src), "replace", Move.replace) is not Move.replace
                ):
                    # A class with its own replace() (os.replace() swaps atomically and leaves the
                    # target alone on failure). Path.replace() is built on move(): calling it here
                    # would never end.
                    try:
                        return src.replace(target)
                    except OSError as error:
                        if error.errno != _errno.EXDEV:
                            raise
                        native = False
                else:
                    target.unlink(missing_ok=True)

        if native:
            try:
                return src.rename(target)
            except NotImplementedError:
                pass
            except OSError as error:
                # Another filesystem or drive: fall back to copy + delete, as
                # shutil.move and 3.14's Path.move do.
                if error.errno != _errno.EXDEV:
                    raise

        # A link is recreated, not read through, wherever the target can hold one, so
        # the result does not depend on rename() being available. The copy is handed
        # what is known (nothing at `target`, `src` not a link); a failed rename changed nothing.
        links = _holds_links(src, target)
        handing = _handing(
            src,
            target,
            held.source_raw if not src_stat.is_symlink() else None,
            relation != _SAME and target_stat is None,
        )
        if src_stat.is_symlink() and links:
            with handing:
                src.copy(target, overwrite=overwrite, follow_symlinks=False)
            src.unlink()
        elif src.is_dir() if src_stat.is_symlink() else src_stat.is_dir():
            with handing:
                src.copy(
                    target,
                    overwrite=overwrite,
                    recursive=True,
                    follow_symlinks=not links,
                )
            src.rm(recursive=True)
        else:
            with handing:
                src.copy(target, overwrite=overwrite)
            src.unlink()

    def move_into(self, target_dir: "Path | str", *, overwrite=False) -> "Path":
        """Move this file or directory into the directory `target_dir`, under
        its own name, and return the new path (pathlib 3.14 parity).

        `move(target_dir / self.name, overwrite=overwrite)`: an existing
        target needs `overwrite=True`. `ValueError` for a path with no name."""
        target = self._into(target_dir)
        self.move(target, overwrite=overwrite)
        return target

    def replace(self, target: "Path | str") -> "Path":
        """Rename this file or directory to `target`, replacing what is
        there, and return `target` as a path (pathlib parity).

        A file replaces a file and a directory replaces an empty directory;
        a directory or a file standing in the way of the other kind raises
        as `os.replace()` does, and a directory that holds anything raises
        `OSError(ENOTEMPTY)` rather than being removed. Built on `move()`, so
        it works between any two paths `move()` accepts; `LocalPath` keeps
        stdlib's `os.replace()`."""
        if isinstance(target, str):
            target = self._coerce_target(target)
        if target.is_dir() and next(iter(target._scandir()), None) is not None:
            raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(target))
        self.move(target, overwrite=True)
        return target


class _Held:
    """The non-following stats `move()` takes of its source and its target,
    each at most once, for `move()` and `_relation()` to share. It lives for
    one call and is never kept on a path."""

    __slots__ = ("source", "source_raw", "_target", "_target_raw", "_taken")

    def __init__(self, src: Path):
        self.source_raw = self.source = None
        self._target = self._target_raw = None
        self._taken = False
        try:
            self.source_raw = src.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        self.source = FileStat.from_stat(self.source_raw)

    def target(self, path: Path):
        """The `FileStat` of `path` without following a link, None when it is
        missing; an `OSError` is raised again by the next call."""
        if not self._taken:
            try:
                self._target_raw = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                self._target_raw = None
            self._target = (
                None
                if self._target_raw is None
                else FileStat.from_stat(self._target_raw)
            )
            self._taken = True
        return self._target

    def same_file(self, src: Path, target: Path) -> bool:
        """`src.samefile(target)` from the stats in hand: for a class that does
        not answer `samefile()` itself and two paths that are not links (a
        link's own stat is not the file `samefile()` compares)."""
        if type(src).samefile is not Identity.samefile or self.source.is_symlink():
            return src.samefile(target)
        there = self.target(target)
        if there is None:
            return False
        if there.is_symlink():
            return src.samefile(target)
        return _same_identity(self.source_raw, self._target_raw)


def _holds_links(src: Path, target: Path) -> bool:
    """Whether a symlink of `src` can be recreated at `target`: `src` reads
    links and the class of `target` implements `_symlink_to()`."""
    return (
        supports(src, Symlink)
        and getattr(type(target), "_symlink_to", None) is not Symlink._symlink_to
    )
